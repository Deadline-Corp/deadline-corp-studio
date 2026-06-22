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

## Устойчивость: смена воронки/ниши НЕ теряет карточки (Фаза 4, 2026-06-16)

`funnel_store.migrate_orphaned_leads(db)` — ГАРАНТИЯ: после ЛЮБОГО изменения набора стадий
(правка воронки, сброс на заводские, смена пресета ниши) активные диалоги, чья стадия больше
не существует, **автоматически переезжают на безопасную стадию** (первая активная нового набора),
с логом `StageTransition` (`by='funnel-migrate'`). Терминальные `lost`/`completed_won` и legacy-ключи
(`nda/tz_approved/in_work/post_sale`) НЕ трогаются (валидны как финальная стадия). Раньше такие лиды
«сиротели» в «Прочее» и были недоступны на канбане — теперь этого не происходит.

Вызывается в `POST /funnel/stages`, `POST /funnel/stages/reset`, `POST /presets/apply` — в той же
транзакции, ПОСЛЕ авто-снимка конфигурации (откат в «Версии конфигурации»). Ответы содержат
`migrated: {migrated, by_stage, fallback}`; UI (Воронка/Пресеты) показывает «N карточек безопасно
перенесено». `GET /funnel/stages/usage` — счётчики карточек на стадиях (для предупреждений).

## Гигиена базы: убрать «Не сложилось» в архив (Фаза 5, 2026-06-16)

`funnel_store.archive_lost_leads(db, older_than_days=None)` — lost-карточки → `status=ARCHIVED`,
чтобы старая база не засоряла канбан/инбокс/задачник. **ОБРАТИМО** (не удаляем — never-delete):
карточки сохраняются, видны в экспорте (`leads/conversations.csv`) и в «Переписках» с
`include_archived`. Полезных от мусорных отличает `lost_reason` (hard_stop = отказ/ошибся).
`POST /funnel/archive-lost` (кнопка «🧹 Убрать «Не сложилось» (N)» в Воронке, + авто-снимок + гашение
осиротевших задач), `GET /funnel/lost-count`. Опц. авто-архивация в кроне при
`bot_settings.lost_auto_archive_days > 0` (молчащие lost старше N дней).

## Грабли / бэклог
- Воронка ещё «не отточена» (слова владельца) → дашборд конверсии отложен.
- Лимит списка 1000, фильтры по каналу/стадии/температуре, чип «застрял >7дн».
