# Календарь, созвоны, часовые пояса

Бронь созвонов (ручная и авто из переписки), напоминания лиду и владельцу,
учёт часовых поясов, FullCalendar-вью, ICS-фид.

---

## Где код

| Слой | Файл |
|---|---|
| Слоты, часовые пояса, напоминания | `services/scheduling.py` |
| Задачи созвонов | `services/scheduled_actions.py` → `write_call_booking`, `write_call_reminder`, `cancel_call_actions`, `run_due_call_reminders` |
| Мозг — распознавание договорённости | `services/conversation_brain.py` → `_resolve_call_dt`, `_lead_silent`, `analyze_and_advance` |
| API (перенос/отмена из карточки) | `admin_api.py` → `POST /conversations/{id}/call` |
| API (подтвердить/отклонить предложение) | `admin_api.py` → `POST /conversations/{id}/call-suggestion` |
| API (FullCalendar) | `admin_api.py` → `GET /calendar-events` |
| ICS-фид | `main.py` → `GET /calendar.ics` |
| Ожидающие подтверждения | `admin_api.py` → `GET /whatsapp/pending-suggestions` |

---

## Предложение созвона (не авто-бронь!)

Бот распознаёт договорённость в переписке → кладёт `pending_call_suggestion` (JSON,
миграция 019). НЕ создаёт событие сам — ждёт человека.

**Поля `pending_call_suggestion`:**
```json
{
  "at": "2026-06-18T07:00:00Z",
  "when_human": "в среду в 14:00",
  "medium": "WhatsApp",
  "reason": "Лид написал «созвонимся в среду»"
}
```

**Зачем подтверждение:** LLM читает дату приблизительно (особенно «в среду утром»);
менеджер видит предложение и правит / отклоняет.

**Как видит менеджер:**
1. **Карточка** (`ConversationDrawer`) — блок «📅 Похоже, договорились о созвоне»
2. **Всплывающее уведомление** (`CallSuggestionToasts`) — поллит `GET /whatsapp/pending-suggestions` каждые 30с
   - «✅ Создать» → событие + убирается везде
   - «✕» → скрывает ТОЛЬКО всплывашку (в карточке остаётся)

**`POST /conversations/{id}/call-suggestion`:**
- `{"action": "confirm", "at": "..."}` → `_book(...)` = бронь + напоминания + стадия → `on_call`
- `{"action": "dismiss"}` → время dismiss записывается, бот не предложит снова по тому же эпизоду

---

## _resolve_call_dt — детерминированный расчёт даты

```python
def _resolve_call_dt(now_lead: datetime, call_day: str, call_time: str) -> datetime:
    """
    LLM возвращает НАЗВАНИЕ дня: 'today'/'tomorrow'/'mon'..'sun'.
    Функция сама вычисляет конкретную дату. LLM-арифметика дат исключена.
    """
```

- `call_day = 'wed'` + `call_time = '15:00'` + текущая дата → точный datetime в зоне лида
- Защита: если день уже прошёл на этой неделе → следующая неделя

---

## Защита от фантом-созвонов (детерминированные стражи)

Gemini 2.5 Flash переоценивает «договорённость о созвоне» — выдумывает день/час там,
где его нет. Поверх LLM-классификации (`call_agreed`) стоят **детерминированные стражи**
в `services/conversation_brain.py`. Предложение создаётся ТОЛЬКО если все условия:

| Страж | Что проверяет | Пример отлова |
|---|---|---|
| `_mentions_call` | В переписке РЕАЛЬНО есть слово про звонок/созвон | **Вячеслав**: «Ок. Отправлю в течение дня» — звонка нет → нет предложения |
| `_defers_timing` | Лид НЕ отложил называние времени | **Zaal**: «Хорошо сообщу время» / «как найду спонсора» → LLM выдумал «вторник 10:00» → подавляем |
| `_lead_silent` | Лид не молчит (мы написали последними) | «увидел КП и пропал» → дожим через next_action, не созвон |
| `_resolve_call_dt` | Назван конкретный день | нет дня → нет точного времени |

**Снятие призрака:** если предложение уже висит, а в переписке звонка нет ВООБЩЕ
(`not _mentions_call`) ИЛИ лид отложил время (`_defers_timing`) → `pending_call_suggestion`
сбрасывается на следующем проходе брейна. Подтверждённые брони (`booked_call_at`) не трогаются.

`_DEFER_TIME` — список фраз-отсрочек: «сообщу время», «напишу когда», «дам знать»,
«как найду», «определюсь», «позже скажу» и т.п.

---

## Хранение броней

- `customer.profile_data['booked_call_at']` — datetime в UTC
- `customer.profile_data['call_medium']` — WhatsApp / Telegram / Phone
- `conv.lead_stage = 'on_call'` при броне
- `scheduled_actions`: `call_booked` (факт; executor=human) + `call_reminder` (авто-напоминания)

---

## Напоминания

`run_due_call_reminders()` в кроне. Три напоминания: за 24ч / за 3ч / за 1ч:

**Лиду** (в мессенджер, на его языке):
```
lead_reminder_text(lang, medium, when_human_local)
# → "Добрый день! Напоминаем о нашем созвоне сегодня в 15:00"
```

**Владельцу** (в форум-топик или `TELEGRAM_OPERATOR_GROUP_ID`):
```
admin_reminder_text(customer_name, medium, when_admin_local, conv_link)
```

---

## Часовые пояса

`lead_tz_from_phone(phone)` → timezone (fixed offset):

| Код номера | Часовой пояс |
|---|---|
| 77... | Астана UTC+5 |
| 79..., 78... | Москва UTC+3 |
| 971... | Дубай UTC+4 |
| 995..., 374... | Тбилиси/Ереван UTC+4 |
| иначе | Пхукет UTC+7 (дефолт = пояс админа) |

**ВАЖНО (фикс 2026-06-16):** пояс лида определяется по **`cust.phone`** (реальный
номер), НЕ по `channel_conversation_id` — у рекламных лидов там скрытый `@lid` (не
телефон), из-за чего пояс падал в Пхукет даже для Астаны → бот ставил созвон на
неверный час. `conversation_brain.analyze_and_advance` теперь берёт `cust.phone`.

**Извлечение часа:** лид указывает время в СВОЁМ поясе. LLM возвращает `call_time`
точным часом: «после 18», «в 18:00», «в 6 вечера» → `"18:00"`; `_resolve_call_dt`
трактует его в поясе лида → UTC. Пример: «после 18 по Астане» (UTC+5) = 13:00 UTC =
**20:00 Пхукета** (раньше бот ошибочно ставил 14:00).

**Тумблер мультипояса** (`bot_settings.tz_multi`, Настройки → «🌍 Часовые пояса»):
- **ВКЛ** (деф., для DEADLINE) — учитывать пояс лида по номеру.
- **ВЫКЛ** — все времена в поясе админа (Пхукет UTC+7); для тех, кто работает в
  одном городе и не имеет лидов из других поясов.

Лиду время называется в ЕГО поясе («в 15:00 (время Астаны)»); владельцу/команде — по Пхукету.

---

## Ручное управление из карточки

```
POST /admin/api/conversations/{id}/call
  {"action": "reschedule", "at": "2026-06-20T09:00:00Z", "medium": "WhatsApp"}
  {"action": "cancel"}
```

`reschedule` → cancel старые `call_booked`/`call_reminder` → write_call_booking(new at).
`cancel` → cancel_call_actions(conv.id) → `profile_data['booked_call_at'] = None`.

---

## FullCalendar (Admin UI)

```
GET /admin/api/calendar-events?start=2026-06-01&end=2026-06-30
```

**Виды событий (kind):**

| kind | Иконка | Источник |
|---|---|---|
| `call` | 📞 | `scheduled_actions` action_type=call_booked OR `profile_data.booked_call_at` |
| `reminder` | ⏰ | `scheduled_actions` action_type=call_reminder |
| `bot` | 🤖 | `scheduled_actions` executor=bot (followup, warming) |
| `task` | 📋 | `scheduled_actions` executor=human |

**Дедуп созвонов по лиду:**
- Если у одного лида два диалога (например, @lid + телефон до дедупа)
- Ключ дедупа: цифры телефона → lowercase имя → id
- Один лид = один блок в календаре

---

## ICS-фид

```
GET /calendar.ics
```

- Подписка в Google Calendar: Другие календари → По URL → вставить ссылку
- Содержит: только будущие созвоны (`call_booked`) + напоминания (`call_reminder`)
- Обновляется при каждом запросе (нет кэша)
- Заголовок: `Content-Type: text/calendar; charset=utf-8`

---

## Грабли / бэклог

- **Двусторонняя Google-синхра** — не сделана (нужен OAuth владельца). Сейчас ICS-фид односторонний.
- **P5b**: формат даты в напоминаниях — `format_slot_human` пока RU-шаблон даже при lang=EN/TH.
- **Авто-пауза ночью** — расписание работы бота (off-hours) в очереди.
