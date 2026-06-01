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

# Явное согласие лида (выбор слота). «не» рядом → НЕ согласие (см. has_neg).
_AFFIRM = (
    "да ", "да,", "да.", "ага", "давай", "ок", "окей", "хорошо", "годит",
    "подходит", "подойдёт", "подойдет", "удобн", "выбираю", "бронир", "запиш",
    "пусть", "идёт", "идет", "согласен", "согласна", "договорились", "беру",
)
# Маркеры вопроса-уточнения (не выбор слота, даже если упомянут час).
_QUESTION_CUES = (
    "только", "тоже", "разве", "неужели", "а есть", "а можно ли", "других нет",
    "другого нет", "что друг", "а друг", "а раньше", "а позже", "а пораньше",
)
# Отмена / перенос / отказ от созвона. Границы слова обязательны: иначе «не удобно»
# ловится внутри «МНЕ удобно» (ложная отмена согласия).
_CANCEL_RE = re.compile(
    r"\b(?:отмен\w*|переду\w*|перенес\w*|перенест\w*|перенос\w*|отказ\w*|"
    r"не\s+надо|ненадо|без\s+созвон\w*|"
    r"не\s+удобно|неудобно|не\s+получится|не\s+смогу|не\s+сейчас|"
    r"в\s+друг(?:ой|ое)\s+раз|пока\s+не\s+(?:надо|нужно)|потом\s+созвон\w*)",
    re.IGNORECASE,
)


def detect_cancel_intent(text: str) -> bool:
    """Лид отказывается / просит перенести / отменить созвон."""
    return bool(_CANCEL_RE.search(text or ""))

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


def _start_of_local_day(now_utc: datetime, days_ahead: int = 0) -> datetime:
    """Начало локального (Бангкок) дня now+days_ahead, как UTC-aware datetime."""
    loc = _to_local(now_utc) + timedelta(days=days_ahead)
    loc = loc.replace(hour=0, minute=0, second=0, microsecond=0)
    return loc.astimezone(timezone.utc)


def _search_slots(
    now_utc: datetime,
    taken_keys: set,
    n: int,
    start_from: datetime,
    hour_min: Optional[int],
    hour_max: Optional[int],
) -> list[datetime]:
    cur = start_from.replace(minute=0, second=0, microsecond=0)
    if cur < start_from:
        cur += timedelta(hours=1)
    horizon = now_utc + timedelta(days=SEARCH_HORIZON_DAYS)
    out: list[datetime] = []
    while cur <= horizon and len(out) < n:
        if _in_window(cur):
            loc = _to_local(cur)
            if (hour_min is None or loc.hour >= hour_min) and (hour_max is None or loc.hour <= hour_max):
                k = _hour_key(cur)
                if k not in taken_keys and k not in out:
                    out.append(k)
        cur += timedelta(hours=1)
    return out


def compute_free_slots(
    now_utc: datetime,
    taken: Optional[Iterable[datetime]] = None,
    n: int = 2,
    *,
    not_before: Optional[datetime] = None,
    hour_min: Optional[int] = None,
    hour_max: Optional[int] = None,
) -> list[datetime]:
    """Вернуть n ближайших свободных слотов (часовые границы, UTC-aware).

    Будни в окне 11:00–19:00 (локально), не раньше now + LEAD_TIME_HOURS, занятые
    пропускаем. Если заданы предпочтения лида (not_before — «завтра»/день недели;
    hour_min/hour_max — «утром/днём/вечером»), сначала ищем под них; если столько
    не набралось — мягко ослабляем (сначала окно времени, потом дату), чтобы
    ВСЕГДА предложить варианты.
    """
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    taken_keys = {_hour_key(t) for t in (taken or [])}
    base_start = now_utc + timedelta(hours=LEAD_TIME_HOURS)
    start = max(base_start, not_before) if not_before else base_start

    out = _search_slots(now_utc, taken_keys, n, start, hour_min, hour_max)
    if len(out) < n and (hour_min is not None or hour_max is not None):
        # ослабляем окно времени, дату-предпочтение оставляем
        out += _search_slots(now_utc, taken_keys | set(out), n - len(out), start, None, None)
    if len(out) < n:
        # ослабляем и дату — ближайшие вообще
        out += _search_slots(now_utc, taken_keys | set(out), n - len(out), base_start, None, None)
    return out[:n]


# Время суток: утро / день / вечер → часовые окна старта (локально).
_TOD = (
    (("вечер", "вечером"), 17, 19),
    (("обед", "после обеда", "днём", "днем", " дня"), 13, 16),
    (("утро", "утром", "с утра"), 11, 12),
)


def parse_time_preference(text: str, now_utc: datetime):
    """Из реплики лида вытащить пожелание по времени созвона.

    Возвращает (not_before, hour_min, hour_max) для compute_free_slots.
    Понимает: сегодня / завтра / послезавтра / день недели («в среду»),
    и время суток (утром / днём / вечером).
    """
    t = (text or "").lower()
    not_before = None
    if "послезавтра" in t:
        not_before = _start_of_local_day(now_utc, 2)
    elif "завтра" in t:
        not_before = _start_of_local_day(now_utc, 1)
    elif "сегодня" in t:
        not_before = None  # ближайшие и так начинаются с сегодня
    else:
        cur_wd = _to_local(now_utc).weekday()
        for i, wd in enumerate(_RU_WEEKDAYS):
            stem = wd[:-1]  # «вторник»→«вторни», ловит «во вторник/вторника»
            if stem in t:
                days_ahead = (i - cur_wd) % 7
                if days_ahead == 0:
                    days_ahead = 7  # «в понедельник» в понедельник → следующий
                not_before = _start_of_local_day(now_utc, days_ahead)
                break

    hour_min = hour_max = None
    for words, lo, hi in _TOD:
        if any(w in t for w in words):
            hour_min, hour_max = lo, hi
            break

    # Явный единственный час («в 14», «14:00») в рабочем окне → точное предпочтение.
    # Несколько разных часов («в 11 и 12») — это пересказ оффера, не предпочтение.
    if hour_min is None:
        expl = re.findall(r"\b([01]?\d|2[0-3]):[0-5]\d\b", t)
        expl += re.findall(r"\b(?:во|в|на)\s+([01]?\d|2[0-3])(?!\d)", t)  # без «к» (≈ «к 15 числу»)
        distinct = {int(x) for x in expl}
        if len(distinct) == 1:
            h = distinct.pop()
            if WORK_START_HOUR <= h <= WORK_LAST_START_HOUR:
                hour_min = hour_max = h

    return not_before, hour_min, hour_max


def format_slot_human(dt_utc: datetime, now_utc: Optional[datetime] = None) -> str:
    """Человекочитаемое время слота (локально, Бангкок).

    Если передан now_utc: сегодня → «сегодня в 14:00», завтра → «завтра в 14:00»,
    иначе — «вторник, 3 июня, в 14:00». Без now_utc — всегда по дню недели.
    """
    loc = _to_local(dt_utc)
    hhmm = loc.strftime("%H:%M")
    if now_utc is not None:
        today = _to_local(now_utc).date()
        d = loc.date()
        if d == today:
            return f"сегодня в {hhmm}"
        if d == today + timedelta(days=1):
            return f"завтра в {hhmm}"
    return f"{_RU_WEEKDAYS[loc.weekday()]}, {loc.day} {_RU_MONTHS[loc.month]}, в {hhmm}"


def _match_offered_by_day(t: str, offered: list[datetime], now_utc: Optional[datetime]) -> Optional[datetime]:
    """Выбор слота по дню («четверг», «завтра», «сегодня»), если он однозначен."""
    for i, wd in enumerate(_RU_WEEKDAYS):
        if wd[:-1] in t:
            cand = [s for s in offered if _to_local(s).weekday() == i]
            return cand[0] if len(cand) == 1 else None
    if now_utc is not None:
        today = _to_local(now_utc).date()
        target = None
        if "послезавтра" in t:
            target = today + timedelta(days=2)
        elif "завтра" in t:
            target = today + timedelta(days=1)
        elif "сегодня" in t:
            target = today
        if target is not None:
            cand = [s for s in offered if _to_local(s).date() == target]
            return cand[0] if len(cand) == 1 else None
    return None


def parse_slot_choice(
    text: str, offered: list[datetime], now_utc: Optional[datetime] = None
) -> Optional[datetime]:
    """Понять, какой из предложенных слотов выбрал лид. None — если непонятно.

    Понимает: «первый/1», «второй/2», явное время («в 14», «14:00»), день недели
    («четверг») / «сегодня»/«завтра» — но ТОЛЬКО когда выбор ОДНОЗНАЧЕН.

    НЕ считает выбором: вопрос-уточнение («…только в 11 и 12?»), упоминание
    нескольких времён сразу, отрицание («не в 11») — иначе бронируем то, что лид
    не выбирал (реальный баг из живого теста).
    """
    if not offered:
        return None
    t = (text or "").lower()
    # «не» как отдельное слово (не «мНЕ»), а также «неудобно»/«ненадо».
    has_neg = bool(re.search(r"\bне\b", t) or re.search(r"неудоб|ненад", t))
    has_affirm = (not has_neg) and any(w in t for w in _AFFIRM)
    has_qcue = any(w in t for w in _QUESTION_CUES)
    # Уточнение («…только в 11 и 12?») или отказ без явного согласия — НЕ выбор слота.
    if (has_qcue or has_neg) and not has_affirm:
        return None

    # Упоминание НЕСКОЛЬКИХ предложенных часов (в т.ч. голым числом: «11 и 12») —
    # это пересказ оффера, не выбор одного слота.
    offered_hours = {_to_local(s).hour for s in offered}
    bare_hours = {int(x) for x in re.findall(r"\b(\d{1,2})\b", t)}
    if len(offered_hours & bare_hours) >= 2:
        return None

    # 1) Явное время (самое специфичное). Чтобы случайные числа («5 страниц»,
    #    «к 15 числу») НЕ принимались за выбор времени, засчитываем только:
    #    (а) HH:MM с двоеточием, либо (б) число сразу после предлога в/во/к/на.
    time_hits: list[tuple[int, int]] = []
    for hh, mm in re.findall(r"\b([01]?\d|2[0-3]):([0-5]\d)\b", t):
        time_hits.append((int(hh), int(mm)))
    for hh in re.findall(r"\b(?:во|в|к|на)\s+([01]?\d|2[0-3])(?!\d)", t):
        time_hits.append((int(hh), 0))
    matched: list[datetime] = []
    for hour, minute in time_hits:
        for slot in offered:
            loc = _to_local(slot)
            if loc.hour == hour and (minute == 0 or loc.minute == minute) and slot not in matched:
                matched.append(slot)
    if len(matched) == 1:
        return matched[0]
    if len(matched) >= 2:
        return None  # назвал несколько времён → выбор не однозначен

    # 2) Выбор по дню (когда слоты различаются датой).
    by_day = _match_offered_by_day(t, offered, now_utc)
    if by_day is not None:
        return by_day

    # 3) Порядковые слова.
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


def parse_explicit_datetime(text: str, now_utc: datetime) -> Optional[datetime]:
    """Лид САМ назвал конкретное время с согласием → вернуть UTC-слот для прямой брони.

    Срабатывает на «давайте в четверг в 14», «мне удобно завтра в 15» — когда есть
    согласие (affirm) + единственный явный час + НЕТ отрицания/вопроса-уточнения.
    Валидирует рабочее окно (будни 11–19 локал) и lead-time. Занятость слота
    проверяет вызывающий (через get_taken_call_slots). None — если не однозначно.
    """
    t = (text or "").lower()
    has_neg = bool(re.search(r"\bне\b", t) or re.search(r"неудоб|ненад", t))
    if has_neg or any(w in t for w in _QUESTION_CUES):
        return None
    if not any(w in t for w in _AFFIRM):
        return None

    hh = mm = None
    hm = re.findall(r"\b([01]?\d|2[0-3]):([0-5]\d)\b", t)
    if hm:
        pairs = {(int(a), int(b)) for a, b in hm}
        if len(pairs) == 1:
            hh, mm = pairs.pop()
    if hh is None:
        bare = {int(x) for x in re.findall(r"\b(?:во|в|на)\s+([01]?\d|2[0-3])(?!\d)", t)}
        if len(bare) == 1:
            hh, mm = bare.pop(), 0
    if hh is None or not (WORK_START_HOUR <= hh <= WORK_LAST_START_HOUR):
        return None

    nb, _, _ = parse_time_preference(text, now_utc)
    day_local = _to_local(nb) if nb is not None else _to_local(now_utc)
    cand = day_local.replace(hour=hh, minute=mm, second=0, microsecond=0).astimezone(timezone.utc)
    if not _in_window(cand) or cand < now_utc + timedelta(hours=LEAD_TIME_HOURS):
        return None
    return cand


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


def offer_slots_text(slots: list[datetime], now_utc: Optional[datetime] = None) -> str:
    """Человекочитаемый список предложенных слотов (для инъекции в промпт)."""
    if not slots:
        return "(нет свободных слотов в ближайшее время — предложи лиду написать удобное время)"
    lines = [f"{i + 1}) {format_slot_human(s, now_utc)}" for i, s in enumerate(slots)]
    return "; ".join(lines)
