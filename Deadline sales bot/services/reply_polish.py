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

NAME_ASK = ("как вас зовут", "как к вам обращаться", "ваше имя")
EMAIL_ASK = ("email", "почт", "куда написать", "куда удобнее", "куда продублировать")


def _sentences(text):
    return [p for p in re.split(r"(?<=[.!?…])\s+", (text or "").strip()) if p.strip()]


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
    contact_redundant = name_known and email_known
    kept = []
    for s in _sentences(answer):
        low = s.lower()
        is_q = s.rstrip().endswith("?")
        if is_q and any(w in low for w in SPECIFICS_MARKERS):
            continue
        if is_q and contact_redundant and (
            any(w in low for w in NAME_ASK) or any(w in low for w in EMAIL_ASK)
        ):
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
