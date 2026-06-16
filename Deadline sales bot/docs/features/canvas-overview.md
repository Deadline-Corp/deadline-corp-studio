# Обзор-канвас (Canvas Overview)

Стартовая страница Admin UI. Агрегирует ключевые метрики по всем каналам, воронке
и задачам в один экран — «взгляд сверху» без углубления в переписки.

---

## Где код

| Слой | Файл |
|---|---|
| API | `admin_api.py` → `GET /admin/api/overview` |
| UI | `admin-ui/src/pages/Overview.tsx` (или Dashboard.tsx) |

---

## GET /admin/api/overview

**Доступ:** member (owner + manager).

**Что возвращает:**

### channels

Массив по каждому активному каналу (`website / telegram / instagram / messenger / whatsapp`):

```json
{
  "channel": "whatsapp",
  "conversations": 142,
  "open": 38,
  "new_yesterday": 5,
  "hot": 12,
  "no_task": 7,
  "last_message_at": "2026-06-16T08:30:00Z"
}
```

| Поле | Что считается |
|---|---|
| `conversations` | Все диалоги этого канала (включая closed/archived) |
| `open` | `status=OPEN` |
| `new_yesterday` | Создан между yesterday 00:00 и today 00:00 |
| `hot` | `lead_temperature IN ('hot', 'ready')` |
| `no_task` | `status=OPEN` без pending `scheduled_action` |
| `last_message_at` | `MAX(messages.created_at)` по каналу |

### funnel

Подсчёт активных лидов по стадиям воронки (кастомные или встроенные 8):

```json
{
  "funnel": [
    {"stage": "in_dialog", "label": "В диалоге", "count": 23},
    {"stage": "qualified", "label": "Квалифицирован", "count": 8},
    ...
  ]
}
```

Включает только непотерянные (`lead_stage != 'lost'`, `status != 'archived'`).

### tasks

Сводка по задачам (все каналы суммарно):

```json
{
  "tasks": {
    "overdue": 3,
    "today": 7,
    "no_task": 12
  }
}
```

### inbox

Счётчики для Inbox-вкладки:

```json
{
  "inbox": {
    "open": 38,
    "takeover": 4,
    "handed_off": 2
  }
}
```

| Поле | Что считается |
|---|---|
| `open` | `status=OPEN` |
| `takeover` | `operator_takeover=True` — оператор взял ведение |
| `handed_off` | handoff_done=True — бриф отправлен в Telegram |

---

## Обновление данных

Эндпоинт всегда возвращает свежие данные из БД (нет кэша на стороне сервера).
Admin UI может поллить каждые 30-60 секунд для живого обновления.

---

## Связь с другими эндпоинтами

| Из Overview | Куда ведёт |
|---|---|
| Канал → «Лиды» | `GET /admin/api/conversations?channel=whatsapp` |
| Воронка → стадия | `GET /admin/api/conversations?stage=qualified` |
| Задачи → «Мой день» | `GET /admin/api/today` |
| Задачник | `GET /admin/api/task-board` |

---

## Грабли

**WhatsApp метрики** — `new_yesterday` считает по `created_at` (timestamp создания диалога).
Рекламный @lid-лид может быть создан до разрешения телефона, поэтому метрика может
расходиться с реальным числом «новых» рекламных контактов на 1-2.

**no_task vs pending** — `no_task` = open диалог без PENDING scheduled_action. Если
задача висит в `processing` (крон взял но ещё не завершил), она НЕ считается «без задачи».
