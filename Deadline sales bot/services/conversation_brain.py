"""Умное авто-ведение WhatsApp-диалога.

По каждой новой реплике — лида ИЛИ ручного ответа оператора с телефона — бот
сам, без участия человека:
  1. двигает стадию воронки ВПЕРЁД (никогда назад),
  2. заносит созвон в календарь, когда время реально согласовано (в т.ч. из
     фразы оператора «договорились на среду в 15»),
  3. сигналит владельцу в Telegram, когда лид явно просит человека/созвон.

Один LLM-вызов (Gemini) возвращает структурное решение по переписке. Всё
best-effort: любая ошибка логируется, диалог не ломается. Бронь/напоминания
переиспользуют те же примитивы, что ручная бронь из карточки
(services.scheduled_actions + services.scheduling) — единый формат.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy.orm import Session

from db.models import Conversation, Customer, Message, StageTransition
from services import funnel_store, scheduling as _sched
from services.scheduled_actions import (
    cancel_call_actions, write_call_booking, write_call_reminder,
)

log = logging.getLogger(__name__)

# Прямой порядок встроенных стадий — двигаем только вперёд.
_FORWARD = ["new_lead", "in_dialog", "qualified", "on_call",
            "proposal", "prepayment", "completed_won"]

_WEEKDAY_RU = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
_WD_IDX = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


def _resolve_call_dt(now_lead: datetime, call_day: Any, call_time: Any) -> Optional[datetime]:
    """ДЕТЕРМИНИРОВАННО посчитать дату/время созвона из НАЗВАНИЯ дня (LLM путает
    арифметику дат: «среда» → 18 вместо 17). LLM возвращает только КАКОЙ день назвали
    (today/tomorrow/mon..sun) + время; точную дату считаем здесь от «сегодня» лида.

    now_lead — текущее время в поясе ЛИДА (aware). Возвращает aware UTC или None."""
    if not call_day:
        return None
    cd = str(call_day).strip().lower()[:3]
    if cd in ("tod", "сег"):
        target = now_lead
    elif cd in ("tom", "зав"):
        target = now_lead + timedelta(days=1)
    elif cd in _WD_IDX:
        delta = (_WD_IDX[cd] - now_lead.weekday()) % 7  # ближайшее вхождение (0=сегодня)
        target = now_lead + timedelta(days=delta)
    else:
        return None
    hh, mm = 10, 0  # дефолт — утро
    t = str(call_time or "").strip().lower()
    m = re.match(r"(\d{1,2})[:.\s](\d{2})", t)
    if m:
        hh, mm = int(m.group(1)), int(m.group(2))
    elif t in ("morning", "утро", "утром"):
        hh = 10
    elif t in ("day", "afternoon", "день", "днём", "днем", "обед"):
        hh = 14
    elif t in ("evening", "вечер", "вечером"):
        hh = 18
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        hh, mm = 10, 0
    dt_lead = target.replace(hour=hh, minute=mm, second=0, microsecond=0)  # сохраняет пояс лида
    return dt_lead.astimezone(timezone.utc)


def _transcript(db: Session, conv: Conversation, limit: int = 14) -> str:
    rows = (
        db.query(Message)
        .filter(Message.conversation_id == conv.id)
        .order_by(Message.created_at.desc())
        .limit(limit).all()
    )
    out = []
    for m in reversed(rows):
        if m.role == "user":
            who = "Лид"
        elif m.role in ("assistant", "operator"):
            who = "Мы"
        else:
            continue
        out.append(f"{who}: {(m.content or '')[:300]}")
    return "\n".join(out)


def _dialog_count(db: Session, conv: Conversation) -> int:
    """Число реплик лида/нас/оператора — мера новизны для периодического sweep."""
    return (
        db.query(Message)
        .filter(Message.conversation_id == conv.id)
        .filter(Message.role.in_(("user", "assistant", "operator")))
        .count()
    )


def _lead_silent(db: Session, conv: Conversation) -> bool:
    """Лид молчит = ПОСЛЕДНЯЯ реплика НЕ от лида (мы написали последними и ждём).
    Используется, чтобы НЕ создавать «договорённость о созвоне» в одностороннем
    порядке: молчание ≠ согласие (кейс: клиент увидел КП и пропал)."""
    m = (
        db.query(Message)
        .filter(Message.conversation_id == conv.id,
                Message.role.in_(("user", "assistant", "operator")))
        .order_by(Message.created_at.desc())
        .first()
    )
    return bool(m) and m.role in ("assistant", "operator")


def _parse_json(raw: str) -> Optional[dict]:
    if not raw:
        return None
    s = raw.strip()
    # срезаем ```json ... ``` если модель обернула
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s).strip()
    m = re.search(r"\{.*\}", s, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except (ValueError, TypeError):
        return None


def _stage_forward(current: str, suggested: str) -> Optional[str]:
    """Вернуть новую стадию, только если suggested СТРОГО впереди current."""
    if suggested not in _FORWARD or current not in _FORWARD:
        return None
    if _FORWARD.index(suggested) > _FORWARD.index(current):
        return suggested
    return None


async def _signal_owner(settings: Any, text: str) -> None:
    from services import bot_settings as _bs
    from channels.telegram import send_telegram_reply
    chat = (_bs.get("manager_chat_id") or "").strip() or (settings.telegram_chat_id or "")
    token = settings.telegram_bot_token
    if not (chat and token):
        return
    try:
        await send_telegram_reply(token, str(chat), text[:3500])
    except Exception as e:  # noqa: BLE001
        log.warning(f"brain owner-signal failed: {e}")


async def _book(db: Session, conv: Conversation, cust: Customer,
                settings: Any, new_dt: datetime, medium: Optional[str]) -> None:
    """Бронь созвона = ровно то же, что ручной перенос из карточки."""
    import asyncio
    prof = dict(cust.profile_data or {})
    await asyncio.to_thread(cancel_call_actions, str(conv.id))
    prof["booked_call_at"] = new_dt.isoformat()
    if medium:
        prof["call_medium"] = medium
    prof.pop("brain_signaled", None)  # бронь снимает «просил человека»
    cust.profile_data = prof
    _from = conv.lead_stage
    conv.lead_stage = "on_call"
    if _from != "on_call":
        db.add(StageTransition(
            conversation_id=conv.id, customer_id=conv.customer_id,
            from_stage=_from, to_stage="on_call", by="bot-brain",
        ))
    db.commit()

    now = datetime.now(timezone.utc)
    chat = conv.channel_conversation_id
    lead_name = cust.name or cust.email or "лид"
    contact = cust.email or ""
    lang = prof.get("lang") or "ru"
    await asyncio.to_thread(
        write_call_booking,
        customer_id=str(cust.id), conversation_id=str(conv.id),
        channel=conv.channel, chat_id=str(chat) if chat else None,
        call_at=new_dt, medium=medium,
    )
    for fire, label in _sched.reminder_schedule(new_dt, now):
        if chat:
            await asyncio.to_thread(
                write_call_reminder,
                customer_id=str(cust.id), conversation_id=str(conv.id),
                channel=conv.channel, chat_id=str(chat), due_at=fire,
                text=_sched.lead_reminder_text(new_dt, label, medium, lang=lang,
                                               phone=str(chat)),
                audience="lead",
            )
        if settings.telegram_operator_group_id:
            await asyncio.to_thread(
                write_call_reminder,
                customer_id=str(cust.id), conversation_id=str(conv.id),
                channel=conv.channel,
                chat_id=str(settings.telegram_operator_group_id), due_at=fire,
                text=_sched.admin_reminder_text(new_dt, lead_name, label, medium, contact),
                audience="admin",
            )
    # СРАЗУ уведомить владельца/менеджера: «поймал договорённость и поставил в
    # календарь» (просьба владельца — видеть, что бот распознал ручную договорённость).
    try:
        when_lead = _sched.format_slot_human(new_dt, tz=_sched.lead_tz_from_phone(str(chat or "")))
        tzlbl = _sched.tz_label_from_phone(str(chat or ""))
        await _signal_owner(
            settings,
            f"📅 Поставил созвон в календарь из переписки:\n"
            f"Лид: {lead_name} ({chat})\n"
            f"Когда: {when_lead} ({tzlbl}){(' · ' + medium) if medium else ''}\n"
            f"Стадия → 📞 Созвон назначен. Если время не то — поправьте в карточке.",
        )
    except Exception as e:  # noqa: BLE001
        log.warning(f"[{str(conv.id)[:8]}] book notify failed: {e}")


def _multi_tz_enabled() -> bool:
    """Учитывать ли часовой пояс ЛИДА (мультипояс). Выкл в настройках (tz_multi=false)
    → всё в поясе админа (Пхукет) — для тех, кто работает в одном городе. Деф. ВКЛ."""
    try:
        from services import bot_settings as _bs
        v = _bs.get("tz_multi")
        return True if v is None else bool(v)
    except Exception:  # noqa: BLE001
        return True


async def analyze_and_advance(db: Session, conv: Conversation, cust: Customer,
                              llm: Any, settings: Any, refresh_draft: bool = True) -> dict:
    """Проанализировать диалог и применить решения. Возвращает что сделано.
    refresh_draft=False — НЕ перегенерировать черновик (его LLM+KB-эмбед тяжёлые;
    в bulk-cron-sweep отключаем, чтобы не исчерпать пул; черновик освежается на
    вебхуке per-message)."""
    done: dict = {"stage": None, "booked": None, "suggested": None, "signaled": False}
    if (conv.lead_stage or "new_lead") in ("lost", "completed_won"):
        return done
    transcript = _transcript(db, conv)
    if not transcript.strip():
        return done

    # Пояс ЛИДА — по его РЕАЛЬНОМУ телефону (cust.phone), а не channel_conversation_id:
    # у рекламных лидов там скрытый @lid (не телефон) → пояс падал в Пхукет даже для
    # Астаны. Если мультипояс выключен в настройках — все в поясе админа (Бангкок).
    _phone_for_tz = (getattr(cust, "phone", None) or conv.channel_conversation_id or "")
    _multi = _multi_tz_enabled()
    tz = _sched.lead_tz_from_phone(_phone_for_tz) if _multi else _sched.BANGKOK
    tz_label = _sched.tz_label_from_phone(_phone_for_tz) if _multi else "время Пхукета"
    now_utc = datetime.now(timezone.utc)
    now_lead = now_utc.astimezone(tz)
    prompt = (
        "Проанализируй переписку веб-студии с лидом и верни СТРОГО JSON (без пояснений). "
        "Поля:\n"
        '  "stage": одна из new_lead|in_dialog|qualified|on_call|proposal|prepayment|completed_won '
        "— текущая стадия по смыслу (in_dialog=идёт разговор, но проект ещё не описан; "
        "qualified=ТОЛЬКО если лид описал КОНКРЕТНУЮ задачу/проект — что именно нужно сделать "
        "(сайт/магазин/бот/Mini App/AI и зачем); просто «привет/расскажите подробнее» — это "
        "in_dialog, НЕ qualified; on_call=договорились о созвоне; proposal=обсуждается КП/цена; "
        "prepayment=готов платить);\n"
        '  "call_agreed": true ТОЛЬКО если в переписке есть ЯВНАЯ ВЗАИМНАЯ '
        "договорённость СОЗВОНИТЬСЯ/ПОЗВОНИТЬ (именно звонок/созвон, голосом) на "
        "КОНКРЕТНЫЙ ДЕНЬ — обе стороны подтвердили. ЯВНО НЕ считается договорённостью "
        "о созвоне (тут false): «отправлю/скину информацию/материалы», «напишу/отвечу "
        "позже», «в течение дня пришлю», «подумаю», «жду подробности», «посмотрю», "
        "обмен ссылками/файлами, вопросы по проекту, общая заинтересованность. Если про "
        "ЗВОНОК явно не договорились с конкретным днём — false. Сомневаешься — false;\n"
        '  "call_day": НАЗВАНИЕ дня договорённости как его произнесли — одно из '
        '"today"|"tomorrow"|"mon"|"tue"|"wed"|"thu"|"fri"|"sat"|"sun", иначе null. '
        "НЕ вычисляй дату сам — только верни, какой день назвали («в среду»→wed, "
        "«завтра»→tomorrow, «сегодня»→today). Дату посчитает система;\n"
        '  "call_time": ТОЧНЫЙ час, если лид/менеджер его назвал — строго "HH:MM" по '
        'времени ЛИДА (его городу). «в 18:00», «после 18», «к 18», «в 6 вечера» → "18:00"; '
        '«в 14», «в 2 дня» → "14:00". Если назван только период — "morning"|"day"|"evening". '
        'Время лид указывает в СВОЁМ часовом поясе — не пересчитывай сам, верни как сказал;\n'
        '  "call_datetime_utc": ISO8601 в UTC (запасной вариант, если call_day не подходит). '
        "Если час не назван — утро→10:00, день→14:00, вечер→18:00 ПО ВРЕМЕНИ ЛИДА, иначе null. "
        f"Сейчас у лида {now_lead.strftime('%Y-%m-%d %H:%M')} ({tz_label}), {_WEEKDAY_RU[now_lead.weekday()]};\n"
        '  "call_medium": "WhatsApp"|"Телефон"|"Zoom"|"Google Meet"|null;\n'
        '  "wants_human": true если лид ЯВНО просит позвонить/связаться с человеком/менеджером;\n'
        '  "reason": кратко почему (≤120 симв).\n\n'
        f"Переписка:\n{transcript}"
    )
    try:
        result = await llm.ainvoke(prompt)
        data = _parse_json(getattr(result, "content", None) or "")
    except Exception as e:  # noqa: BLE001
        log.warning(f"[{str(conv.id)[:8]}] brain LLM failed: {e}")
        return done
    if not data:
        return done

    # 1) стадия вперёд
    new_stage = _stage_forward(conv.lead_stage or "new_lead", str(data.get("stage") or ""))
    if new_stage and new_stage != "on_call":  # on_call ставит бронь ниже
        from_stage = conv.lead_stage
        conv.lead_stage = new_stage
        db.add(StageTransition(
            conversation_id=conv.id, customer_id=conv.customer_id,
            from_stage=from_stage, to_stage=new_stage, by="bot-brain",
        ))
        db.commit()
        done["stage"] = new_stage
        if settings.crm_enabled and new_stage in funnel_store.BUILTIN_KEYS:
            try:
                from services.crm_dispatch import dispatch_stage_change
                dispatch_stage_change(
                    customer_id=str(conv.customer_id), crm_deal_id=conv.crm_deal_id,
                    new_stage=new_stage, lost_reason=None, conversation_id=str(conv.id),
                )
            except Exception as e:  # noqa: BLE001
                log.warning(f"[{str(conv.id)[:8]}] brain CRM mirror failed: {e}")

    # 2) ПРЕДЛОЖЕНИЕ созвона (НЕ авто-бронь): распознали договорённость → кладём
    #    в conv.pending_call_suggestion → менеджер подтверждает в карточке → событие.
    # Дату считаем ДЕТЕРМИНИРОВАННО из названия дня (LLM врёт: «среда»→18 вместо 17);
    # call_datetime_utc — только запасной вариант, если день не распознан.
    resolved_dt = _resolve_call_dt(now_lead, data.get("call_day"), data.get("call_time"))
    # СТОП ФАНТОМ-СОЗВОНАМ: если лид молчит (мы написали последними), договорённости
    # по факту нет — не создаём призрачный созвон (кейс «увидел КП и пропал»). Такой
    # лид попадёт в дожим через next_action, а не в календарь.
    # Предлагаем созвон ТОЛЬКО при: явной договорённости (call_agreed) + НАЗВАННОМ
    # дне (call_day) + лид НЕ молчит. Без названного дня = бот «угадал» время →
    # ложное срабатывание (кейс Вячеслав: «отправлю инфо в течение дня» ≠ созвон).
    if (data.get("call_agreed") and data.get("call_day") and resolved_dt
            and not _lead_silent(db, conv)):
        try:
            new_dt = resolved_dt
            prof = cust.profile_data or {}
            # не дублируем: уже забронировано ~то же время, или уже есть такое же
            # предложение, или предложение по тому же времени недавно отклоняли.
            def _close(a_iso) -> bool:
                try:
                    a = datetime.fromisoformat(str(a_iso).replace("Z", "+00:00"))
                    if a.tzinfo is None:
                        a = a.replace(tzinfo=timezone.utc)
                    return abs((a - new_dt).total_seconds()) < 600
                except (ValueError, TypeError):
                    return False
            existing_sugg = getattr(conv, "pending_call_suggestion", None) or {}
            dup = _close(prof.get("booked_call_at")) or _close(existing_sugg.get("at")) \
                or _close(prof.get("call_suggest_dismissed_at_val"))
            if new_dt > now_utc and not dup:
                # ВЕРНЫЙ пояс — из cust.phone + мультипояс (см. выше), НЕ из @lid
                tzlbl = tz_label
                when_h = _sched.format_slot_human(new_dt, tz=tz)
                conv.pending_call_suggestion = {
                    "at": new_dt.isoformat(),
                    "when_human": f"{when_h} ({tzlbl})",
                    "medium": data.get("call_medium"),
                    "reason": str(data.get("reason") or "")[:200],
                    "ts": now_utc.isoformat(),
                }
                db.commit()
                done["suggested"] = new_dt.isoformat()
                name = cust.name or conv.channel_conversation_id or "лид"
                await _signal_owner(
                    settings,
                    f"📅 Похоже, договорились о созвоне:\nЛид: {name}\n"
                    f"Когда: {when_h} ({tzlbl})\n"
                    f"Откройте карточку в панели → «Создать событие», если верно.",
                )
        except (ValueError, TypeError) as e:
            log.warning(f"[{str(conv.id)[:8]}] brain bad call_datetime: {e}")
    # Бот пересмотрел и созвона НЕТ → снимаем СВОЁ ложное/устаревшее предложение
    # (кейс Вячеслав: «отправлю инфо» ≠ созвон). Подтверждённые брони не трогаем
    # (они в profile_data.booked_call_at, а не в pending_call_suggestion).
    elif data.get("call_agreed") is False and getattr(conv, "pending_call_suggestion", None):
        conv.pending_call_suggestion = None
        db.commit()
        done["cleared_suggestion"] = True

    # 3) сигнал владельцу (один раз на эпизод «просит человека», пока нет брони)
    if data.get("wants_human") and not done.get("booked") and not done.get("suggested"):
        prof = dict(cust.profile_data or {})
        if not prof.get("brain_signaled"):
            name = cust.name or conv.channel_conversation_id or "лид"
            await _signal_owner(
                settings,
                f"🔔 Лид {name} просит связаться/созвон.\n"
                f"Причина: {str(data.get('reason') or '')[:200]}\n"
                f"Открыть карточку в панели → одобрить ответ / поставить время.",
            )
            prof["brain_signaled"] = now_utc.isoformat()
            cust.profile_data = prof
            db.commit()
            done["signaled"] = True

    # Держим предложенный ответ свежим — генерим В ФОНЕ (не в GET-пути карточки!),
    # если черновика нет или он устарел. Так панель всегда показывает актуальный
    # ответ, а conversation_detail остаётся без LLM (не вешает пул).
    try:
        if refresh_draft and not bool(getattr(conv, "wa_autonomous", False)):
            from services import wa_drafts
            if not conv.pending_wa_draft or wa_drafts.is_stale(db, conv):
                payload = await wa_drafts.generate_for_conv(
                    db, conv, cust, llm, source="brain_refresh",
                )
                if payload:
                    db.commit()
                    done["draft"] = True
    except Exception as e:  # noqa: BLE001
        db.rollback()
        log.warning(f"[{str(conv.id)[:8]}] brain draft refresh failed: {e}")

    # Запомнить «проанализировано до этого числа реплик» — периодический sweep
    # пропускает диалоги без новых сообщений (не жжёт LLM зря).
    try:
        prof = dict(cust.profile_data or {})
        prof["brain_last_count"] = _dialog_count(db, conv)
        cust.profile_data = prof
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
    return done


async def sweep_recent(llm: Any, settings: Any, since_minutes: int = 360,
                       limit: int = 10, force: bool = False) -> dict:
    """Периодическая проверка актуальности: пройтись по недавно активным
    WhatsApp-диалогам и до-применить решения, если появились новые реплики
    (в т.ч. РУЧНОЙ ответ оператора с телефона, который мог не прийти вебхуком).

    ВАЖНО для прода: НЕ держим одно DB-соединение на весь проход. Сначала
    КОРОТКОЙ сессией собираем id диалогов, которым нужен анализ (дешёвый
    count-чек), сессию закрываем. Затем каждый диалог обрабатываем В СВОЕЙ
    короткой сессии — соединение возвращается в пул МЕЖДУ LLM-вызовами, а не
    держится все минуты разом (иначе пул исчерпывается → вис, инцидент 06-02).
    Анализирует только диалоги с НОВЫМИ сообщениями с прошлого разбора."""
    from datetime import timedelta
    from db.connection import session_scope
    out = {"examined": 0, "analyzed": 0, "stage": 0, "booked": 0, "suggested": 0, "signaled": 0}
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=since_minutes)

    # Фаза 1 — короткая сессия: собрать кандидатов (без LLM, соединение освобождаем).
    candidates: list[str] = []
    with session_scope() as db:
        rows = (
            db.query(Conversation, Customer)
            .join(Customer, Conversation.customer_id == Customer.id)
            .filter(Conversation.channel == "whatsapp")
            .filter(Conversation.last_message_at >= cutoff)
            .order_by(Conversation.last_message_at.desc())
            .limit(limit).all()
        )
        for conv, cust in rows:
            out["examined"] += 1
            if (conv.lead_stage or "new_lead") in ("lost", "completed_won"):
                continue
            if not force:
                last_seen = (cust.profile_data or {}).get("brain_last_count")
                cur = _dialog_count(db, conv)
                if last_seen is not None and cur <= int(last_seen):
                    continue  # новых сообщений нет — пропускаем (без LLM)
            candidates.append(str(conv.id))

    # Фаза 2 — по кандидату СВОЯ короткая сессия (LLM не держит общий коннект).
    for cid in candidates:
        try:
            with session_scope() as db:
                row = (
                    db.query(Conversation, Customer)
                    .join(Customer, Conversation.customer_id == Customer.id)
                    .filter(Conversation.id == cid).first()
                )
                if not row:
                    continue
                conv, cust = row
                # В bulk-проходе НЕ перегенерируем черновик (LLM+KB-эмбед тяжёлые —
                # исчерпывали пул, инцидент 06-15). Стадия/бронь/сигнал — да.
                res = await analyze_and_advance(db, conv, cust, llm, settings, refresh_draft=False)
            out["analyzed"] += 1
            import asyncio as _a
            await _a.sleep(0.4)  # уступаем loop между LLM-вызовами (health не виснет)
            if res.get("stage"):
                out["stage"] += 1
            if res.get("booked"):
                out["booked"] += 1
            if res.get("suggested"):
                out["suggested"] = out.get("suggested", 0) + 1
            if res.get("signaled"):
                out["signaled"] += 1
        except Exception as e:  # noqa: BLE001
            log.warning(f"[{cid[:8]}] sweep analyze failed: {e}")
    return out
