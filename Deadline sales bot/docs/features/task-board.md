# Задачник (Task Board)

Центральный CRM-инструмент «что делать прямо сейчас». Агрегирует все отложенные задачи
и лидов без задачи в единый приоритизированный список.

---

## Где код

| Слой | Файл / путь |
|---|---|
| API | `admin_api.py` → `GET /admin/api/task-board`, `POST /admin/api/task-board/generate` |
| Задачи (хранение) | `services/scheduled_actions.py` |
| Умный следующий шаг | `services/next_action.py` |
| UI | `admin-ui/src/pages/TaskBoard.tsx` |
| Модели | `db/models.py` → `ScheduledAction`, `Conversation.next_action` |

---

## Как работает

### GET /admin/api/task-board

Возвращает структуру с двумя разделами:

**1. Бакеты срочности** — только pending/processing `scheduled_actions`:

| Бакет | Когда попадает |
|---|---|
| `overdue` | `due_at < now` |
| `today` | `due_at` сегодня |
| `tomorrow` | `due_at` завтра |
| `week` | `due_at` ≤ следующее воскресенье |
| `later` | всё остальное pending |

Сортировка внутри бакета: `lead_temperature` (hot>warm>cold) × 100 + позиция стадии в воронке.

**2. «Лиды без задачи»** (`no_task_leads`):
- Диалоги с активной стадией (не lost/archived) и БЕЗ pending scheduled_action
- Если `conv.next_action` заполнен — возвращается `label`, `mode`, `draft`, `reason`
- Сортировка: `unclear` вверху → `human` → `reengage` → прочие → без next_action

### POST /admin/api/task-board/generate (owner only)

Запускает мозг для всех лидов без задачи (до 20 за раз):
1. Собирает candidates: активные диалоги без pending задачи
2. Каждый кандидат обрабатывается в **собственной короткой session_scope** (anti-vis — коннект отпускается между вызовами)
3. Вызывает `next_action.generate_next_action(db, conv, cust, llm)` для каждого
4. Результат сохраняется в `conv.next_action` (JSONB)

Возвращает: `{"generated": N, "candidates": M}`.

---

## Структура next_action (JSONB в conversations)

```json
{
  "kind": "reengage",
  "mode": "needs_approval",
  "label": "Напомнить о КП",
  "draft": "Добрый день! Хотел уточнить...",
  "reason": "Лид замолчал 3 дня назад после отправки КП"
}
```

| Поле | Значение |
|---|---|
| `kind` | `reengage / answer / human / wait / unclear` |
| `mode` | `bot_auto / needs_approval / human / wait / unclear` |
| `label` | Короткая метка для UI (одна строка) |
| `draft` | Готовый черновик сообщения (если mode=needs_approval) |
| `reason` | Пояснение для оператора |

**mode=bot_auto** — только если `conv.wa_autonomous==True` (per-conv разрешение «Бот ведёт сам»).  
**mode=needs_approval** — черновик попадает в `pending_wa_draft`; оператор одобряет/отклоняет из карточки.

---

## Связанные эндпоинты

| Метод | Путь | Назначение |
|---|---|---|
| GET | `/admin/api/today` | «Мой день»: overdue + today + created + upcoming созвоны (дедуп по телефону) |
| POST | `/admin/api/tasks` | Создать задачу вручную (executor=bot или human) |
| POST | `/admin/api/scheduled-actions/{id}/done` | Отметить human-задачу выполненной |
| POST | `/admin/api/scheduled-actions/{id}/reschedule` | Перенести задачу |
| POST | `/admin/api/scheduled-actions/{id}/cancel` | Отменить задачу |

---

## Флаги и настройки

Задачник читает BotSetting-оверрайды (TTL-кэш 60с) для порогов:
- `nudge_hours_min` — минимум часов между ботовыми пинками
- `warming_cadence_*` — кадэнс прогрева (для warming_touch задач)
- `score_decay_*` — насколько быстро падает температура (влияет на приоритет)

---

## Спящие лиды (дожим) — `Tasks.tsx → SleepingPanel`

«💤 Спящие» = активные whatsapp-карточки, молчащие дольше `hours` (деф. 24) после нашего
сообщения. `GET /whatsapp/sleeping` отдаёт их (с готовым черновиком — выше).

**Поток дожима:** «🤖 Подготовить дожим» (`/whatsapp/prepare-drafts`) — бот пишет черновик
каждому (ПОД КОНТРОЛЕМ, не уходит) → «✅ Отправить» точечно (`/conversations/{id}/wa-draft` send)
или «всем готовым» (`/whatsapp/send-sleeping`, фон, **троттл 5–7с + лимит 300/день**, анти-бан).

**Ручные кнопки на лиде (Фаза 2, 2026-06-16):**
- **«✗ Проигран»** → `POST /conversations/{id}/stage` `{to_stage:lost, lost_reason}` (для флага «не лид»
  причина `hard_stop`, иначе `delayed`). Уходит из дожима (lost не активная стадия).
- **«🚫 Убрать из спящих»** → `POST /whatsapp/sleeping/{id}/dismiss` — ставит
  `profile_data['sleeping_dismissed_at']=now`, НЕ меняя стадию. Лид скрыт из дожима, пока сам не
  напишет (новое сообщение свежее отметки → снова появится).
- **Флаг «⚠️ не лид»** — `_dead_lead_check` (детерминированно) по ПОСЛЕДНЕМУ сообщению лида:
  «извините за беспокойство», «ошибся номером», «не интересует», «напишите на другой номер» и т.п.
  → такие наверх списка + красная кнопка «Проигран» одним кликом (не дожимать).

---

## Грабли

**Фантомные задачи** — если карточка уходит в ARCHIVED, задачи не cancelling автоматически.
Решение: `cancel_orphan_scheduled_actions(db)` в кроне (`services/whatsapp_sync.py`) + ручной
вызов через `POST /whatsapp/dedup`.

**mode=bot_auto + WA_BRAIN=0** — бот не отправит автономно, хотя mode=bot_auto. WA_BRAIN
должен быть включён для автономной работы.

**next_action устарело** — поле не обновляется само при изменении стадии. Явно звать
`POST /task-board/generate` или нажать кнопку «Обновить» в задачнике.
