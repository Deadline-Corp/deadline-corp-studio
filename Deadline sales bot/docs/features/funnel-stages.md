# Фича: Воронка и стадии

## Что делает
Стадии лида, авто-движение по смыслу переписки, классификация лид/не-лид.

## Файлы кода
- `services/funnel_store.py` — `BUILTIN_STAGES` / `BUILTIN_KEYS`.
- `services/lead_classifier.py` — `classify_whatsapp_conversation` (лид/не-лид +
  категория + температура + стадия; Gemini + эвристика).
- `services/funnel.py` — стейт-машина (silence→lost и т.д.).
- `services/conversation_brain.py` — авто-движение вперёд (`_FORWARD`).
- `admin_api.py` — `/conversations/{id}/stage` (ручная смена), `/funnel*`.

## Стадии (lead_stage)
`new_lead` 🆕 → `in_dialog` 💬 → `qualified` ✅ → `on_call` 📞 → `proposal` 📄 →
`prepayment` 💰 → `completed_won` 🏁 · `lost` ❌.

`qualified` — ТОЛЬКО когда лид описал конкретную задачу проекта (не на «привет/
расскажите подробнее» — это `in_dialog`).

## Кто двигает
- Мозг (`analyze_and_advance`) — вперёд, по смыслу (никогда назад). См. autopilot-and-brain.md.
- Классификатор при импорте/синке — только из дефолтного `new_lead`.
- Оператор вручную — «Сменить стадию» / drag в Воронке.
- Зеркало в HubSpot (если `crm_enabled`) для builtin-ключей.

## Температура
`cold/warm/hot/ready/client/frozen` — по сигналам + decay при молчании (`services/temperature.py`).

## Грабли / бэклог
- Воронка ещё «не отточена» (слова владельца) → дашборд конверсии отложен.
- Лимит списка 1000, фильтры по каналу/стадии/температуре, чип «застрял >7дн».
