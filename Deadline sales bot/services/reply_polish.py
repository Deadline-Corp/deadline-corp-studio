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

# Ложное «записал ваш телеграм/контакт» — на сайте бот НЕ получает ник из фразы
# «я напишу в телеграм», значит обещание ложное. Вырезаем (если лид не дал @ник).
FALSE_RECORD = (
    "записал ваш телеграм", "записала ваш телеграм", "записал ваш телеграмм",
    "записал ваш тг", "записала ваш тг", "записал ваш контакт", "записала ваш контакт",
    "сохранил ваш телеграм", "записал ваш ник", "записал ваш аккаунт",
)

# Повторный пуш «перейдём/продолжим в telegram», когда лид УЖЕ согласился сам туда написать.
TG_REPUSH = (
    "давайте продолжим в telegram", "давайте продолжим в телеграм", "продолжим в telegram",
    "продолжим в телеграм", "давайте перейдём в telegram", "давайте перейдем в telegram",
    "перейдём в telegram", "перейдем в telegram", "перейти в telegram", "давайте в telegram",
    "продолжить в telegram", "перейдём в телеграм", "перейдем в телеграм",
)


def lead_going_to_tg(lead_message):
    """Лид сам сказал, что напишет/перейдёт в Telegram (тогда повторно туда не зовём)."""
    lm = (lead_message or "").lower()
    if not re.search(r"телеграм|telegram|\bтг\b|\bтелег\b", lm):
        return False
    return bool(re.search(r"напиш|перейд|перейт|зайд|буд|свяж|пишу|\bсам\b|\bок\b|хорош|давай|\bда\b", lm))


# Слова, которые НЕ имя (на случай «меня зовут просто Иван» / шумовых совпадений).
_NAME_STOP = {
    "хочу", "нужен", "нужна", "нужно", "просто", "сейчас", "тут", "здесь", "буду",
    "могу", "готов", "готова", "это", "как", "что", "так", "вот", "ну", "мне",
}
# Якоримся ТОЛЬКО на явные фразы-представления, чтобы не ловить ложное из
# «я хочу сайт». Имя — одно слово (кириллица/латиница, 2–30 букв), регистр любой
# (юзеры часто пишут строчными «меня зовут мария»). Берём первое имя, нормализуем.
_NAME_ANCHOR_RE = re.compile(
    r"(?:меня\s+зовут|меня\s+звать|мо[её]\s+имя\s*[-—:]?|зовите\s+меня|обращайтесь\s+ко\s+мне)\s+"
    r"([A-Za-zА-Яа-яЁё]{2,30})",
    re.IGNORECASE,
)


def extract_lead_name(text):
    """Детерминированно вынуть имя лида из реплики («меня зовут X», «моё имя X»,
    «зовите меня X»). Возвращает Имя (нормализованный регистр) или None.

    Зачем: llama-классификатор кладёт lead_name в handoff_data НЕНАДЁЖНО (часто
    None даже когда лид явно представился) — ловим кодом, как остальные хард-гарды.
    """
    if not text:
        return None
    m = _NAME_ANCHOR_RE.search(text)
    if not m:
        return None
    w = m.group(1)
    if w.lower() in _NAME_STOP:
        return None
    return w[:1].upper() + w[1:].lower()


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


# Переспрос «куда/на какой telegram·email вам написать» — в МЕССЕНДЖЕРЕ бессмыслица:
# мы уже в чате с лидом (chat_id есть), писать ему есть куда. Бот в живом тесте в
# телеге спросил «на какой email или telegram удобнее написать?». Вырезаем ТОЛЬКО
# эту клаузу (с ведущим союзом «и/,»), сохраняя остальное предложение — напр.
# «…как вас зовут и на какой telegram написать?» → «…как вас зовут?».
_CHANNEL_REASK_RE = re.compile(
    r"(?:\s*(?:,|;|—|–|-|\bи\b)\s*)?"
    r"(?:на\s+как(?:ой|ое)\s+(?:e-?mail|email|почт\w*|telegram|телеграм|тг)"
    r"(?:\s+или\s+(?:telegram|телеграм|e-?mail|email|почт\w*))?"
    r"|куда(?:\s+вам)?(?:\s+удобн\w+)?)"
    r"[^?.!]{0,40}?(?:написать|продублир\w+|связ\w+|скинуть)",
    re.IGNORECASE,
)


def _strip_channel_clause(s):
    """Убрать клаузу «куда/на какой telegram·email написать» (для мессенджера)."""
    out = _CHANNEL_REASK_RE.sub("", s)
    # Подчистить висящие союзы/пробелы перед финальным знаком.
    out = re.sub(r"\s*(?:,|;|—|–|-|\bи\b)\s*([?!.…])", r"\1", out)
    out = re.sub(r"\s+([?!.…])", r"\1", out)
    out = re.sub(r"\s{2,}", " ", out).strip()
    return out


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


def drop_bad_questions(answer, *, name_known=False, email_known=False,
                       tg_handle_given=False, lead_to_tg=False, on_messenger=False,
                       suppress_tg_push=False):
    """Убрать вопросы-выпытывания, повтор контакта, ложное «записал ваш телеграм»
    и повторный пуш в telegram, когда лид уже сам согласился туда написать.

    suppress_tg_push=True: лид попросил ДРУГОЙ канал (WhatsApp/телефон) → вырезаем
    любые «перейдём/продолжим в Telegram / @deadline_corp», чтобы бот не звал в ТГ
    наперекор просьбе."""
    if not answer:
        return answer
    kept = []
    for s in _sentences(answer):
        # Сначала вырезаем вшитую клаузу-зонд («… и как считать наценку …»),
        # затем решаем по очищенному предложению.
        s = _strip_probe_clause(s)
        # В мессенджере «куда/на какой telegram·email вам написать?» — бессмыслица
        # (мы уже в чате). Вырезаем клаузу; если предложение состояло только из неё —
        # оно станет пустым и отсеется ниже.
        if on_messenger:
            s = _strip_channel_clause(s)
            if not s.strip() or s.strip() in {"?", ".", "!", "…"}:
                continue
        low = s.lower()
        is_q = s.rstrip().endswith("?")
        # Ложное «записал ваш телеграм/контакт» — лид ника не давал.
        if not tg_handle_given and any(w in low for w in FALSE_RECORD):
            continue
        # Повторный пуш в telegram, когда лид уже сказал, что сам туда напишет.
        if lead_to_tg and any(w in low for w in TG_REPUSH):
            continue
        # Лид попросил другой канал → вырезаем любой пуш в Telegram/@deadline_corp.
        if suppress_tg_push and ("@deadline_corp" in low or any(w in low for w in TG_REPUSH)):
            continue
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


def limit_questions(answer, max_questions=1):
    """Анти-перегруз: оставить не более N вопросов (последних). Стейтменты не трогаем.

    Лид в живом тесте получал по 3 вопроса за реплику. Обычно последний вопрос —
    нужный CTA (имя/email/созвон), а лишние зонды-переспросы идут раньше → их и режем.
    """
    if not answer:
        return answer
    sents = _sentences(answer)
    q_idx = [i for i, s in enumerate(sents) if s.rstrip().endswith("?")]
    if len(q_idx) <= max_questions:
        return answer
    keep_q = set(q_idx[-max_questions:])
    out = [s for i, s in enumerate(sents) if not s.rstrip().endswith("?") or i in keep_q]
    return " ".join(out).strip()


def polish(answer, *, lead_message="", is_first_turn=False,
           name_known=False, email_known=False, channel="", suppress_tg_push=False):
    """Применить все гарды. Если вырезали всё — вернуть версию после greeting-фикса
    (пустой ответ хуже неидеального).

    suppress_tg_push: лид попросил другой канал → не пушим Telegram/@deadline_corp."""
    if not answer:
        return answer
    # Лид реально дал @ник (тогда «записал ваш телеграм» — правда, не трогаем).
    tg_handle_given = bool(re.search(r"@[A-Za-z0-9_]{3,}", lead_message or ""))
    lead_to_tg = lead_going_to_tg(lead_message)

    # Контакт дан ПРЯМО в этом сообщении? Тогда переспрашивать его — глупость
    # (extraction в БД срабатывает после генерации ответа, поэтому name_known/
    # email_known из customer ещё False на этом ходе). Детектим в тексте.
    _msg = lead_message or ""
    if re.search(r"[\w.+-]+@[\w-]+\.[A-Za-z]{2,}", _msg):
        email_known = True
    # Имя в начале сообщения. Ловим «Имя,» / «Имя.» / «Имя <email>» / «Имя Фамилия»
    # (лид часто пишет «Александр alexandr@mail.ru» БЕЗ запятой — раньше не ловилось
    # → бот переспрашивал имя/email, которые только что дали). НЕ ловим одиночное
    # слово вроде «Привет» (нет запятой/email/второго имени → не сработает).
    if re.match(
        r"^\s*[А-ЯЁA-Z][а-яёa-z]{1,}(\s*[,.]|\s+[\w.+-]+@[\w-]+\.[A-Za-z]{2,}|\s+[А-ЯЁA-Z][а-яёa-z]+)",
        _msg,
    ):
        name_known = True
    if re.search(r"(меня\s+зовут|зовут\s+меня|\bзовут\b|\bмо[её]\s+имя\b|\bимя\s*[:—-])\s*[А-ЯЁA-Z]", _msg):
        name_known = True
    on_messenger = bool(channel) and str(channel).lower() != "website"
    out = mirror_greeting(answer, lead_message, is_first_turn)
    cleaned = drop_bad_questions(
        out, name_known=name_known, email_known=email_known,
        tg_handle_given=tg_handle_given, lead_to_tg=lead_to_tg,
        on_messenger=on_messenger, suppress_tg_push=suppress_tg_push,
    )
    cleaned = limit_questions(cleaned, max_questions=1)
    return cleaned or out
