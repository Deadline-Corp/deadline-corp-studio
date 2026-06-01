# -*- coding: utf-8 -*-
"""Call-booking engine — ЧИСТАЯ логика (только datetime + re, без БД/IO).

Бот-продажник назначает созвон: предлагает 2 свободных слота в рабочем окне
(будни 11:00–20:00 по Пхукету/Бангкоку, UTC+7), лид выбирает один — бронируем.
Способ связи (WhatsApp/Telegram/Zoom/Google Meet) — на выбор лида.

Всё время — timezone-aware UTC. Рабочие часы трактуются в BANGKOK (UTC+7).
Модуль НЕ импортирует тяжёлые зависимости → легко юнит-тестить отдельно.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

# ---- Конфиг окна созвонов ----
TZ_OFFSET_HOURS = 7                       # Пхукет/Бангкок, UTC+7
BANGKOK = timezone(timedelta(hours=TZ_OFFSET_HOURS))
WORK_START_HOUR = 11                      # локально, первый слот стартует в 11:00
WORK_LAST_START_HOUR = 19                 # последний 1ч-слот стартует в 19:00 (конец 20:00)
SLOT_MINUTES = 60
LEAD_TIME_HOURS = 3                       # не предлагать слоты раньше, чем через N часов
SEARCH_HORIZON_DAYS = 21                  # как далеко вперёд искать свободные слоты

# Напоминания: (за сколько до созвона, человекочитаемая метка)
REMINDERS: tuple[tuple[timedelta, str], ...] = (
    (timedelta(hours=24), "завтра"),
    (timedelta(hours=3), "через 3 часа"),
    (timedelta(hours=1), "через час"),
)

# Способы созвона на выбор лида.
CALL_MEDIA = ("WhatsApp", "Telegram", "Zoom", "Google Meet")

_RU_WEEKDAYS = [
    "понедельник", "вторник", "среда", "четверг",
    "пятница", "суббота", "воскресенье",
]
_RU_MONTHS = [
    "", "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]


def _to_local(dt_utc: datetime) -> datetime:
    """UTC-aware → локальное (Бангкок) время."""
    if dt_utc.tzinfo is None:
        dt_utc = dt_utc.replace(tzinfo=timezone.utc)
    return dt_utc.astimezone(BANGKOK)


def _hour_key(dt_utc: datetime) -> datetime:
    """Нормализуем к началу часа в UTC (ключ слота для сравнения занятости)."""
    if dt_utc.tzinfo is None:
        dt_utc = dt_utc.replace(tzinfo=timezone.utc)
    return dt_utc.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)


def _in_window(dt_utc: datetime) -> bool:
    """Слот попадает в рабочее окно: будни (Пн–Пт), 11:00–19:00 локального старта."""
    loc = _to_local(dt_utc)
    return loc.weekday() < 5 and WORK_START_HOUR <= loc.hour <= WORK_LAST_START_HOUR


def compute_free_slots(
    now_utc: datetime,
    taken: Optional[Iterable[datetime]] = None,
    n: int = 2,
) -> list[datetime]:
    """Вернуть n ближайших свободных слотов (часовые границы, UTC-aware).

    - старт не раньше now + LEAD_TIME_HOURS, выровнен вверх до целого часа;
    - только будни в окне 11:00–19:00 (локально);
    - пропускаем уже занятые слоты (`taken`, сравнение по началу часа UTC).
    """
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    taken_keys = {_hour_key(t) for t in (taken or [])}

    start = now_utc + timedelta(hours=LEAD_TIME_HOURS)
    cur = start.replace(minute=0, second=0, microsecond=0)
    if cur < start:
        cur += timedelta(hours=1)

    horizon = now_utc + timedelta(days=SEARCH_HORIZON_DAYS)
    out: list[datetime] = []
    while cur <= horizon and len(out) < n:
        if _in_window(cur) and _hour_key(cur) not in taken_keys:
            out.append(_hour_key(cur))
        cur += timedelta(hours=1)
    return out


def format_slot_human(dt_utc: datetime) -> str:
    """«вторник, 3 июня, 14:00» (локальное время Бангкок)."""
    loc = _to_local(dt_utc)
    return f"{_RU_WEEKDAYS[loc.weekday()]}, {loc.day} {_RU_MONTHS[loc.month]}, {loc.strftime('%H:%M')}"


def parse_slot_choice(text: str, offered: list[datetime]) -> Optional[datetime]:
    """Понять, какой из предложенных слотов выбрал лид. None — если непонятно.

    Понимает: «первый/1/первое», «второй/2», а также явное время («в 14», «14:00»),
    совпадающее с одним из предложенных (по локальному часу).
    """
    if not offered:
        return None
    t = (text or "").lower()

    # 1) Явное время (самое специфичное). Чтобы случайные числа («5 страниц»,
    #    «к 15 числу») НЕ принимались за выбор времени, засчитываем только:
    #    (а) HH:MM с двоеточием, либо (б) число сразу после предлога в/во/к/на.
    time_hits: list[tuple[int, int]] = []
    for hh, mm in re.findall(r"\b([01]?\d|2[0-3]):([0-5]\d)\b", t):
        time_hits.append((int(hh), int(mm)))
    for hh in re.findall(r"\b(?:во|в|к|на)\s+([01]?\d|2[0-3])(?!\d)", t):
        time_hits.append((int(hh), 0))
    for hour, minute in time_hits:
        for slot in offered:
            loc = _to_local(slot)
            if loc.hour == hour and (minute == 0 or loc.minute == minute):
                return slot

    # 2) Порядковые слова.
    if len(offered) >= 2 and "втор" in t:
        return offered[1]
    if "перв" in t:
        return offered[0]

    # 3) Цифра-порядковый — ТОЛЬКО безопасно: либо всё сообщение ≈ сама цифра
    #    («2», «2)», «2.»), либо рядом слово «вариант/слот». Иначе «в 2 раза»,
    #    «2 страницы», «за 1 день» НЕ должны считаться выбором слота.
    t_core = t.strip().rstrip(").!").strip()
    if t_core in ("1", "2"):
        idx = int(t_core) - 1
        if idx < len(offered):
            return offered[idx]
    if len(offered) >= 2 and re.search(r"(?:вариант|вар\.?|слот|option)\s*2\b|\b2\s*(?:вариант|вар|слот)", t):
        return offered[1]
    if re.search(r"(?:вариант|вар\.?|слот|option)\s*1\b|\b1\s*(?:вариант|вар|слот)", t):
        return offered[0]
    return None


def detect_call_medium(text: str) -> Optional[str]:
    """Определить предпочтительный способ созвона из текста лида."""
    t = (text or "").lower()
    if any(w in t for w in ("whatsapp", "whats app", "вотсап", "ватсап", "вацап", "вотсапп")):
        return "WhatsApp"
    if any(w in t for w in ("zoom", "зум")):
        return "Zoom"
    if any(w in t for w in ("google meet", "гугл мит", "гугл-мит", "гуглмит", "g meet", "meet", "мит")):
        return "Google Meet"
    if any(w in t for w in ("telegram", "телеграм", "телега", "тг", "tg")):
        return "Telegram"
    return None


def reminder_schedule(call_at_utc: datetime, now_utc: datetime) -> list[tuple[datetime, str]]:
    """Список (когда_напомнить_UTC, метка) для созвона. Прошедшие — отбрасываем."""
    if call_at_utc.tzinfo is None:
        call_at_utc = call_at_utc.replace(tzinfo=timezone.utc)
    out: list[tuple[datetime, str]] = []
    for delta, label in REMINDERS:
        fire_at = call_at_utc - delta
        if fire_at > now_utc:
            out.append((fire_at, label))
    return out


def lead_reminder_text(call_at_utc: datetime, label: str, medium: Optional[str] = None) -> str:
    """Текст напоминания ЛИДУ."""
    when = format_slot_human(call_at_utc)
    via = f" ({medium})" if medium else ""
    return (
        f"Напоминаю про наш созвон {label} — {when}{via}. "
        f"Команда DEADLINE на связи 🙂 Если планы поменялись — просто напишите."
    )


def admin_reminder_text(
    call_at_utc: datetime, lead_name: str, label: str, medium: Optional[str] = None,
    contact: Optional[str] = None,
) -> str:
    """Текст напоминания АДМИНУ (оператору)."""
    when = format_slot_human(call_at_utc)
    via = f" · {medium}" if medium else ""
    who = lead_name or "лид"
    contact_s = f" · {contact}" if contact else ""
    return f"📞 Созвон {label} — {when}{via}\nЛид: {who}{contact_s}"


def offer_slots_text(slots: list[datetime]) -> str:
    """Человекочитаемый список предложенных слотов (для инъекции в промпт)."""
    if not slots:
        return "(нет свободных слотов в ближайшее время — предложи лиду написать удобное время)"
    lines = [f"{i + 1}) {format_slot_human(s)}" for i, s in enumerate(slots)]
    return "; ".join(lines)
