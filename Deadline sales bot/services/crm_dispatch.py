"""High-level CRM event dispatcher (Phase 7+8, 2026-05-26).

Bridges the bot's hot path to the CRM event queue. Each function is a
small composition: read Customer + Conversation state, decide what CRM
events the current turn implies, enqueue them. Returns immediately —
all CRM I/O happens later in the worker.

dispatch_on_message_turn() is the ONE function the hot path calls per
message turn — it handles all the branching internally (new lead vs
returning, log message, handoff transition, etc).

Writeback pattern:
  Worker resolves the real CRM-side ids asynchronously. We pass a
  callback that opens a fresh DB session (via session_scope) and writes
  the id back to Customer.crm_contact_id / Conversation.crm_deal_id.
  Subsequent log_message events for the same conversation will see the
  populated id and flow through; events fired during the gap drop
  silently (acceptable — our Postgres has the truth in messages table).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from services.crm.base import (
    Deal,
    Lead,
    MessageLog,
)
from services.crm_queue import (
    enqueue,
    make_create_deal_event,
    make_create_task_event,
    make_log_message_event,
    make_update_stage_event,
    make_update_temperature_event,
    make_upsert_contact_event,
)


logger = logging.getLogger(__name__)


# =============================================================================
# Test-lead filter (Phase B Step 7, 2026-05-27)
# =============================================================================
# Тестовые лиды для dogfood-прогонов под разными легендами идут с email
# вида `test+<persona>@<domain>` (Gmail-style plus addressing — все приходят
# в один реальный ящик, но бот видит их как разных людей). Чтобы такие
# прогоны НЕ загрязняли продакшен HubSpot, гасим CRM-диспатч в самом
# верху обеих entry-точек ниже. Бот продолжает работать нормально (DB
# заполняется), просто события в CRM не уходят.
#
# Паттерн хардкодим стабильным "test+" — если понадобится сделать
# конфигурируемым через env, обернуть в Settings без изменения вызывающих.

_TEST_EMAIL_PREFIX = "test+"


def _is_test_email(email: Optional[str]) -> bool:
    """True если email подпадает под dogfood-test-конвенцию."""
    return bool(email) and email.lower().startswith(_TEST_EMAIL_PREFIX)


# =============================================================================
# First touch — new lead arrived
# =============================================================================

def dispatch_on_first_touch(
    *,
    customer_id: str,
    customer_name: Optional[str],
    customer_email: Optional[str],
    customer_phone: Optional[str],
    customer_tg_handle: Optional[str],
    conversation_id: str,
    channel: str,
    channel_user_id: str,
    first_message_text: Optional[str],
    interaction_type: str,
    temperature: str,
    score: int,
    initial_stage: str = "new_lead",
    project_type: Optional[str] = None,
    source_url: Optional[str] = None,
) -> None:
    """Enqueue events for a brand-new lead: upsert_contact + create_deal.

    Caller has already created Customer + Conversation rows in our DB.
    We don't have CRM-side ids yet; the worker will fill those in via
    the on_contact_id / on_deal_id callbacks (Phase 8 — until then
    callers can re-fetch by external_id if needed).
    """
    # Test-lead filter (Phase B Step 7) — dogfood-прогоны с test+...@
    # email не попадают в продакшен CRM. См. _is_test_email выше.
    if _is_test_email(customer_email):
        logger.info(
            "[crm_dispatch] skipping first-touch CRM dispatch for test lead %s",
            customer_email,
        )
        return

    identity_keys: dict[str, Any] = {}
    if customer_email:
        identity_keys["email"] = customer_email
    if customer_phone:
        identity_keys["phone"] = customer_phone
    if customer_tg_handle:
        identity_keys["tg_handle"] = customer_tg_handle

    contact_handle = customer_email or customer_tg_handle or customer_phone

    lead = Lead(
        id=str(customer_id),
        contact_name=customer_name,
        contact_handle=contact_handle,
        channel=channel,
        channel_user_id=channel_user_id,
        first_message_at=datetime.now(timezone.utc),
        source_url=source_url,
        interaction_type=interaction_type,
        temperature=temperature,
        score=score,
        identity_keys=identity_keys,
    )
    enqueue(make_upsert_contact_event(customer_id=str(customer_id), lead=lead))

    deal_title = _build_deal_title(customer_name, project_type, channel, first_message_text)
    deal = Deal(
        lead_id=str(customer_id),
        conversation_id=str(conversation_id),
        title=deal_title,
        stage=initial_stage,
        project_type=project_type,
        brief=(first_message_text[:500] if first_message_text else None),
    )
    # NOTE: contact_id is "pending" — the worker will resolve it via the
    # upsert_contact event ahead of this in the queue. For HubSpot we'd
    # ideally serialise this dependency; current impl relies on FIFO order
    # of a single-worker queue (events for the same customer arrive in
    # the order we enqueue them). Multi-worker would need explicit deps.
    enqueue(make_create_deal_event(
        customer_id=str(customer_id),
        deal=deal,
        contact_id="pending",  # worker will substitute
    ))


# =============================================================================
# Per-message events
# =============================================================================

def dispatch_message_log(
    *,
    customer_id: str,
    crm_contact_id: Optional[str],
    conversation_id: str,
    role: str,
    channel: str,
    text: str,
    metadata: Optional[dict] = None,
) -> None:
    """Mirror one message into the CRM contact timeline.

    If crm_contact_id is None (writeback not yet applied), we enqueue with
    contact_id='pending' — the worker lazy-resolves it from DB and retries
    until the upsert_contact event's writeback lands. This way the first
    message of a brand-new lead still ends up in the timeline.
    """
    contact_id = crm_contact_id if crm_contact_id else "pending"
    msg = MessageLog(
        lead_id=str(customer_id),
        conversation_id=str(conversation_id),
        role=role,  # type: ignore[arg-type]
        channel=channel,  # type: ignore[arg-type]
        text=text,
        timestamp=datetime.now(timezone.utc),
        metadata=metadata or {},
    )
    enqueue(make_log_message_event(
        customer_id=str(customer_id), msg=msg, contact_id=contact_id,
    ))


def build_qualified_deal_fields(
    handoff_data: dict, channel: str,
) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Phase C1 (2026-05-29): turn the handoff classifier output into a
    READABLE HubSpot deal title + structured brief for the card.

    title  = имя лида → короткая суть задачи → «{канал} · {тип}»
    desc   = структурный бриф (тип · задача · срок · срочность · контакт)
    Returns (title, description, project_type); any element may be None.
    """
    if not handoff_data:
        return None, None, None
    name = (handoff_data.get("lead_name") or "").strip()
    project_type = (handoff_data.get("project_type") or "").strip() or None
    if project_type in ("Unknown", "unknown"):
        project_type = None
    task = (handoff_data.get("task_summary") or "").strip()
    timeline = (handoff_data.get("timeline") or "").strip()
    urgency = (handoff_data.get("urgency") or "").strip()
    email = (handoff_data.get("lead_email") or "").strip()
    tg = (handoff_data.get("lead_telegram_username") or "").strip()
    phone = (handoff_data.get("lead_phone") or "").strip()
    source = (handoff_data.get("traffic_source") or "").strip()  # Phase C1.2

    channel_short = {
        "telegram": "TG", "instagram": "IG", "messenger": "FB",
        "website": "Web", "whatsapp": "WA", "email": "Email",
    }.get((channel or "").lower(), (channel or "Web").capitalize())

    # --- title: name → task essence → channel·type ---
    if name:
        title = name if not project_type else f"{name} · {project_type}"
    elif task:
        snippet = task.split("\n", 1)[0].strip()
        if len(snippet) > 50:
            snippet = snippet[:50].rsplit(" ", 1)[0] + "…"
        title = (snippet[0].upper() + snippet[1:]) if snippet else None
    elif project_type:
        title = f"{channel_short} · {project_type}"
    else:
        title = None

    # --- structured brief ---
    contact_bits = [b for b in (email, tg, phone) if b]
    lines = [
        f"Тип: {project_type or '—'}",
        f"Задача: {task or '—'}",
        f"Срок: {timeline or '—'}",
        f"Срочность: {urgency or 'Normal'}",
        f"Канал: {channel_short}",
    ]
    if source:
        lines.insert(0, f"Источник: {source}")
    if contact_bits:
        lines.append(f"Контакт: {' · '.join(contact_bits)}")
    description = "\n".join(lines)
    return title, description, project_type


def dispatch_stage_change(
    *,
    customer_id: str,
    crm_deal_id: Optional[str],
    new_stage: str,
    lost_reason: Optional[str] = None,
    conversation_id: Optional[str] = None,
    title: Optional[str] = None,
    description: Optional[str] = None,
    project_type: Optional[str] = None,
    next_meeting_at=None,
) -> None:
    """Push a funnel-stage transition to the CRM deal.

    deal_id='pending' is used when the create_deal event hasn't completed yet —
    common when a handoff fires on the very first message. The worker will
    lazy-resolve it from Conversation.crm_deal_id when create_deal's writeback
    has applied. conversation_id is required for that resolution.

    Phase C1: optional title/description/project_type ride along so the deal
    card gets a readable name + structured brief in the SAME PATCH as the
    stage move (used on handoff→qualified).

    next_meeting_at (datetime): при переходе в «📞 Созвон назначен» — дата/время
    созвона, пишется в карточку сделки (HubSpot next_meeting_at).
    """
    deal_id = crm_deal_id if crm_deal_id else "pending"
    enqueue(make_update_stage_event(
        customer_id=str(customer_id),
        deal_id=deal_id,
        stage=new_stage,  # type: ignore[arg-type]
        lost_reason=lost_reason,  # type: ignore[arg-type]
        conversation_id=conversation_id,
        title=title,
        description=description,
        project_type=project_type,
        next_meeting_at=next_meeting_at,
    ))


def dispatch_temperature_change(
    *,
    customer_id: str,
    crm_contact_id: Optional[str],
    new_temperature: str,
) -> None:
    """Push a temperature change to the CRM contact custom property."""
    contact_id = crm_contact_id if crm_contact_id else "pending"
    enqueue(make_update_temperature_event(
        customer_id=str(customer_id),
        contact_id=contact_id,
        temperature=new_temperature,  # type: ignore[arg-type]
    ))


def dispatch_operator_task(
    *,
    customer_id: str,
    crm_contact_id: Optional[str],
    crm_deal_id: Optional[str],
    title: str,
    category: str = "callback",
    due_in_minutes: int = 15,
    description: Optional[str] = None,
    conversation_id: Optional[str] = None,
    on_task_id=None,
) -> None:
    """Create an operator task in the CRM. Used after handoff, dunning, etc.

    Both contact_id and deal_id may be 'pending' — the worker resolves
    them lazily from DB. conversation_id is needed for deal_id resolution.

    on_task_id: опц. колбэк(task_id) — воркер зовёт его с реальным CRM task_id
    после создания. Используется, чтобы привязать задачу к followup-строке
    (тогда крон закроет её в CRM при исполнении).
    """
    contact_id = crm_contact_id if crm_contact_id else "pending"
    deal_id_for_event: Optional[str] = (
        crm_deal_id if crm_deal_id else ("pending" if conversation_id else None)
    )
    due_at = datetime.now(timezone.utc) + timedelta(minutes=due_in_minutes)
    enqueue(make_create_task_event(
        customer_id=str(customer_id),
        contact_id=contact_id,
        deal_id=deal_id_for_event,
        conversation_id=conversation_id,
        title=title,
        due_at=due_at,
        category=category,  # type: ignore[arg-type]
        description=description,
        on_task_id=on_task_id,
    ))


# =============================================================================
# Helpers
# =============================================================================

_RU_DAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
_RU_MON = ["", "января", "февраля", "марта", "апреля", "мая", "июня",
           "июля", "августа", "сентября", "октября", "ноября", "декабря"]


def _fmt_call_time(dt: datetime) -> str:
    """Человекочитаемое время созвона в UTC+7 (Бангкок) для CRM-задачи."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    loc = dt + timedelta(hours=7)
    return f"{_RU_DAYS[loc.weekday()]}, {loc.day} {_RU_MON[loc.month]}, {loc.hour:02d}:{loc.minute:02d} (Бангкок, UTC+7)"


def _fmt_call_short(dt: datetime) -> str:
    """Короткий формат для заголовка задачи: «чт 04.06 11:00»."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    loc = dt + timedelta(hours=7)
    short = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"][loc.weekday()]
    return f"{short} {loc.day:02d}.{loc.month:02d} {loc.hour:02d}:{loc.minute:02d}"


def _suggest_call_dt(now: datetime) -> datetime:
    """Предложить разумное время связи, если лид НЕ назвал точное. Возвращает UTC.

    Логика (локально UTC+7): до 10:00 → сегодня 11:00; до 15:00 → сегодня 16:00;
    позже → завтра 11:00. Выходные пропускаем на ближайший будний 11:00.
    11:00 и 16:00 — «удобные всем» окна (идея пользователя)."""
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    loc = now + timedelta(hours=7)
    if loc.hour < 10:
        cand = loc.replace(hour=11, minute=0, second=0, microsecond=0)
    elif loc.hour < 15:
        cand = loc.replace(hour=16, minute=0, second=0, microsecond=0)
    else:
        cand = (loc + timedelta(days=1)).replace(hour=11, minute=0, second=0, microsecond=0)
    while cand.weekday() >= 5:  # сб/вс → ближайший будний 11:00
        cand = (cand + timedelta(days=1)).replace(hour=11, minute=0, second=0, microsecond=0)
    return (cand - timedelta(hours=7)).replace(tzinfo=timezone.utc)


def _build_deal_title(
    customer_name: Optional[str],
    project_type: Optional[str],
    channel: str,
    first_message: Optional[str] = None,
) -> str:
    """Build a meaningful deal title for HubSpot.

    Priority (Phase B Step 8, 2026-05-27):
      1. First-message snippet (first line, smart-truncated to ~55 chars
         at word boundary) + short channel code → "Хочу лендинг для кафе · IG"
      2. Legacy fallback when first_message is empty/missing → keeps the old
         "<name> — <project_type> (<channel>)" pattern.

    Heuristic, no LLM call — keeps dispatcher sync and free. Result is still
    миллион раз лучше чем дефолтный «Unknown lead — scope TBD (website)»
    для случаев когда лид сразу написал что хочет.
    """
    channel_short = {
        "telegram": "TG",
        "instagram": "IG",
        "messenger": "FB",
        "website": "Web",
        "whatsapp": "WA",
        "email": "Email",
    }.get((channel or "").lower(), (channel or "").capitalize() or "Web")

    if first_message and first_message.strip():
        snippet = first_message.strip().split("\n", 1)[0].strip()
        if len(snippet) > 55:
            # Trim at word boundary so we don't cut mid-word
            snippet = snippet[:55].rsplit(" ", 1)[0] + "…"
        if snippet:
            snippet = snippet[0].upper() + snippet[1:]
            return f"{snippet} · {channel_short}"

    # Legacy fallback — preserves backward-compat for first_message=None callers
    name_part = customer_name or "Unknown lead"
    pt_part = project_type if project_type else "scope TBD"
    return f"{name_part} — {pt_part} ({channel})"


# =============================================================================
# Hot-path entry point — called once per message turn from _handle_message
# =============================================================================

# Phase 12 tuning (2026-05-28): threshold raised 50 → 80 after smoke #6 showed
# P2 visitors (base score 60 with no keywords) were getting deals on first "hi".
# At 80, P1 (base 100) still triggers deal immediately (intent is explicit from
# ad/direct request — appropriate to track). P2 needs either content keywords
# (e.g. "бюджет" +20 + "дедлайн" +20 = 100, real intent visible) OR engagement
# (lead_messages_count >= 3) OR handoff. Casual visitors no longer pollute pipeline.
DEAL_CREATION_SCORE_THRESHOLD = 80
DEAL_CREATION_LEAD_MESSAGES_THRESHOLD = 3


def dispatch_on_message_turn(
    *,
    customer: Any,          # db.models.Customer — has crm_contact_id field
    conversation: Any,      # db.models.Conversation — has crm_deal_id, lead_stage
    last_lead_message: Optional[str],
    last_bot_reply: Optional[str],
    handoff_just_fired: bool,
    channel: str,
    lead_messages_count: int = 0,
    project_type: Optional[str] = None,
    handoff_data: Optional[dict] = None,
    call_booked_at=None,
) -> None:
    """Process one message turn — enqueue CRM events implied.

    LAZY DEAL CREATION (Phase 12, 2026-05-28):
    Contact is created/updated on every new lead (lightweight identity).
    Deal is created LAZILY — only when there's a real signal that this is
    actually a lead, not just a casual visitor:
      - handoff_just_fired (email was captured → real lead)
      - customer.lead_score >= DEAL_CREATION_SCORE_THRESHOLD (50)
      - lead_messages_count >= DEAL_CREATION_LEAD_MESSAGES_THRESHOLD (3)
    The "or" semantics + idempotency via `deal_id is None` ensures one deal
    per conversation, created as soon as ANY signal fires.

    Why: HubSpot pipeline shouldn't fill with throwaway "hi" deals from
    visitors who never engage. Contacts are fine to track (lightweight
    identity record) but Deals must represent real sales opportunities.

    Branching (determined from Customer/Conversation state):
      1. customer.crm_contact_id is None  → enqueue upsert_contact
      2. conversation.crm_deal_id is None AND any signal → enqueue create_deal
      3. Always: log_message events for lead message + bot reply
      4. handoff_just_fired → update_deal_stage(qualified) + operator task

    Args use Customer + Conversation directly (not unpacked) because the
    hot path already has them in hand and unpacking 10 fields here would
    be noise. We only read attributes, never write to them — writebacks
    happen in worker callbacks via a fresh session_scope.
    """
    try:
        # Test-lead filter (Phase B Step 7) — dogfood email vида
        # test+persona@... не двигают CRM. См. _is_test_email на верху файла.
        if _is_test_email(getattr(customer, "email", None)):
            logger.debug(
                "[crm_dispatch] skipping turn CRM dispatch for test lead %s",
                customer.email,
            )
            return

        customer_id = str(customer.id)
        contact_id = customer.crm_contact_id
        deal_id = conversation.crm_deal_id
        score = int(getattr(customer, "lead_score", 0) or 0)

        # 1. Ensure contact exists. Contact is lightweight identity — fine to
        #    create eagerly on every new lead (so we have someone to attach
        #    log_messages to even before deal-signal threshold is crossed).
        if not contact_id:
            _enqueue_upsert_contact(
                customer=customer,
                channel=channel,
            )
        else:
            # Контакт уже есть, НО имя/email/телефон лид часто даёт ПОЗЖЕ первого
            # хода (когда контакт уже создан «пустым»). Раньше повторного апдейта не
            # было → в HubSpot оставались name=None/email=None. upsert_contact
            # идемпотентен (найдёт по email/handle и обновит). Дедуп флагом
            # synced_contact, чтобы не дёргать HubSpot каждый ход без изменений.
            _nm = (getattr(customer, "name", None) or "").strip()
            _em = (getattr(customer, "email", None) or "").strip()
            _ph = (getattr(customer, "phone", None) or "").strip()
            if _nm or _em or _ph:
                _sig = f"{_nm}|{_em}|{_ph}"
                _prof = getattr(customer, "profile_data", None) or {}
                if _prof.get("synced_contact") != _sig:
                    _enqueue_upsert_contact(customer=customer, channel=channel, known_id=contact_id)
                    try:
                        _np = dict(_prof); _np["synced_contact"] = _sig
                        customer.profile_data = _np
                    except Exception:  # noqa: BLE001
                        pass
                    logger.info(
                        "[crm_dispatch] contact re-upsert (name/email/phone updated) conv=%s",
                        str(conversation.id)[:8],
                    )

        # 2. Lazy deal creation. Only fire when there's a real sales signal
        #    AND the deal doesn't already exist (idempotency).
        should_create_deal = (not deal_id) and (
            handoff_just_fired
            or score >= DEAL_CREATION_SCORE_THRESHOLD
            or lead_messages_count >= DEAL_CREATION_LEAD_MESSAGES_THRESHOLD
            or call_booked_at is not None  # бронь созвона = точно реальный лид
        )
        if should_create_deal:
            _enqueue_create_deal(
                customer=customer,
                conversation=conversation,
                first_message_text=last_lead_message,
                channel=channel,
                project_type=project_type,
            )
            logger.info(
                "[crm_dispatch] deal create triggered for conv=%s "
                "(handoff=%s score=%d msgs=%d)",
                str(conversation.id)[:8], handoff_just_fired, score, lead_messages_count,
            )

        # 2b. Созвон назначен → двигаем сделку в «📞 Созвон назначен» + пишем время
        #     в карточку. Enqueue ПОСЛЕ create_deal (FIFO) — worker резолвит deal_id
        #     из writeback create_deal. Бронь форсит создание сделки (см. выше),
        #     поэтому карточка точно появится, даже если порогов ещё не было.
        if call_booked_at is not None:
            dispatch_stage_change(
                customer_id=customer_id,
                crm_deal_id=deal_id,
                new_stage="on_call",
                conversation_id=str(conversation.id),
                next_meeting_at=call_booked_at,
            )
            # Видимая CRM-задача со ВРЕМЕНЕМ и КАНАЛОМ созвона. next_meeting_at
            # пишется в проперти сделки, но его часто нет на дефолтном лэйауте
            # карточки → оператор «не видит время/канал». Задача — нагляднее всего:
            # видна в таймлайне карточки + это и есть «напоминание по созвону» в CRM.
            _medium = (getattr(customer, "profile_data", None) or {}).get("call_medium")
            _when = _fmt_call_time(call_booked_at)
            _nm = (customer.name or customer.email
                   or (customer.identity_keys or {}).get("tg_handle") or "лид")
            _ch_txt = f", канал: {_medium}" if _medium else ""
            _due_min = max(1, int((call_booked_at - datetime.now(timezone.utc)).total_seconds() / 60))
            dispatch_operator_task(
                customer_id=customer_id,
                crm_contact_id=contact_id,
                crm_deal_id=deal_id,
                conversation_id=str(conversation.id),
                title=f"Созвон с {_nm}: {_when}{_ch_txt}",
                category="callback",
                due_in_minutes=_due_min,
                description=f"Созвон назначен: {_when}. Канал: {_medium or 'уточнить у лида'}.",
                # Запоминаем id задачи: если канал назовут СЛЕДУЮЩИМ сообщением —
                # дополним эту же задачу (dispatch_update_call_task), не плодя новую.
                on_task_id=_make_call_task_writeback(customer_id),
            )
            logger.info(
                "[crm_dispatch] call booked → stage on_call + task conv=%s at=%s medium=%s",
                str(conversation.id)[:8], call_booked_at, _medium,
            )

        # 3. Log lead message
        if last_lead_message:
            dispatch_message_log(
                customer_id=customer_id,
                crm_contact_id=contact_id,
                conversation_id=str(conversation.id),
                role="lead",
                channel=channel,
                text=last_lead_message,
            )

        # Log bot reply
        if last_bot_reply:
            dispatch_message_log(
                customer_id=customer_id,
                crm_contact_id=contact_id,
                conversation_id=str(conversation.id),
                role="bot",
                channel=channel,
                text=last_bot_reply,
            )

        # Handoff just fired → move deal to qualified + create operator task
        if handoff_just_fired:
            # Phase C1: enrich the card with a readable title + structured brief
            # built from the handoff classifier output (task_summary etc.), set
            # in the SAME PATCH as the stage move.
            _title, _desc, _ptype = build_qualified_deal_fields(
                handoff_data or {}, channel,
            )
            dispatch_stage_change(
                customer_id=customer_id,
                crm_deal_id=deal_id,
                new_stage="qualified",
                conversation_id=str(conversation.id),
                title=_title,
                description=_desc,
                project_type=_ptype,
            )
            # Task Engine Фаза A: задача создаётся на КАЖДОГО квалифицированного
            # лида (включая сайт) — «никто не теряется». Формулировка зависит от
            # канала: в мессенджере лид уже перешёл и ждёт (срочно подхватить),
            # на сайте — обычное «связаться». Задача вешается на менеджера
            # (HUBSPOT_OWNER_ID, если задан в env). C1.2-смысл сохранён: на сайте
            # это CRM-задача (to-do в карточке), а не реал-тайм пинг оператору.
            _name = (customer.name or customer.email
                     or (customer.identity_keys or {}).get("tg_handle") or "новый лид")
            _task_desc = (_desc or last_lead_message or "")[:1000]
            if (channel or "").lower() != "website":
                # Мессенджер — лид УЖЕ перешёл и ждёт прямо сейчас → срочно (15 мин).
                _task_title = f"Подхватить в Telegram — {_name} ждёт"
                _task_due_min = 15
            else:
                # Сайт — связи нет в реал-тайме. Лид точное время НЕ назвал →
                # ставим задачу на разумное окно (11:00/16:00, удобно всем), чтобы у
                # менеджера была задача С ДАТОЙ И ВРЕМЕНЕМ, а не просто «связаться».
                _sug = _suggest_call_dt(datetime.now(timezone.utc))
                _task_title = f"Связаться с лидом — {_name} · предв. {_fmt_call_short(_sug)}"
                _task_due_min = max(1, int((_sug - datetime.now(timezone.utc)).total_seconds() / 60))
                _task_desc = (
                    f"Предлагаемое время связи: {_fmt_call_time(_sug)} (лид точное не "
                    f"назвал — дефолтное удобное окно).\n\n" + _task_desc
                )
            dispatch_operator_task(
                customer_id=customer_id,
                crm_contact_id=contact_id,
                crm_deal_id=deal_id,
                conversation_id=str(conversation.id),
                title=_task_title,
                category="callback",
                due_in_minutes=_task_due_min,
                description=_task_desc,
            )
            logger.info(
                "[crm_dispatch] handoff conv=%s channel=%s — deal qualified + "
                "callback task created (owner per HUBSPOT_OWNER_ID)",
                str(conversation.id)[:8], channel,
            )

        # Task Engine: лид просит связаться позже («через неделю / в субботу /
        # завтра») → задача-напоминание + (в мессенджере) бот сам напишет в срок.
        # ВЗАИМОСВЯЗЬ С CRM (Вариант 1, 2026-06-02):
        #  • Мессенджер: единый путь — событие schedule_followup с task_*-полями.
        #    Воркер пишет ОДНУ followup-строку (дедуп latest-wins) и создаёт
        #    зеркальную CRM-задачу ТОЛЬКО для нового followup, привязывая её к
        #    строке. Крон при исполнении followup закроет задачу в CRM
        #    (complete_task). Повтор просьбы → строка переносится, задача не дублится.
        #  • Сайт (нет chat_id): бот сам не пишет → прямая задача оператору, как было
        #    (закрывает её человек; авто-закрытия нет).
        if last_lead_message:
            try:
                from services.followup_parse import parse_followup_when
                _fu = parse_followup_when(last_lead_message)
                if _fu is not None:
                    _nm = (customer.name or customer.email
                           or (customer.identity_keys or {}).get("tg_handle") or "новый лид")
                    _title = f"Написать лиду {_nm} — просил связаться позже"
                    _desc = (last_lead_message or "")[:500]
                    _chat_id = getattr(conversation, "channel_conversation_id", None)
                    _is_msgr = (channel or "").lower() in (
                        "telegram", "whatsapp", "instagram", "messenger"
                    )
                    if _chat_id and _is_msgr:
                        # Мессенджер: followup-строка ведёт всё; задачу создаёт/дедупит
                        # воркер по task_*-полям и линкует к строке.
                        from services.crm_queue import make_schedule_followup_event
                        enqueue(make_schedule_followup_event(
                            customer_id=customer_id,
                            conversation_id=str(conversation.id),
                            channel=channel,
                            chat_id=str(_chat_id),
                            due_at=_fu,
                            text=None,  # дефолтный тёплый текст в scheduled_actions
                            task_title=_title,
                            task_description=_desc,
                            task_contact_id=contact_id,
                            task_deal_id=deal_id if deal_id else None,
                        ))
                        logger.info(
                            "[crm_dispatch] bot self-followup scheduled conv=%s chat=%s (CRM task linked)",
                            str(conversation.id)[:8], _chat_id,
                        )
                    else:
                        # Сайт / нет канала — задача оператору (человек напишет сам).
                        _mins = max(
                            1, int((_fu - datetime.now(timezone.utc)).total_seconds() / 60)
                        )
                        dispatch_operator_task(
                            customer_id=customer_id,
                            crm_contact_id=contact_id,
                            crm_deal_id=deal_id if deal_id else None,
                            conversation_id=str(conversation.id),
                            title=_title,
                            category="callback",
                            due_in_minutes=_mins,
                            description=_desc,
                        )
                        logger.info(
                            "[crm_dispatch] operator callback task conv=%s due_in_min=%d (website)",
                            str(conversation.id)[:8], _mins,
                        )
            except Exception as _fe:  # noqa: BLE001
                logger.debug("[crm_dispatch] followup detect skipped: %s", _fe)

    except Exception as exc:  # noqa: BLE001
        logger.warning("[crm_dispatch] dispatch_on_message_turn failed: %s", exc)


def _build_lead_from_customer(customer: Any, channel: str) -> Lead:
    """Map our Customer ORM row to the CRMAdapter Lead value object.

    Extracted from _enqueue_upsert_contact so the same mapping is used
    consistently (and tested separately if needed).
    """
    identity_keys: dict[str, Any] = {}
    if customer.email:
        identity_keys["email"] = customer.email
    if customer.phone:
        identity_keys["phone"] = customer.phone

    # Pull tg_handle from channel_identities if present
    tg_handle = None
    for ident in (customer.identities or []):
        if ident.channel == "telegram" and ident.username:
            tg_handle = ident.username
            identity_keys["tg_handle"] = ident.username
            break

    contact_handle = customer.email or tg_handle or customer.phone

    # Find external_id for this channel
    channel_user_id = ""
    for ident in (customer.identities or []):
        if ident.channel == channel:
            channel_user_id = ident.external_id
            break

    return Lead(
        id=str(customer.id),
        contact_name=customer.name,
        contact_handle=contact_handle,
        channel=channel,  # type: ignore[arg-type]
        channel_user_id=channel_user_id,
        first_message_at=datetime.now(timezone.utc),
        source_url=None,
        interaction_type=getattr(customer, "interaction_type", "P2") or "P2",
        temperature=getattr(customer, "lead_temperature", "cold") or "cold",
        score=getattr(customer, "lead_score", 0) or 0,
        identity_keys=identity_keys,
    )


def _enqueue_upsert_contact(
    *,
    customer: Any,
    channel: str,
    known_id: Optional[str] = None,
) -> None:
    """Phase 12 (2026-05-28): enqueue ONLY upsert_contact, not deal.

    Called when we have a new lead but haven't seen a real sales signal
    yet (lazy deal creation). The contact captures identity + signals
    (interaction_type, score, temperature); the deal will follow when
    handoff fires or engagement thresholds are crossed.
    """
    customer_id = str(customer.id)
    lead = _build_lead_from_customer(customer, channel)

    from services.crm_queue import enqueue, make_upsert_contact_event
    enqueue(make_upsert_contact_event(
        customer_id=customer_id,
        lead=lead,
        on_contact_id=_make_contact_id_writeback(customer_id),
        known_id=known_id,
    ))


def _enqueue_create_deal(
    *,
    customer: Any,
    conversation: Any,
    first_message_text: Optional[str],
    channel: str,
    project_type: Optional[str],
) -> None:
    """Phase 12 (2026-05-28): enqueue ONLY create_deal.

    Called when a real sales signal fires (handoff / score / engagement)
    AND conversation doesn't already have a deal. Contact may or may not
    be CRM-synced yet — if not, contact_id='pending' triggers lazy
    resolution in the worker (services/crm_queue._resolve_pending_contact_id).
    """
    customer_id = str(customer.id)
    conversation_id = str(conversation.id)
    contact_id = customer.crm_contact_id or "pending"

    deal = Deal(
        lead_id=customer_id,
        conversation_id=conversation_id,
        title=_build_deal_title(customer.name, project_type, channel, first_message_text),
        stage=getattr(conversation, "lead_stage", "new_lead") or "new_lead",
        project_type=project_type,
        brief=(first_message_text[:500] if first_message_text else None),
    )

    from services.crm_queue import enqueue, make_create_deal_event
    enqueue(make_create_deal_event(
        customer_id=customer_id,
        deal=deal,
        contact_id=contact_id,
        on_deal_id=_make_deal_id_writeback(conversation_id),
    ))


# =============================================================================
# DB writeback callbacks — worker calls these once it has the real CRM ids
# =============================================================================

def _make_call_task_writeback(customer_id: str):
    """Factory: колбэк, сохраняющий id задачи созвона в customer.profile_data —
    чтобы потом (когда лид назовёт канал СЛЕДУЮЩИМ сообщением) дополнить эту же
    задачу через update_task, а не плодить новую."""
    def writeback(task_id: str) -> None:
        if not task_id:
            return
        try:
            from db.connection import session_scope
            from db.models import Customer
            from uuid import UUID
            with session_scope() as s:
                cust = s.query(Customer).filter(Customer.id == UUID(customer_id)).first()
                if cust:
                    _p = dict(cust.profile_data or {})
                    _p["call_task_id"] = task_id
                    cust.profile_data = _p
            logger.info("[crm_dispatch] call_task_id <- %s (customer %s)", task_id, customer_id[:8])
        except Exception as exc:  # noqa: BLE001
            logger.warning("[crm_dispatch] call task writeback skipped: %s", exc)
    return writeback


def dispatch_update_call_task(*, customer_id: str, task_id: str, lead_name: str,
                              call_at: datetime, medium: str) -> None:
    """Дополнить задачу созвона каналом, названным позже. Enqueue update_task."""
    from services.crm_queue import enqueue, make_update_task_event
    _when = _fmt_call_time(call_at)
    enqueue(make_update_task_event(
        customer_id=str(customer_id),
        task_id=str(task_id),
        subject=f"Созвон с {lead_name}: {_when}, канал: {medium}",
        body=f"Созвон назначен: {_when}. Канал: {medium}.",
    ))


def _make_contact_id_writeback(customer_id: str):
    """Factory: returns a callback that writes contact_id to Customer.crm_contact_id."""
    def writeback(contact_id: str) -> None:
        try:
            from db.connection import session_scope
            from db.models import Customer
            from uuid import UUID
            with session_scope() as s:
                cust = s.query(Customer).filter(Customer.id == UUID(customer_id)).first()
                if cust and not cust.crm_contact_id:
                    cust.crm_contact_id = contact_id
                    logger.info(
                        "[crm_dispatch] customer %s crm_contact_id <- %s",
                        customer_id, contact_id,
                    )
        except Exception as exc:  # noqa: BLE001
            # ERROR (не WARNING): провал writeback = contact_id остаётся None,
            # следующие события с 'pending' будут зря ретраиться часами. Это
            # должно всплывать в алертах, а не тонуть в потоке предупреждений.
            logger.error(
                "[crm_dispatch] CONTACT_ID WRITEBACK FAILED for %s: %s — "
                "pending-события не зарезолвятся пока не пройдёт следующий upsert_contact",
                customer_id, exc,
            )
    return writeback


def _make_deal_id_writeback(conversation_id: str):
    """Factory: returns a callback that writes deal_id to Conversation.crm_deal_id."""
    def writeback(deal_id: str) -> None:
        try:
            from db.connection import session_scope
            from db.models import Conversation
            from uuid import UUID
            with session_scope() as s:
                conv = s.query(Conversation).filter(Conversation.id == UUID(conversation_id)).first()
                if conv and not conv.crm_deal_id:
                    conv.crm_deal_id = deal_id
                    logger.info(
                        "[crm_dispatch] conversation %s crm_deal_id <- %s",
                        conversation_id, deal_id,
                    )
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "[crm_dispatch] DEAL_ID WRITEBACK FAILED for %s: %s — "
                "сделка создана, но conv.crm_deal_id не записан (риск дубля при ретрае)",
                conversation_id, exc,
            )
    return writeback


def _make_followup_task_writeback(conversation_id: str):
    """Factory: колбэк, привязывающий CRM task_id к pending bot-followup строке
    диалога. После этого крон (run_due_followups) при исполнении followup закроет
    задачу в CRM через complete_task. Дедуп гарантирует ОДНУ pending-строку, так
    что обновляем все pending bot-followup'ы диалога (по факту — одну)."""
    def writeback(task_id: str) -> None:
        if not task_id:
            return
        try:
            from db.connection import session_scope
            from db.models import ScheduledAction
            from uuid import UUID
            with session_scope() as s:
                (s.query(ScheduledAction)
                 .filter(ScheduledAction.conversation_id == UUID(conversation_id),
                         ScheduledAction.action_type == "followup_message",
                         ScheduledAction.status == "pending",
                         ScheduledAction.executor == "bot")
                 .update({"crm_task_id": task_id}, synchronize_session=False))
            logger.info("[crm_dispatch] followup(conv=%s) crm_task_id <- %s",
                        conversation_id[:8], task_id)
        except Exception as exc:  # noqa: BLE001
            # Некритично: задача просто не авто-закроется (оператор закроет руками).
            logger.warning("[crm_dispatch] followup task writeback skipped conv=%s: %s",
                           conversation_id, exc)
    return writeback
