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

# ДЕТЕРМИНИРОВАННЫЙ разбор дня из РУ-текста — НЕ доверяем LLM (путал «среду»→четверг,
# кейс Денис: «на среду» забронировал на четверг). Берём ПОСЛЕДНЕЕ упоминание дня в
# переписке (самая свежая договорённость). Возвращает LLM-совместимое call_day или None.
_CALL_DAY_WORDS = [
    ("сегодня", "today"), ("завтра", "tomorrow"),
    ("понедельник", "mon"), ("вторник", "tue"),
    ("среду", "wed"), ("среда", "wed"), ("среды", "wed"),
    ("четверг", "thu"),
    ("пятницу", "fri"), ("пятница", "fri"), ("пятницы", "fri"),
    ("субботу", "sat"), ("суббота", "sat"), ("субботы", "sat"),
    ("воскресенье", "sun"), ("воскресение", "sun"), ("воскресенья", "sun"),
]


def _parse_call_day_ru(text: str) -> Optional[str]:
    """Найти ПОСЛЕДНИЙ названный день недели/относительный день в тексте (детерминированно,
    без LLM). rfind → берёт самое позднее упоминание (свежая договорённость)."""
    t = (text or "").lower()
    cands: list = []
    # «послезавтра» обрабатываем ПЕРВЫМ и вырезаем (оно содержит «завтра» — иначе «завтра»
    # перебьёт). Замена на пробелы той же длины — чтобы позиции остальных слов не съехали.
    for m in re.finditer(r"послезавтра|после\s*завтра", t):
        cands.append((m.start(), "posle"))
    t = re.sub(r"послезавтра|после\s*завтра", lambda mm: " " * len(mm.group()), t)
    for word, day in _CALL_DAY_WORDS:
        p = t.rfind(word)
        if p >= 0:
            cands.append((p, day))
    return max(cands, key=lambda x: x[0])[1] if cands else None


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
    elif cd in ("pos", "пос"):  # послезавтра
        target = now_lead + timedelta(days=2)
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
        # пояс по РЕАЛЬНОМУ телефону (cust.phone), не из @lid; дуальное время для Пхукета
        _ph = getattr(cust, "phone", None) or str(chat or "")
        when_lead = _sched.format_call_when(
            new_dt, _sched.lead_tz_from_phone(_ph), _sched.tz_label_from_phone(_ph))
        await _signal_owner(
            settings,
            f"📅 Поставил созвон в календарь из переписки:\n"
            f"Лид: {lead_name} ({chat})\n"
            f"Когда: {when_lead}{(' · ' + medium) if medium else ''}\n"
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


# Слова про звонок/созвон — детерминированный страж против галлюцинаций LLM
# (gemini ловит «договорились о созвоне» там, где звонка вообще не было).
_CALL_WORDS = (
    "созвон", "созвонимся", "созвонить", "созвоним", "перезвон", "позвон", "звонок",
    "звонк", "наберу", "набер", "голосом", "по телефону", "телефон", "zoom", "зум",
    "meet", "митинг", "встреч", "созвучи", "call", "видеозвон", "видео-звон", "вотсап звон",
)


def _mentions_call(transcript: str) -> bool:
    """В переписке вообще есть упоминание звонка/созвона? Если НЕТ — «договорённость
    о созвоне» точно галлюцинация LLM (кейс Вячеслав: «отправлю инфо» — звонка нет)."""
    t = (transcript or "").lower()
    return any(w in t for w in _CALL_WORDS)


# Лид ОТЛОЖИЛ называние времени — конкретной договорённости НЕТ, даже если слово
# «созвон» прозвучало (кейс Zaal: «Хорошо сообщу время» / «как найду спонсора» →
# LLM выдумал «вторник 10:00»). Детерминированный страж против фантом-времени.
_DEFER_TIME = (
    "сообщу время", "сообщу когда", "сообщу позже", "сообщу как", "скажу время",
    "скажу когда", "скажу позже", "напишу время", "напишу когда", "напишу как",
    "напишу позже", "дам знать", "дам вам знать", "уточню время", "уточню когда",
    "уточню позже", "определюсь", "позже скажу", "позже сообщу", "позже напишу",
    "потом скажу", "потом напишу", "как найду", "как смогу", "как освобожусь",
    "как определюсь", "согласую и сообщу", "выберу время", "будет время напишу",
    "появится время", "освобожусь напишу",
)


def _defers_timing(transcript: str) -> bool:
    """Лид отложил называние времени созвона → договорённости с конкретным часом
    по факту нет. Не пересчитываем фантом-час, не держим призрачное предложение."""
    t = (transcript or "").lower()
    return any(p in t for p in _DEFER_TIME)


# ── Слой 2: авто-заполнение кастом-полей под нишу из переписки ───────────────
# Поля (тип проекта/бюджет/срок/услуга…) задаёт ПРЕСЕТ ниши (NICHE_PRESETS), их
# можно править в Настройках. Здесь мозг ЗАОДНО (в том же LLM-вызове, без второго)
# извлекает их значения из того, что лид УЖЕ сказал, и пишет в profile_data['fields']
# — НЕ затирая ручные правки оператора (fields_auto). Если в переписке про поле
# ничего нет — не выдумываем; бот соберёт это естественно по ходу брифа.

def _auto_fill_enabled() -> bool:
    """Авто-заполнение полей ботом (деф. ВКЛ). Выкл в настройках: auto_fill_fields=false."""
    try:
        from services import bot_settings as _bs
        v = _bs.get("auto_fill_fields")
        return True if v is None else bool(v)
    except Exception:  # noqa: BLE001
        return True


def _field_specs(db: Session) -> list[dict]:
    """Активные кастом-поля для извлечения: key/label/type/options (select)."""
    try:
        from db.models import CustomFieldDef
        defs = (
            db.query(CustomFieldDef)
            .filter(CustomFieldDef.active == True)  # noqa: E712
            .order_by(CustomFieldDef.position.asc()).all()
        )
        return [
            {"key": f.key, "label": f.label,
             "options": list(f.options or []) if f.field_type == "select" else None}
            for f in defs
        ]
    except Exception:  # noqa: BLE001
        return []


def _fields_spec_block(specs: list[dict]) -> str:
    """Текст для промпта: список полей ниши + инструкция извлечь их значения."""
    if not specs:
        return ""
    lines = []
    for f in specs:
        opt = f"; выбери из: {', '.join(f['options'])}" if f.get("options") else ""
        lines.append(f"    - {f['key']} ({f['label']}{opt})")
    return (
        '  "extracted_fields": словарь {ключ_поля: значение} — заполни ТОЛЬКО тем, что лид '
        "ЯВНО назвал в переписке; про что не сказано — НЕ включай ключ (не выдумывай). "
        "Поля ниши:\n" + "\n".join(lines) + "\n"
    )


def _merge_auto_fields(cust: Customer, extracted: Any, allowed: set) -> bool:
    """Записать авто-извлечённые ботом значения в profile_data['fields'], НЕ затирая
    ручные правки (пишем только в пустые ИЛИ ранее-авто поля; ключ→fields_auto).
    Возвращает True, если что-то изменилось."""
    if not isinstance(extracted, dict) or not extracted:
        return False
    prof = dict(cust.profile_data or {})
    fields = dict(prof.get("fields") or {})
    auto = set(prof.get("fields_auto") or [])
    changed = False
    for key, val in extracted.items():
        if key not in allowed or val in (None, "", []):
            continue
        sval = str(val).strip()[:200]
        if not sval:
            continue
        cur = fields.get(key)
        if cur in (None, "", []) or key in auto:  # пусто ИЛИ заполнял сам бот
            if cur != sval:
                fields[key] = sval
                auto.add(key)
                changed = True
    if changed:
        prof["fields"] = fields
        prof["fields_auto"] = sorted(auto)
        cust.profile_data = prof
    return changed


async def extract_fields_now(db: Session, conv: Conversation, cust: Customer, llm: Any) -> dict:
    """Разовый БЭКФИЛЛ значений полей из ВСЕЙ переписки — для старых карточек, где
    авто-заполнение (Слой 2) ещё не срабатывало (показывают 0/N). Один LLM-вызов, БЕЗ
    побочных эффектов (стадию/созвон не трогает). Ручные правки защищены _merge_auto_fields."""
    specs = _field_specs(db)
    if not specs:
        return {"ok": True, "filled": False, "reason": "нет активных полей"}
    transcript = _transcript(db, conv, limit=40)
    if not transcript.strip():
        return {"ok": True, "filled": False, "reason": "пустая переписка"}
    prompt = (
        "Из переписки извлеки значения полей клиента и верни СТРОГО JSON (без пояснений) "
        'вида {"extracted_fields": {ключ: значение}}.\n'
        + _fields_spec_block(specs)
        + f"\nПереписка:\n{transcript}"
    )
    try:
        result = await llm.ainvoke(prompt)
        data = _parse_json(getattr(result, "content", None) or "")
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "filled": False, "reason": str(e)[:120]}
    if not data:
        return {"ok": True, "filled": False}
    changed = _merge_auto_fields(cust, data.get("extracted_fields"), {f["key"] for f in specs})
    if changed:
        db.commit()
    return {"ok": True, "filled": changed}


async def apply_operator_reschedule(db: Session, conv: Conversation, cust: Customer,
                                    operator_text: str, settings: Any) -> Optional[str]:
    """КОРЕНЬ #2: оператор написал в чате новое время созвона («завтра в 14:00 по Астане»,
    «перенесём на пятницу в 15») → ДЕТЕРМИНИРОВАННО распознаём день+время и ПЕРЕБРОНИРУЕМ
    существующий созвон, чтобы календарь не отставал от договорённости. Перебронируем ТОЛЬКО
    если созвон уже актуален (стадия on_call ИЛИ есть бронь ИЛИ есть предложение) — не создаём
    созвон из случайной фразы оператора. Возвращает новый ISO-время или None.

    День/время — теми же детерминированными парсерами, что и мозг (не LLM)."""
    text = (operator_text or "").strip()
    if not text:
        return None
    # созвон должен быть в контексте — иначе «я завтра в 14 занят» не должно бронировать
    prof = dict(cust.profile_data or {})
    call_relevant = (conv.lead_stage == "on_call" or prof.get("booked_call_at")
                     or getattr(conv, "pending_call_suggestion", None))
    if not call_relevant:
        return None
    # пояс лида (как в analyze_and_advance) + явная приписка в тексте оператора
    _phone_for_tz = (getattr(cust, "phone", None) or conv.channel_conversation_id or "")
    _multi = _multi_tz_enabled()
    tz = _sched.lead_tz_from_phone(_phone_for_tz) if _multi else _sched.BANGKOK
    tz_label = _sched.tz_label_from_phone(_phone_for_tz) if _multi else "время Пхукета"
    if _multi:
        _ex = _sched.explicit_tz_from_text(text)
        if _ex:
            tz, tz_label = _ex
    now_utc = datetime.now(timezone.utc)
    now_lead = now_utc.astimezone(tz)
    _day = _parse_call_day_ru(text)
    if not _day:
        return None  # нет названного дня — не перенос
    # время вытаскиваем из текста простым поиском «ЧЧ[:ММ]» / «в ЧЧ»
    import re as _re
    _tm = _re.search(r"(\d{1,2})[:.\s](\d{2})", text)
    _hh = None
    if _tm:
        _hh = f"{_tm.group(1)}:{_tm.group(2)}"
    else:
        _tm2 = _re.search(r"\bв\s+(\d{1,2})\b", text)
        if _tm2:
            _hh = _tm2.group(1)
    if not _hh:
        return None  # день есть, а времени нет — не однозначный перенос
    # ГАРД ЛОЖНОГО ПЕРЕНОСА: «завтра НАПИШУ вам в 14:00» = оператор НАПИШЕТ текст,
    # а НЕ переносит ЗВОНОК. Раньше любой день+время в реплике оператора на стадии
    # on_call двигал бронь → корёжил корректный созвон (кейс +77012990880: верный
    # созвон «сегодня» уезжал на «завтра» из вежливой подписи). Если есть «текстовый»
    # глагол (напишу/скину/отправлю/пришлю/вышлю) и НЕТ ни одного сигнала про звонок —
    # это НЕ перенос созвона, выходим.
    _tl = text.lower()
    _has_call_intent = any(k in _tl for k in (
        "созвон", "звон", "набер", "перенес", "перенос",
        "в силе", "встрет", "встреч", "созвонимся", "колл", "call"))
    _has_text_intent = any(k in _tl for k in (
        "напиш", "скин", "отправл", "пришл", "вышл", "сброшу"))
    if _has_text_intent and not _has_call_intent:
        return None
    new_dt = _resolve_call_dt(now_lead, _day, _hh)
    if not new_dt or new_dt <= now_utc:
        return None
    # уже на это время? не дёргаем
    _cur = prof.get("booked_call_at")
    if _cur:
        try:
            _curdt = datetime.fromisoformat(str(_cur).replace("Z", "+00:00"))
            if _curdt.tzinfo is None:
                _curdt = _curdt.replace(tzinfo=timezone.utc)
            if abs((_curdt - new_dt).total_seconds()) < 600:
                return None
        except (ValueError, TypeError):
            pass
    # ПЕРЕБРОНЬ — те же helpers, что ручной перенос из карточки
    import asyncio as _aio
    from services.scheduled_actions import cancel_call_actions, write_call_booking, write_call_reminder
    await _aio.to_thread(cancel_call_actions, str(conv.id))
    prof["booked_call_at"] = new_dt.isoformat()
    cust.profile_data = prof
    _from = conv.lead_stage
    conv.lead_stage = "on_call"
    if _from != "on_call":
        db.add(StageTransition(conversation_id=conv.id, customer_id=conv.customer_id,
                               from_stage=_from, to_stage="on_call", by="operator"))
    db.commit()
    when_h = _sched.format_call_when(new_dt, tz, tz_label)
    _chat = conv.channel_conversation_id
    _is_msgr = (conv.channel or "website").lower() != "website"
    _medium = prof.get("call_medium")
    try:
        await _aio.to_thread(write_call_booking, customer_id=str(cust.id),
                             conversation_id=str(conv.id), channel=conv.channel,
                             chat_id=str(_chat) if _chat else None, call_at=new_dt, medium=_medium)
        for _fire, _label in _sched.reminder_schedule(new_dt, now_utc):
            if settings and getattr(settings, "telegram_operator_group_id", None):
                await _aio.to_thread(write_call_reminder, customer_id=str(cust.id),
                                     conversation_id=str(conv.id), channel=conv.channel,
                                     chat_id=str(settings.telegram_operator_group_id), due_at=_fire,
                                     text=_sched.admin_reminder_text(new_dt, cust.name or "лид", _label,
                                                                     _medium, cust.email or ""),
                                     audience="admin")
            if _chat and _is_msgr:
                await _aio.to_thread(write_call_reminder, customer_id=str(cust.id),
                                     conversation_id=str(conv.id), channel=conv.channel,
                                     chat_id=str(_chat), due_at=_fire,
                                     text=_sched.lead_reminder_text(new_dt, _label, _medium,
                                                                    lang=prof.get("lang") or "ru",
                                                                    phone=str(_chat) if _chat else None),
                                     audience="lead")
    except Exception as e:  # noqa: BLE001
        log.warning(f"[{str(conv.id)[:8]}] operator-reschedule reminders failed: {e}")
    try:
        from services.bot_decisions import log_decision
        log_decision("call_rescheduled",
                     f"Оператор перенёс созвон сообщением в чате на {when_h} — обновил бронь и календарь",
                     conversation_id=conv.id, customer_id=cust.id, actor="operator",
                     detail={"at": new_dt.isoformat(), "when_human": when_h}, db=db)
    except Exception:  # noqa: BLE001
        pass
    log.info(f"[{str(conv.id)[:8]}] operator reschedule → {new_dt.isoformat()}")
    return new_dt.isoformat()


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
    # Явная приписка пояса в переписке («в 14:00 по Астане») ПЕРЕОПРЕДЕЛЯЕТ пояс по
    # номеру — лид прямо сказал, в каком времени; это важнее догадки по префиксу.
    if _multi:
        _ex_tz = _sched.explicit_tz_from_text(transcript)
        if _ex_tz:
            tz, tz_label = _ex_tz
    now_utc = datetime.now(timezone.utc)
    now_lead = now_utc.astimezone(tz)
    # Слой 2: поля ниши — мозг заодно извлечёт их значения из переписки (если вкл).
    field_specs = _field_specs(db) if _auto_fill_enabled() else []
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
        '  "reason": кратко почему (≤120 симв).\n'
        + _fields_spec_block(field_specs)
        + f"\nПереписка:\n{transcript}"
    )
    try:
        result = await llm.ainvoke(prompt)
        data = _parse_json(getattr(result, "content", None) or "")
    except Exception as e:  # noqa: BLE001
        log.warning(f"[{str(conv.id)[:8]}] brain LLM failed: {e}")
        return done
    if not data:
        return done

    # Слой 2: авто-заполнение полей ниши из переписки (защита ручных правок).
    if field_specs:
        try:
            if _merge_auto_fields(cust, data.get("extracted_fields"),
                                  {f["key"] for f in field_specs}):
                db.commit()
                done["fields_filled"] = True
        except Exception as e:  # noqa: BLE001
            db.rollback()
            log.warning(f"[{str(conv.id)[:8]}] brain auto-fields failed: {e}")

    # 1) стадия вперёд.
    # WA_BRAIN идёт ПАРАЛЛЕЛЬНО hot-path (оба — независимые писатели lead_stage в своих
    # сессиях). Brain читает conv из своей сессии и мог взять устаревшую стадию ДО коммита
    # hot-path. Перечитываем СВЕЖУЮ стадию из БД (column-query минует identity-map → видит
    # уже закоммиченное hot-path значение, READ COMMITTED) и применяем forward-guard к ней —
    # иначе brain мог откатить стадию, которую hot-path только что продвинул вперёд.
    try:
        _fresh_stage = (
            db.query(Conversation.lead_stage)
            .filter(Conversation.id == conv.id)
            .scalar()
        )
        if _fresh_stage and _fresh_stage != conv.lead_stage:
            conv.lead_stage = _fresh_stage
    except Exception:  # noqa: BLE001
        pass
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
        try:
            from services.bot_decisions import log_decision as _logd
            _rd = str(data.get("reason") or "").strip()
            _logd("stage_change",
                  f"Передвинул «{from_stage or 'старт'}» → «{new_stage}»"
                  + (f": {_rd}" if _rd else " — распознал прогресс по переписке"),
                  conversation_id=conv.id, customer_id=conv.customer_id,
                  detail={"from": from_stage, "to": new_stage, "by": "bot-brain"}, db=db)
        except Exception:  # noqa: BLE001
            pass
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
    # День — ДЕТЕРМИНИРОВАННО из текста (надёжнее LLM, который путал «среду»→четверг,
    # кейс Денис); LLM-call_day остаётся запасным, если в тексте явного дня нет.
    _call_day = _parse_call_day_ru(transcript) or data.get("call_day")
    resolved_dt = _resolve_call_dt(now_lead, _call_day, data.get("call_time"))
    # СТОП ФАНТОМ-СОЗВОНАМ: если лид молчит (мы написали последними), договорённости
    # по факту нет — не создаём призрачный созвон (кейс «увидел КП и пропал»). Такой
    # лид попадёт в дожим через next_action, а не в календарь.
    # Предлагаем созвон ТОЛЬКО при: явной договорённости (call_agreed) + НАЗВАННОМ
    # дне (call_day) + в переписке РЕАЛЬНО упомянут звонок/созвон (_mentions_call —
    # детерминированный страж против галлюцинаций LLM) + лид НЕ молчит.
    _calls = _mentions_call(transcript)
    _defers = _defers_timing(transcript)
    if (data.get("call_agreed") and _call_day and resolved_dt
            and _calls and not _defers and not _lead_silent(db, conv)):
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
                # ВЕРНЫЙ пояс — из cust.phone + мультипояс (см. выше), НЕ из @lid.
                # Дуальное время: «<время лида> (город) = <ЧЧ:ММ> (Пхукет)» — админ в
                # Пхукете видит ОБА времени и не путается (кейс «14:00 по Астане»).
                when_h = _sched.format_call_when(new_dt, tz, tz_label)
                conv.pending_call_suggestion = {
                    "at": new_dt.isoformat(),
                    "when_human": when_h,
                    "medium": data.get("call_medium"),
                    "reason": str(data.get("reason") or "")[:200],
                    "ts": now_utc.isoformat(),
                }
                db.commit()
                done["suggested"] = new_dt.isoformat()
                try:
                    from services.bot_decisions import log_decision as _logds
                    _med = data.get("call_medium")
                    _logds("call_suggested",
                           f"Распознал договорённость о созвоне: {when_h}"
                           + (f" ({_med})" if _med else "")
                           + ". Поставил предложение менеджеру на подтверждение.",
                           conversation_id=conv.id, customer_id=conv.customer_id,
                           detail={"at": new_dt.isoformat(), "when_human": when_h, "medium": _med}, db=db)
                except Exception:  # noqa: BLE001
                    pass
                name = cust.name or conv.channel_conversation_id or "лид"
                await _signal_owner(
                    settings,
                    f"📅 Похоже, договорились о созвоне:\nЛид: {name}\n"
                    f"Когда: {when_h}\n"
                    f"Откройте карточку в панели → «Создать событие», если верно.",
                )
        except (ValueError, TypeError) as e:
            log.warning(f"[{str(conv.id)[:8]}] brain bad call_datetime: {e}")
    # Снимаем СВОЁ прежнее предложение ТОЛЬКО если в переписке ВООБЩЕ нет упоминания
    # звонка/созвона (детерминированно ложное — кейс Вячеслав: «отправлю инфо»). Так
    # НЕ снесём валидное (где «созвонимся» есть, но LLM в этот проход не распознал —
    # кейс Денис). Подтверждённые брони (booked_call_at) не трогаем.
    elif getattr(conv, "pending_call_suggestion", None) and (not _calls or _defers):
        # Снимаем призрачное предложение, если звонка в переписке нет ВООБЩЕ
        # (кейс Вячеслав) ИЛИ лид отложил время (кейс Zaal: «сообщу время»).
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
