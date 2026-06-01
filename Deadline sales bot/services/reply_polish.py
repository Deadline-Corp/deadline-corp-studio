# -*- coding: utf-8 -*-
"""Детерминированные пост-гарды на ответ бота (ЧИСТАЯ логика, только re+str).

Живой тест показал: llama-3.3-70b ненадёжно следует текстовым правилам и копирует
few-shot дословно. Поэтому ЖЁСТКИЕ ограничения держим КОДОМ поверх сгенерированного
ответа (как уже сделано для channel-guard `_msgr_leaks` и анти-повтора).

Покрывает три косяка, которые правила не починили:
1. Приветствие-зеркало: лид «Здравствуйте» → бот не должен открывать «Привет».
2. Не выпытывать: вырезать ВОПРОСЫ про наценку/маржу/бюджет/процент/частоту/тех-стек.
3. Не переспрашивать уже данные имя/email (когда оба контакта уже есть).
"""
import re

FORMAL_GREETINGS = (
    "здравствуйте", "добрый день", "добрый вечер", "доброе утро", "доброго времени",
)

# Если ВОПРОС бота содержит это — он лезет в детали, которые не его. Вырезаем.
SPECIFICS_MARKERS = (
    "наценк", "маржу", "маржа", "маржи", "бюджет", "процент",
    "как часто", "частот обновл", "тех-стек", "техническ детал",
)

NAME_ASK = ("как вас зовут", "как к вам обращаться", "ваше имя", "подскажите имя", "представьтесь")
# Только ASK-фразы (не ловим statements вроде «на email продублируем»).
EMAIL_ASK = (
    "на какой email", "какой email", "ваш email", "оставьте email", "скиньте email",
    "пришлите email", "куда написать", "куда удобнее написать", "куда продублировать",
    "email или telegram", "оставьте контакт", "ваш контакт",
)


def _sentences(text):
    return [p for p in re.split(r"(?<=[.!?…])\s+", (text or "").strip()) if p.strip()]


# Вшитая клауза-зонд вида «… и как считать наценку …» / «… и какой процент …».
# Модель часто лепит её ВНУТРЬ нормального предложения (без «?»), поэтому
# вырезаем ТОЛЬКО саму клаузу, оставляя остальной текст. Якорь « и <вопрос-слово> …
# <маркер> » не задевает statements вроде «своя наценка и автообновление».
_PROBE_CLAUSE = re.compile(
    r"[,\s]+и\s+(?:как(?:ой|ая|ую|ие)?|сколько|на\s+как(?:ой|ую))\b"
    r"[^—.,!?]*?\b(?:наценк\w*|маржу|маржа|маржи|процент\w*|бюджет\w*)\w*",
    re.IGNORECASE,
)


def _strip_probe_clause(s):
    """Убрать вшитую клаузу-зонд про наценку/маржу/процент/бюджет, сохранив предложение."""
    return _PROBE_CLAUSE.sub("", s)


def mirror_greeting(answer, lead_message, is_first_turn):
    """Первый ход + лид поздоровался формально, а бот открыл «Привет» → «Здравствуйте»."""
    if not is_first_turn or not answer:
        return answer
    lm = (lead_message or "").lower()
    if not any(g in lm for g in FORMAL_GREETINGS):
        return answer
    m = re.match(r"^\s*привет\s*[!,.…]*\s*", answer, flags=re.IGNORECASE)
    if m:
        rest = answer[m.end():].lstrip()
        return ("Здравствуйте! " + rest) if rest else "Здравствуйте!"
    return answer


def drop_bad_questions(answer, *, name_known=False, email_known=False):
    """Убрать вопросы-выпытывания и повторный запрос уже данных имени+email."""
    if not answer:
        return answer
    kept = []
    for s in _sentences(answer):
        # Сначала вырезаем вшитую клаузу-зонд («… и как считать наценку …»),
        # затем решаем по очищенному предложению.
        s = _strip_probe_clause(s)
        low = s.lower()
        is_q = s.rstrip().endswith("?")
        # Выпытывание деталей — только если это ВОПРОС (statements про наценку оставляем).
        if is_q and any(w in low for w in SPECIFICS_MARKERS):
            continue
        # Повторный запрос контакта — даже БЕЗ «?» («Расскажите, как вас зовут…»).
        # Вырезаем предложение, только если ВСЕ запрашиваемые в нём данные уже есть.
        asks_name = any(w in low for w in NAME_ASK)
        asks_email = any(w in low for w in EMAIL_ASK)
        if asks_name or asks_email:
            name_ok = (not asks_name) or name_known
            email_ok = (not asks_email) or email_known
            if name_ok and email_ok:
                continue
        kept.append(s)
    return " ".join(kept).strip()


def polish(answer, *, lead_message="", is_first_turn=False,
           name_known=False, email_known=False):
    """Применить все гарды. Если вырезали всё — вернуть версию после greeting-фикса
    (пустой ответ хуже неидеального)."""
    if not answer:
        return answer
    out = mirror_greeting(answer, lead_message, is_first_turn)
    cleaned = drop_bad_questions(out, name_known=name_known, email_known=email_known)
    return cleaned or out
