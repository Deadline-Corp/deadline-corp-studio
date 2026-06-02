# -*- coding: utf-8 -*-
"""Детект просьбы лида общаться в ДРУГОМ канале (не Telegram): WhatsApp / телефон /
Viber / Signal. Чистая функция, без БД — тестируется локально.

Поведение бота при срабатывании: согласиться («да, конечно»), сказать что передаст
менеджеру и человек свяжется ИМЕННО в этом канале (НЕ тащить в Telegram), взять
контакт (номер) если дан или попросить его, и поставить задачу оператору.

ВАЖНО: это НЕ выбор канала для созвона (Zoom/TG/WhatsApp/Meet во время брони —
там работает scheduling.detect_call_medium). Поэтому в main.py детект зовётся
ТОЛЬКО когда активной брони/выбора слота нет.
"""
from __future__ import annotations

import re
from typing import Optional

# Телефон: +7..., 8(900)..., с пробелами/скобками/дефисами. Минимум ~10 цифр.
_PHONE_RE = re.compile(r"(\+?\d[\d\-\s()]{8,}\d)")

# (label, regex). label — как назовём канал в ответе/задаче.
_CHANNELS = [
    ("WhatsApp", r"(whats\s?app|вотс?ап\w*|ватс?ап\w*|ва[цс]ап\w*|вотсап\w*)"),
    ("Viber",    r"(viber|вайбер\w*)"),
    ("Signal",   r"(\bsignal\b|сигнал\w*)"),
    ("телефон",  r"(по\s+телефон\w*|позвон\w+|перезвон\w+|звонк\w+|набер\w+\s+(мне|меня)|на\s+телефон\w*|по\s+номеру)"),
]

# «не сижу/не пользуюсь телеграмом», «нет телеграма» → лид хочет другой канал, но не назвал какой.
_NO_TG = re.compile(
    r"(не\s+(сижу|пользу\w+|использу\w+|люблю|захожу)\s+\w*\s*(в\s+)?(телеграм\w*|telegram|тг)\b"
    r"|нет\s+(у\s+меня\s+)?(телеграм\w*|telegram|тг)\b"
    r"|(телеграм\w*|telegram|тг)\s+не\s+(использу\w+|пользу\w+|сижу|захожу))",
    re.IGNORECASE,
)


def detect_alt_channel(text: Optional[str]) -> Optional[tuple[str, Optional[str]]]:
    """Вернуть (label, contact|None) если лид просит другой канал связи, иначе None.

    contact — извлечённый телефон/номер, если есть в этом сообщении.
    """
    if not text:
        return None
    t = text.lower().replace("ё", "е")
    for label, pat in _CHANNELS:
        if re.search(pat, t):
            ph = _PHONE_RE.search(text)
            return (label, ph.group(1).strip() if ph else None)
    if _NO_TG.search(t):
        return ("другой канал", None)
    return None
