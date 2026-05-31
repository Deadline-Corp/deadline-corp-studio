# -*- coding: utf-8 -*-
"""Парсер «когда написать лиду» из текста («через неделю / в субботу / через 2 минуты»).

Чистая функция, без зависимостей от БД — тестируется локально.
Task Engine (Фаза B): результат → due_at для отложенного действия бота.

TZ: операции Deadline в UTC+7 (Пхукет/Бангкок). Для дневной гранулярности
(«завтра», «в субботу») время по умолчанию 11:00 локального. Для минут/часов —
точная дельта от now (удобно и для теста: «через 2 минуты»).
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Optional

TZ_OFFSET = timedelta(hours=7)  # UTC+7
DEFAULT_HOUR_LOCAL = 11         # дефолтное время суток для дневной гранулярности

_WEEKDAYS = {
    "понедельник": 0, "пн": 0,
    "вторник": 1, "вт": 1,
    "сред": 2, "ср": 2,            # среду/среда
    "четверг": 3, "чт": 3,
    "пятниц": 4, "пт": 4,          # пятницу/пятница
    "суббот": 5, "сб": 5,          # субботу/суббота
    "воскресень": 6, "вс": 6,
}


def _at_local_hour(now_utc: datetime, day_delta: int, hour: int = DEFAULT_HOUR_LOCAL) -> datetime:
    """now + day_delta дней, время = hour:00 локального (UTC+7), вернуть aware-UTC."""
    local = now_utc + TZ_OFFSET
    target_local = (local + timedelta(days=day_delta)).replace(
        hour=hour, minute=0, second=0, microsecond=0
    )
    return (target_local - TZ_OFFSET).replace(tzinfo=timezone.utc)


def parse_followup_when(text: Optional[str], now: Optional[datetime] = None) -> Optional[datetime]:
    """Вернуть aware-UTC datetime, когда лид просит написать, либо None.

    Поддержка: «через N минут/часов/дней/недель», «завтра», «послезавтра»,
    «в <день недели>», «на следующей неделе». None = явной просьбы нет.
    """
    if not text:
        return None
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    t = text.lower().replace("ё", "е")

    # «через N минут/час/дней/недель»
    m = re.search(r"через\s+(\d+)\s*(минут|мин|час|часа|часов|день|дня|дней|недел)", t)
    if m:
        n = int(m.group(1)); unit = m.group(2)
        if unit.startswith("мин"):
            return now + timedelta(minutes=n)
        if unit.startswith("час"):
            return now + timedelta(hours=n)
        if unit.startswith("недел"):
            return _at_local_hour(now, 7 * n)
        return _at_local_hour(now, n)  # день/дня/дней

    # «через минуту/час/день/неделю» (без числа = 1)
    if re.search(r"через\s+минут", t):
        return now + timedelta(minutes=1)
    if re.search(r"через\s+час", t):
        return now + timedelta(hours=1)
    if re.search(r"через\s+(недел|week)", t) or "на следующей неделе" in t:
        return _at_local_hour(now, 7)
    if re.search(r"через\s+(день|пару дней)", t):
        return _at_local_hour(now, 1)

    if "послезавтра" in t:
        return _at_local_hour(now, 2)
    if "завтра" in t:
        return _at_local_hour(now, 1)

    # «в субботу / в пятницу / в пн» → ближайший такой день недели (если сегодня — то +7)
    for key, wd in _WEEKDAYS.items():
        if re.search(r"\b(в|во)\s+" + key, t) or re.search(r"\b" + key + r"\b", t):
            local = now + TZ_OFFSET
            ahead = (wd - local.weekday()) % 7
            if ahead == 0:
                ahead = 7  # «в субботу», когда сегодня суббота → следующая
            return _at_local_hour(now, ahead)

    return None
