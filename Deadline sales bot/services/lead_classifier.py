"""WhatsApp chat triage — лид vs не-лид (2026-06-14).

Зачем: личный номер в WhatsApp Business держит ВПЕРЕМЕШКУ деловые обращения
(потенциальные клиенты студии: сайт/бот/автоматизация/AI) и личные чаты
(друзья, семья, доставка, банк, спам). При импорте всех переписок из WAHA
(services/whatsapp_sync.py) систему нужно научить отличать ЛИДА от НЕ-лида,
чтобы воронка/карточки не засорялись личной перепиской, а оператор сразу
видел, где деньги.

Контракт classify_whatsapp_conversation():
  вход  — транскрипт диалога (text), имя контакта, метки WhatsApp (если есть),
          профиль ниши/компании (tenant), langchain-LLM (handoff_llm из main).
  выход — dict {is_lead, confidence(0..1), category, reason, temperature}.

LLM — основной путь (читает смысл). Эвристика — фоллбэк, если LLM недоступен
или вернул мусор: ключевые слова услуг → лид; явные личные/сервисные маркеры
→ не-лид. Никаких побочных эффектов, БД не трогает — только классификация.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

log = logging.getLogger(__name__)

# Деловые маркеры (намерение / предмет услуг студии). RU + EN.
_BUSINESS_KEYWORDS = (
    "сайт", "лендинг", "веб", "приложени", "бот", "чат-бот", "автоматиз",
    "интеграц", "crm", "црм", "telegram бот", "ai", "ии", "нейросет", "mvp",
    "разработ", "проект", "техзадан", "тз", "смета", "бюджет", "стоимост",
    "цена", "сколько стоит", "прайс", "запуск", "доработ", "api", "дашборд",
    "магазин", "e-commerce", "ecommerce", "автоматизировать", "заявк", "лид",
    "website", "landing", "web app", "chatbot", "automation", "integration",
    "develop", "build a", "quote", "estimate", "budget", "price", "how much",
    "project", "deadline", "хочу заказать", "нужен сайт", "нужна разработка",
)

# Явно НЕ-лид: личное / служебное / спам.
_NONLEAD_KEYWORDS = (
    "мам", "пап", "сын", "дочь", "брат", "сестр", "люблю", "соскучил",
    "поздравля", "с днём рожден", "доставк", "заказ готов", "ваш код",
    "verification code", "код подтвержд", "банк", "карта заблок", "акция",
    "скидк", "розыгрыш", "не беспокоить", "отписаться", "unsubscribe",
    "delivery", "your code", "otp",
)


def _heuristic(transcript: str, contact_name: str = "") -> dict:
    """Дешёвый детерминированный фоллбэк без LLM."""
    t = (transcript or "").lower()
    biz = sum(1 for k in _BUSINESS_KEYWORDS if k in t)
    non = sum(1 for k in _NONLEAD_KEYWORDS if k in t)
    if biz == 0 and non == 0:
        # Нет сигналов — мягкий «возможно лид» с низкой уверенностью,
        # чтобы оператор глянул сам, а не потерял.
        return {
            "is_lead": True, "confidence": 0.3, "category": "unknown",
            "reason": "Нет явных деловых или личных маркеров (эвристика).",
            "temperature": "cold", "by": "heuristic",
        }
    is_lead = biz >= non
    conf = min(0.85, 0.5 + 0.1 * abs(biz - non))
    return {
        "is_lead": is_lead,
        "confidence": round(conf, 2),
        "category": "service_inquiry" if is_lead else "personal_or_other",
        "reason": f"Эвристика: деловых маркеров {biz}, личных {non}.",
        "temperature": "warm" if (is_lead and biz >= 2) else "cold",
        "by": "heuristic",
    }


def _coerce(result: dict, fallback: dict) -> dict:
    """Привести ответ LLM к строгой форме, добив недостающее из фоллбэка."""
    out = dict(fallback)
    try:
        out["is_lead"] = bool(result.get("is_lead", fallback["is_lead"]))
        c = result.get("confidence", fallback["confidence"])
        out["confidence"] = max(0.0, min(1.0, float(c)))
        out["category"] = str(result.get("category") or fallback["category"])[:40]
        out["reason"] = str(result.get("reason") or fallback["reason"])[:500]
        temp = str(result.get("temperature") or fallback["temperature"]).lower()
        out["temperature"] = temp if temp in ("cold", "warm", "hot", "frozen") else "cold"
        out["by"] = "llm"
    except (TypeError, ValueError) as e:  # noqa: BLE001
        log.warning(f"lead_classifier coerce failed: {e} — using heuristic")
        return fallback
    return out


def _extract_json(text: str) -> Optional[dict]:
    """Вытащить первый JSON-объект из ответа LLM (терпим к ```json и префиксам)."""
    if not text:
        return None
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def classify_whatsapp_conversation(
    *,
    llm: Any,
    transcript: str,
    contact_name: str = "",
    wa_labels: Optional[list] = None,
    business_context: str = "",
) -> dict:
    """Классифицировать WhatsApp-диалог как лид/не-лид.

    `llm` — langchain ChatOpenAI (обычно handoff_llm). Если None или падает —
    возвращаем эвристику. `wa_labels` — метки WhatsApp Business на чате (если
    WAHA их отдаёт) — сильный сигнал для бизнес-меток.
    `business_context` — короткое описание ниши/студии для контекста.
    """
    fallback = _heuristic(transcript, contact_name)

    # Метки WhatsApp Business — почти однозначный сигнал «это клиент».
    label_names = [str(x.get("name") if isinstance(x, dict) else x) for x in (wa_labels or [])]
    label_hint = ""
    if label_names:
        label_hint = f"\nМЕТКИ WhatsApp Business на чате: {', '.join(label_names)}"

    if llm is None or not (transcript or "").strip():
        if label_names:
            fallback["is_lead"] = True
            fallback["confidence"] = max(fallback["confidence"], 0.7)
            fallback["reason"] += f" + метки WhatsApp: {', '.join(label_names)}"
        return fallback

    ctx = business_context or (
        "DEADLINE — студия разработки: сайты/лендинги, веб-приложения, "
        "автоматизация (CRM, интеграции, боты), AI-агенты. Клиенты — "
        "предприниматели и компании, которым нужна разработка под ключ."
    )
    prompt = (
        "Ты сортируешь чаты WhatsApp владельца бизнеса. По переписке реши, "
        "это ДЕЛОВОЙ ЛИД/КЛИЕНТ (человек интересуется услугами, обсуждает "
        "проект, цену, ТЗ, заказ) или НЕ ЛИД (личный чат, друзья/семья, "
        "доставка, банк, коды, спам, рассылка).\n\n"
        f"Контекст бизнеса: {ctx}\n"
        f"Имя/контакт: {contact_name or '—'}{label_hint}\n\n"
        f"ПЕРЕПИСКА (последние сообщения):\n{transcript[:4000]}\n\n"
        "Верни СТРОГО один JSON-объект без пояснений:\n"
        '{"is_lead": true|false, "confidence": 0.0-1.0, '
        '"category": "service_inquiry|existing_client|partner|personal|service_msg|spam|unknown", '
        '"reason": "1 фраза почему", "temperature": "cold|warm|hot"}'
    )
    try:
        from langchain_core.messages import HumanMessage
        raw = llm.invoke([HumanMessage(content=prompt)]).content
        parsed = _extract_json(raw)
        if parsed is None:
            log.warning("lead_classifier: LLM returned no JSON — heuristic fallback")
            return fallback
        result = _coerce(parsed, fallback)
        # Метки усиливают «лид», даже если LLM колебался.
        if label_names and not result["is_lead"]:
            result["reason"] += f" (но есть метки WhatsApp: {', '.join(label_names)})"
        return result
    except Exception as e:  # noqa: BLE001
        log.warning(f"lead_classifier LLM failed: {e} — heuristic fallback")
        return fallback
