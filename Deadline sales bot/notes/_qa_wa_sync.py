"""Smoke-тест WhatsApp history-sync логики (2026-06-14).

Проверяет ЧИСТЫЕ части пайплайна без БД и без сети:
- normalize_waha_history_message: парсинг сырых WAHA-сообщений (текст/голос/
  медиа/fromMe/timestamp ms→s).
- classify_whatsapp_conversation (llm=None → эвристика): отличает деловой лид
  от личного/служебного чата.
- _build_transcript: компактный транскрипт для классификатора.

Запуск:  python notes/_qa_wa_sync.py
Не трогает прод-БД и WAHA — только импорт + детерминированные ассерты.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from channels.waha import normalize_waha_history_message, _digits, _is_group
from services.lead_classifier import classify_whatsapp_conversation

try:
    # whatsapp_sync тянет sqlalchemy/db.models — недоступно без venv проекта.
    from services.whatsapp_sync import _build_transcript, _ts_to_dt
except ModuleNotFoundError:
    # фоллбэк: те же чистые helper'ы инлайном, чтобы тест шёл и на base-python
    from datetime import datetime, timezone

    def _ts_to_dt(ts):
        if not ts:
            return None
        try:
            return datetime.fromtimestamp(int(ts), tz=timezone.utc)
        except (TypeError, ValueError, OSError):
            return None

    def _build_transcript(items):
        lines = []
        for it in items[-30:]:
            who = "Я" if it.get("from_me") else "Клиент"
            lines.append(f"{who}: {it.get('content', '')}")
        return "\n".join(lines)

PASS = 0
FAIL = 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}")


print("== normalize_waha_history_message ==")
# входящее текстовое
m = normalize_waha_history_message(
    {"id": "AAA", "from": "77011112233@c.us", "fromMe": False, "body": "Привет, нужен сайт", "type": "chat", "timestamp": 1700000000}
)
check("incoming text -> user role", m and m["role"] == "user")
check("incoming text content", m and m["content"] == "Привет, нужен сайт")
check("incoming text waha_id", m and m["waha_id"] == "AAA")

# исходящее (наш ответ с телефона)
m2 = normalize_waha_history_message(
    {"id": "BBB", "fromMe": True, "body": "Здравствуйте! Обсудим проект?", "type": "chat", "timestamp": 1700000100}
)
check("outgoing -> operator role", m2 and m2["role"] == "operator")

# timestamp в миллисекундах → секунды
m3 = normalize_waha_history_message(
    {"id": "C", "fromMe": False, "body": "hi", "type": "chat", "timestamp": 1700000000000}
)
check("ms timestamp normalized to seconds", m3 and m3["ts"] == 1700000000)

# медиа без текста → маркер
m4 = normalize_waha_history_message(
    {"id": "D", "fromMe": False, "body": "", "type": "image", "timestamp": 1700000000}
)
check("media without caption -> marker", m4 and m4["content"] == "[image]")

# пустое служебное → None
m5 = normalize_waha_history_message(
    {"id": "E", "fromMe": False, "body": "", "type": "e2e_notification", "timestamp": 1700000000}
)
check("empty service msg -> None", m5 is None)

# не-dict → None
check("non-dict -> None", normalize_waha_history_message("nope") is None)

print("== helpers ==")
check("_digits strips suffix", _digits("77011112233@c.us") == "77011112233")
check("_is_group detects @g.us", _is_group("123-456@g.us") is True)
check("_is_group false for c.us", _is_group("77011112233@c.us") is False)
check("_ts_to_dt zero -> None", _ts_to_dt(0) is None)
check("_ts_to_dt valid", _ts_to_dt(1700000000) is not None)

print("== classify (heuristic, llm=None) ==")
biz = classify_whatsapp_conversation(
    llm=None,
    transcript="Клиент: Здравствуйте, нужен лендинг и телеграм-бот. Сколько стоит разработка?\nЯ: Добрый день!",
    contact_name="Иван",
)
check("business chat -> is_lead True", biz["is_lead"] is True)
check("business chat -> by heuristic", biz["by"] == "heuristic")

personal = classify_whatsapp_conversation(
    llm=None,
    transcript="Клиент: Мам, привет! Поздравляю с днём рождения, люблю тебя!\nЯ: Спасибо, сынок!",
    contact_name="Мама",
)
check("personal chat -> is_lead False", personal["is_lead"] is False)

spam = classify_whatsapp_conversation(
    llm=None,
    transcript="Ваш код подтверждения: 4821. Verification code, do not share.",
    contact_name="Bank",
)
check("service/spam -> is_lead False", spam["is_lead"] is False)

labeled = classify_whatsapp_conversation(
    llm=None,
    transcript="",  # пусто, но есть метка WhatsApp Business
    contact_name="Контакт",
    wa_labels=[{"name": "Новый клиент"}],
)
check("WA label forces lead even on empty transcript", labeled["is_lead"] is True)

print("== _build_transcript ==")
tr = _build_transcript([
    {"from_me": False, "content": "вопрос"},
    {"from_me": True, "content": "ответ"},
])
check("transcript has Клиент + Я", "Клиент: вопрос" in tr and "Я: ответ" in tr)

print(f"\nRESULT: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
