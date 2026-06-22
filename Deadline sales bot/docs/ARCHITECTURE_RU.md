# DEADLINE Sales Bot — Полная Архитектура (актуально на 2026-06-16)

Этот документ — единая авторитетная карта системы для нового разработчика или нового контекстного окна.
Содержит: общую архитектуру, все подсистемы и связи, каналы, мозг, воронку, задачи, календарь, ENV-флаги, список эндпоинтов, миграции и правила безопасной работы.

---

## 1. Общая архитектура

```
┌──────────────────────────────────────────────────────────────────┐
│  Railway — единый контейнер (один процесс)                        │
│                                                                    │
│  FastAPI (async event loop)                                        │
│    ├─ main.py           — публичные вебхуки + /message + /chat   │
│    ├─ admin_api.py      — /admin/api/* (Bearer ADMIN_UI_TOKEN)   │
│    └─ /admin/ui         — StaticFiles SPA (React, admin-ui/dist) │
│                                                                    │
│  SQLAlchemy (sync, connection pool)                               │
│    └─ PostgreSQL + pgvector (Railway Postgres)                    │
│                                                                    │
│  Cron-воркер (asyncio.Task, каждые 10 мин)                       │
│    └─ services/cron.py :: start_cron_worker                      │
│                                                                    │
│  Вотчдог-поток (threading.Thread)                                │
│    └─ main.py :: _watchdog — если /health не отвечает >120c      │
│       → os._exit(1) → Railway перезапускает контейнер            │
└──────────────────────────────────────────────────────────────────┘
```

### Ключевые проектные решения

| Решение | Почему |
|---|---|
| Sync SQLAlchemy в async FastAPI | Меньше сложности, предсказуем. НО: держать коннект при LLM-вызове = вис (пул блокируется). Все тяжёлые пути — в фон. |
| Единый процесс | Railway бесплатный тариф — один инстанс. Вотчдог защищает от вечного виса. |
| pgvector вместо Chroma | Chroma осталась как легаси-бэкстоп. Все новые KB-чанки и эмбеддинги коррекций — в Postgres. |
| Alembic миграции авто на старте | `alembic upgrade head` при каждом запуске. Нет ручного шага. |

---

## 2. Поток данных: входящее сообщение → ответ

```
Лид пишет в WhatsApp / TG / Сайт / IG / FB
       │
       ▼
Вебхук приходит на Railway
       │
POST /webhooks/waha          POST /webhooks/telegram       POST /message (сайт)
POST /webhooks/greenapi      POST /webhooks/messenger
POST /webhooks/instagram
       │
       ▼  (ack СРАЗУ, обработка в фоне — asyncio.create_task)
channels/*.py :: parse_*_webhook(request)
       │
       ▼
_handle_message(req, db)                         ◄── ЕДИНЫЙ обработчик для всех каналов
       │
       ├─ 1. IDENTITY: resolve_or_create_customer_with_meta(db, channel, external_id, ...)
       │       → Customer + ChannelIdentity (по email/phone/tg_handle — один лид, много каналов)
       │
       ├─ 2. CONVERSATION: get_or_create_conversation(db, customer_id, channel, channel_conv_id)
       │       → Conversation (статус OPEN)
       │
       ├─ 3. ИМЯ/ТЕЛЕФОН из мессенджера: для WhatsApp — из notifyName + peer (@c.us → телефон;
       │                                    @lid → LID API WAHA → телефон)
       │
       ├─ 4. ФОРУМ-ТОПИК: если TELEGRAM_OPERATOR_GROUP_ID задан и у диалога нет топика —
       │       create_forum_topic → link_forum_topic → close_forum_topic (бот стартует закрытым)
       │
       ├─ 5. PERSIST: append_message(db, conv.id, role="user", ...)
       │
       ├─ 6. PHASE 13 (Returning Lead): если was_returning_match + свежая беседа →
       │       Branch A: recall-приветствие (без RAG/LLM-основного потока)
       │       Branch B: classify_topic_decision → NEW (новая беседа) | CONTINUE | UNCLEAR
       │
       ├─ 7. RAG: pgvector_search(question, top_k=5) → relevant KB chunks
       │
       ├─ 8. TRAINING CORRECTIONS: training.retrieve(question) → top-K активных коррекций
       │
       ├─ 9. BUILD PROMPT: build_chat_prompt(system_prompt, history, context, corrections)
       │       system_prompt = DB-версия (prompt_store) ИЛИ константа prompts.SYSTEM_PROMPT
       │
       ├─ 10. LLM: call_llm(prompt) → primary_llm → fallback_llm при ошибке
       │
       ├─ 11. PERSIST ответ: append_message(role="assistant")
       │
       ├─ 12. HANDOFF CHECK: check_handoff(history) → если лид дал контакт и есть бриф →
       │       send_telegram_brief → mark_handoff_done
       │
       └─ 13. COMMIT + вернуть MessageResponse
              │
              ▼
    Канал-специфичная отправка (channels/*.py :: send_*_reply)

WhatsApp-путь ОТЛИЧАЕТСЯ:
  - WA_BRAIN активен (env WA_BRAIN=1) → _brain_for_wa_peer(conv, cust) в фоне:
       analyze_and_advance(db, conv, cust, llm) → двигает стадию + созвон + сигнал
  - wa_autonomous==True (per-conv) → бот отвечает сам в Telegram
  - wa_autonomous==False → ответ в pending_wa_draft (одобрение в карточке)
  - WA_WEBHOOK_PAUSE=1 → вебхук ack без обработки (экстренная пауза)
```

---

## 3. Каналы

### 3.1 WhatsApp (WAHA) — главный канал

**Файлы:** `channels/waha.py`, `main.py` (`_process_wa_payload`, `_wa_send`, `_resolve_wa_chat_id`)

**Как подключено:**
- Self-hosted WAHA (devlikeapro/waha) на VPS → регистрирует вебхук на наш `/webhooks/waha`
- Высший приоритет: если `WAHA_BASE_URL` задан — WhatsApp идёт через WAHA
- История синхронизируется ТОЛЬКО при ре-скане QR (через `/admin/api/whatsapp/sync`)

**@lid рекламные лиды:**
- Рекламный лид приходит со скрытым peer `@lid` (≥13 цифр) вместо телефона
- При входящем: LID API WAHA → resolve_lid_phone → phone в customer.phone
- При импорте (`/whatsapp/import-leads`): fable-import по телефону
- Дедуп: `dedup_wa_by_phone` (крон, каждые 10 мин) + `/whatsapp/dedup` (ручной)

**Антибан / лимиты:**
- `WA_DAILY_SEND_CAP` — максимум исходящих в сутки (env, default неограничен)
- Throttle между отправками: `asyncio.sleep(0.4)` в `sweep_recent`
- Фантом-чистка: `cleanup_wa_artifacts()` — удаляет assistant-сообщения без подтверждения доставки

**Вебхук-обработка (ack-first):**
```python
# main.py
@app.post("/webhooks/waha")
async def waha_webhook(request: Request):
    # 1. Прочитать тело
    # 2. asyncio.create_task(_process_wa_payload(data))  ← фон
    return {"ok": True}  # ← немедленный ack (WAHA не ждёт)
```

**WA_BRAIN флаг:**
- `WA_BRAIN=1` → при каждом новом сообщении: `analyze_and_advance(...)` в фоне
  - Двигает воронку, распознаёт договорённость о созвоне, сигналит владельцу
- `WA_BRAIN_SWEEP=1` → дополнительно в кроне: `sweep_recent(...)` раз в 10 мин
  (по умолчанию ВЫКЛ — риск исчерпания пула при LLM в кроне)

### 3.2 Telegram

**Файлы:** `channels/telegram.py`, `main.py`

**Как подключено:**
- Telegram Bot API → вебхук на `/webhooks/telegram`
- Защита: `TELEGRAM_WEBHOOK_SECRET` — каждый апдейт должен иметь matching header
- Если секрет не задан → 503 (fail-closed)

**Форум-суперgroup (оператор):**
- `TELEGRAM_OPERATOR_GROUP_ID` — ID форум-суперgroups команды
- При первом сообщении каждого диалога → `create_forum_topic` → тема для этого лида
- Топик закрывается сразу (бот активный спикер); оператор нажимает «Возьму на себя» → `reopen_forum_topic`
- `set_operator_takeover(True)` → бот молчит, оператор отвечает из форума

**Дедуп апдейтов:**
- `ProcessedUpdate` таблица — `event_key = 'telegram:<update_id>'`, ON CONFLICT DO NOTHING
- Переживает рестарт (раньше был in-memory dict, ретраи после деплоя дублировали сообщения)

### 3.3 Website виджет

**Файлы:** `widget/widget.js`, `main.py` (`/chat`, `/message`)

- SPA-виджет отправляет в `/chat` (POST, legacy) или `/message` (новый)
- `session_id` = `external_id` = `channel_conversation_id`
- WebSocket-like: лид поллит GET-ответом, бот отвечает синхронно

### 3.4 Instagram / Messenger (Meta)

**Файлы:** `channels/instagram.py`, `channels/messenger.py`

- Вебхуки: `/webhooks/instagram`, `/webhooks/messenger`
- Верификация: `META_APP_SECRET` → `X-Hub-Signature-256` на каждом запросе
- `META_PAGE_ACCESS_TOKEN` — один токен на IG и Messenger (одна Facebook Page)
- GET-верификация: `/webhooks/instagram?hub.challenge=...` → `META_VERIFY_TOKEN`

### 3.5 Green-API (резервный, без верификации)

**Файл:** `channels/greenapi.py`

- Альтернатива WAHA: подключение реального номера по QR через linked-device
- Активируется только если `GREENAPI_ID_INSTANCE` задан
- Вебхук: `/webhooks/greenapi`

---

## 4. Мозг (LLM-провайдер)

### 4.1 Выбор провайдера

```python
# main.py :: _resolve_llm_config()
if LLM_PROVIDER == "gemini":     → Google Gemini (OpenAI-совместимый endpoint)
elif LLM_PROVIDER == "openrouter": → OpenRouter
elif LLM_PROVIDER == "ollama":   → Ollama Cloud
# Авто (если LLM_PROVIDER пусто):
elif OPENROUTER_API_KEY:         → OpenRouter (приоритет)
elif OLLAMA_API_KEY:             → Ollama Cloud
```

**Модели (дефолт):**
- OpenRouter primary: `meta-llama/llama-3.3-70b-instruct`
- OpenRouter fallback: `deepseek/deepseek-chat`
- Gemini primary: `gemini-2.5-flash`
- Gemini fallback: `gemini-2.5-flash-lite`

**Клиенты:**
- `primary_llm` — основной (temp 0.2, max_tokens 2048)
- `fallback_llm` — резерв при ошибке
- `handoff_llm` — для handoff-классификатора (temp 0.0)
- `trainer_llm` — для /admin/training (temp 0.3)

### 4.2 conversation_brain.analyze_and_advance

**Файл:** `services/conversation_brain.py`

Один LLM-вызов на переписку → JSON-решение:

```
{
  "stage": "qualified",          // текущая стадия по смыслу
  "call_agreed": true,           // стороны договорились о созвоне
  "call_day": "wed",             // НАЗВАНИЕ дня (LLM не вычисляет дату)
  "call_time": "15:00",
  "call_datetime_utc": "...",    // запасной вариант
  "call_medium": "WhatsApp",
  "wants_human": false,          // лид просит живого человека
  "reason": "..."
}
```

**_resolve_call_dt(now_lead, call_day, call_time):**
- ДЕТЕРМИНИРОВАННО вычисляет дату из названия дня (today/tomorrow/mon..sun)
- LLM возвращает только "какой день назвали" — никакой арифметики в LLM
- Защита от ошибок LLM с датами

**_lead_silent(db, conv):**
- Последняя реплика НЕ от лида → молчание → НЕ создаём призрачный созвон
- Защита от фантом-договорённости ("увидел КП и пропал")

**Результат analyze_and_advance:**
1. Стадия вперёд (только вперёд, never назад)
2. `conv.pending_call_suggestion = {...at, when_human, medium, reason}` (не авто-бронь!)
   → менеджер подтверждает в карточке → создаётся событие
3. Сигнал владельцу (один раз на эпизод "просит человека")
4. Обновление черновика ответа в фоне (`refresh_draft=True` — на вебхуке; `False` — в sweep)

### 4.3 next_action.generate_next_action

**Файл:** `services/next_action.py`

Умный следующий шаг по лиду (сохраняется в `conv.next_action`):

| kind | mode (в задачнике) | Когда |
|---|---|---|
| `reengage` | `bot_auto` / `needs_approval` | Лид замолчал после КП/предложения |
| `answer` | `bot_auto` / `needs_approval` | Есть что ответить, бот может сам |
| `human` | `human` | Нужен живой человек (КП, переговоры) |
| `wait` | `wait` | Лид недавно обещал ответить |
| `unclear` | `unclear` | Непонятно → пас администратору |

`mode=bot_auto` — только если `conv.wa_autonomous==True` (per-conv флаг «Разрешить боту»).
`mode=needs_approval` — иначе черновик в `pending_wa_draft`.

---

## 5. Воронка и стадии

**Файлы:** `services/funnel_store.py`, `services/funnel.py`, `db/models.py`

### 5.1 Встроенные стадии (BUILTIN_KEYS)

```
new_lead → in_dialog → qualified → nda → on_call → tz_approved
         → proposal → prepayment → in_work → completed_won → post_sale
                                                          → lost
```

**lost_reason:** price / not_our_format / competitor / delayed / no_budget / hard_stop

### 5.2 Кастомные стадии (PipelineStage)

- Настраиваются из Admin UI → Настройки → Воронка
- Хранятся в `pipeline_stages` таблице
- `builtin=True` → нельзя удалить (на них ссылается бот), можно переименовать/скрыть
- `funnel_store.get_stages(db)` = кастомные ИЛИ встроенные 8 (если таблица пустая)

### 5.3 StageTransition — история переходов

- Пишется при КАЖДОМ переходе: by=admin/bot/automation
- Фундамент конверсионной аналитики
- Из карточки Admin UI → `/admin/api/conversations/{id}/stage`

---

## 6. Задачи (Scheduled Actions)

**Файл:** `services/scheduled_actions.py`, `db/models.py :: ScheduledAction`

### Типы задач (action_type)

| action_type | executor | Когда создаётся |
|---|---|---|
| `followup_message` | bot | Лид попросил напомнить / нудж / шаблон прогрева |
| `call_booked` | human | Созвон назначен (факт; бот не "исполняет") |
| `call_reminder` | bot | Автоматически при каждом созвоне (за день/3ч/1ч) |
| `warming_touch` | human | Крон: лид нагревается по кадэнс-таблице |
| `operator_callback` | human | Ручная задача из Admin UI |
| `escalation` | human | CRM-эскалация (через crm_dispatch) |

### Статусы задачи

```
pending → processing (крон взял) → done / failed (после 3 попыток)
       → superseded (более новая задача заменила)
       → cancelled (отмена созвона или архивация карточки)
```

### Атомарный клейм (FOR UPDATE SKIP LOCKED)

- `claimed_at` при взятии в работу
- Протухший claim (>15 мин) — перезаберётся следующим кроном
- Один followup не уйдёт дважды при параллельных свипах

### Дедуп followup_message

- `write_scheduled_action`: у диалога максимум ONE pending bot-followup
- Новая просьба → гасим (superseded) старые → `crm_task_id` переносится

---

## 7. Задачник (Task Board)

**Эндпоинт:** `GET /admin/api/task-board`  
**Файл:** `admin_api.py`

**4 режима отображения:**

1. **Бакеты срочности:** overdue / today / tomorrow / week / later
   - По scheduled_actions (pending/processing), не из архивных карточек
   - Приоритет: температура × 100 + позиция стадии

2. **«Лиды без задачи»** (`no_task_leads`):
   - Активная стадия, нет pending scheduled_action
   - `conv.next_action` (мозг) → label/mode/draft/reason
   - `unclear` → выше остальных

3. **`POST /task-board/generate`** (owner only):
   - Мозг разбирает до 20 лидов без задачи → записывает `conv.next_action`
   - Каждый лид в СВОЕЙ короткой сессии (anti-vis)

4. **`GET /today`** — «Мой день»:
   - Просроченные / сегодня / на неделю + созвоны с дедупом по телефону/имени

---

## 8. Календарь

**Файл:** `admin_api.py`

| Эндпоинт | Что |
|---|---|
| `GET /admin/api/calendar-events?start=&end=` | Все события диапазона: scheduled_actions + booked_call_at. Для FullCalendar (месяц/неделя/день). |
| `GET /calendar.ics` (main.py) | ICS-фид для Google Calendar / Apple Calendar. Только будущие созвоны + напоминания. |
| `POST /admin/api/conversations/{id}/call` | Перенос / отмена созвона вручную из карточки. |
| `POST /admin/api/conversations/{id}/call-suggestion` | Подтвердить / отклонить предложение бота о созвоне. |

**Виды событий (kind):**
- `call` — назначенный созвон (📞)
- `reminder` — напоминание о созвоне (⏰)
- `bot` — задача бота (🤖)
- `task` — задача человека (📋)

**Дедуп созвонов:**
- Один лид может иметь 2 карточки (@lid + телефонная) → созвон показывается 1 раз
- Ключ дедупа: телефон (цифры) → имя (lowercase) → id

---

## 9. Обзор-канвас (Overview)

**Эндпоинт:** `GET /admin/api/overview`

Расширенные метрики для стартовой страницы Admin UI:

**Каналы** (по каждому: website/telegram/instagram/messenger):
- `conversations` — всего бесед
- `open` — открытых
- `new_yesterday` — новых за вчера
- `hot` — лидов с температурой hot/ready
- `no_task` — активных без задачи
- `last_message_at` — последняя активность

**Воронка:** подсчёт по lead_stage (активные стадии, кастомные или встроенные)

**Задачи:** overdue, today, no_task — сводки

**Inbox:** open, takeover, handed_off

---

## 10. Дедуп @lid-дублей

**Файл:** `services/whatsapp_sync.py`

**dedup_wa_by_phone(db):**
- Находит карточки WhatsApp с одинаковым phone (цифры) — рекламный @lid + телефонный двойник
- Оставляет с более продвинутой стадией воронки; остальные → ARCHIVED
- Запускается каждые 10 мин в кроне + ручной `/admin/api/whatsapp/dedup`

**cancel_orphan_scheduled_actions(db):**
- Задачи/напоминания карточек ARCHIVED → superseded
- Чтобы «Мой день» и Календарь обновлялись без ручного ввода

**cleanup_wa_artifacts():**
- Фантомы (assistant без waha_id/approved_via/delivered) → удалить
- Эхо-дубли (operator-echo совпадает с assistant) → удалить оператор-дубль

---

## 11. Бэкап БД

**Файл:** `services/db_backup.py`

**build_export()** — синхронный обход через SQLAlchemy core:
- Таблицы: customers, conversations, messages, scheduled_actions, stage_transitions,
  bot_settings, training_corrections, workspace_members, crm_events, automations,
  automation_runs, processed_updates, kb_chunks
- Эмбеддинги (большие векторы) исключаются
- Формат: `deadline-backup-YYYYMMDD-HHMM.json.gz`

**Три пути:**
- `GET /admin/api/db-backup` → скачать вручную (owner only)
- `POST /admin/api/db-backup/send-telegram` → отправить в TG владельцу прямо сейчас
- Авто в кроне раз в день (env `DB_BACKUP_TG=1`, default включён)

---

## 12. /health и вотчдог

**Эндпоинт:** `GET /health`

- Railway liveness probe
- Проверяет DB (быстрый `SELECT 1`), статус WAHA-сессии (только если настроено)
- **НИКОГДА не делает LLM-вызовов** — эндпоинт должен отвечать за <3с
- off-loop: не держит DB-коннект при сетевых запросах к WAHA

**Вотчдог (threading.Thread):**
- Пульс: main.py пишет `_last_health_ts = time.time()` в событии `/health`
- Вотчдог-поток проверяет каждые 30с: если пульс > 120с → `os._exit(1)`
- Railway замечает exit и перезапускает контейнер (~10с downtime)
- Защита от scenario: "WAHA вебхук пришёл → sync LLM без to_thread → пул выдохся → event loop завис → /health не отвечает → вотчдог убивает"

---

## 13. Полный список эндпоинтов

### 13.1 main.py (публичные)

| Метод | Путь | Назначение |
|---|---|---|
| POST | `/message` | Универсальный вход (любой канал) |
| POST | `/chat` | Алиас /message для website-виджета (legacy) |
| POST | `/lead-submit` | Форма лидов с сайта deadlinecorp.com/lead-form/ |
| GET | `/health` | Liveness probe Railway |
| GET | `/calendar.ics` | ICS-фид созвонов (Google Cal / Apple Cal) |
| GET | `/webhooks/telegram` | Верификация Telegram-вебхука |
| POST | `/webhooks/telegram` | Входящие из Telegram |
| GET | `/webhooks/messenger` | Верификация Meta-вебхука (hub.challenge) |
| POST | `/webhooks/messenger` | Входящие из FB Messenger |
| GET | `/webhooks/instagram` | Верификация Meta-вебхука |
| POST | `/webhooks/instagram` | Входящие из Instagram |
| POST | `/webhooks/waha` | Входящие из WAHA (WhatsApp self-hosted) |
| POST | `/webhooks/greenapi` | Входящие из Green-API (резервный WA) |
| GET | `/admin/ui/` | SPA Admin UI (StaticFiles) |

### 13.2 admin_api.py (/admin/api/*)

Все эндпоинты за Bearer-токеном. `owner` = ADMIN_UI_TOKEN; `manager` = mgr_-токен из workspace_members.

**Аутентификация / профиль**

| Метод | Путь | Доступ | Назначение |
|---|---|---|---|
| GET | `/me` | member | Текущий пользователь + tenant + настройки UI |

**Команда (Team)**

| Метод | Путь | Доступ | Назначение |
|---|---|---|---|
| GET | `/team` | owner | Список членов команды |
| POST | `/team` | owner | Создать менеджера (токен показывается 1 раз) |
| POST | `/team/{id}/toggle` | owner | Активировать / деактивировать |
| POST | `/team/{id}/update` | owner | Задать отдел и Telegram chat |

**Обзор-канвас**

| Метод | Путь | Доступ | Назначение |
|---|---|---|---|
| GET | `/overview` | member | Сводка: каналы, воронка, задачи, inbox |

**Inbox / Переписки**

| Метод | Путь | Доступ | Назначение |
|---|---|---|---|
| GET | `/conversations` | member | Список с фильтрами: channel/stage/temperature/status/takeover/q |
| GET | `/conversations/{id}` | member | Карточка лида (без LLM! только БД) |
| GET | `/conversations/{id}/messages` | member | Сообщения (пагинация: after/before/limit) |
| POST | `/conversations/{id}/reply` | member | Ответить от имени оператора |
| POST | `/conversations/{id}/takeover` | member | Взять / отдать обратно боту |
| POST | `/conversations/{id}/stage` | member | Сменить стадию воронки (+ CRM-зеркало) |
| POST | `/conversations/{id}/call` | member | Перенос / отмена созвона из карточки |
| POST | `/conversations/{id}/call-suggestion` | member | Подтвердить / отклонить предложение бота о созвоне |
| POST | `/conversations/{id}/wa-draft` | member | Одобрить / отклонить черновик ответа бота |
| POST | `/conversations/{id}/wa-autonomous` | member | Разрешить / запретить боту вести диалог сам |
| POST | `/conversations/{id}/suggest-reply` | member | Сгенерировать черновик ответа по запросу |
| POST | `/conversations/{id}/advise` | member | AI-копилот: рекомендация + черновик |
| POST | `/conversations/{id}/assign` | member | Назначить / снять сотрудника |
| POST | `/conversations/{id}/recurrence` | member | Сделать клиента регулярным (P6) |
| POST | `/conversations/{id}/nudge` | member | Пинок лиду: now / schedule / draft (LLM) |
| POST | `/conversations/{id}/fields` | member | Обновить кастомные поля лида |

**WhatsApp**

| Метод | Путь | Доступ | Назначение |
|---|---|---|---|
| GET | `/whatsapp/status` | member | Статус WAHA-сессии + прогресс последней синхронизации |
| POST | `/whatsapp/sync` | member | Запустить фоновый импорт истории из WAHA |
| GET | `/whatsapp/pending-suggestions` | member | Все диалоги с ожидающим предложением созвона |
| POST | `/whatsapp/import-leads` | member | Пакетный импорт лидов из fable-сессии |
| POST | `/whatsapp/dedup` | owner | Дедупликация @lid и fable-import карточек |
| POST | `/whatsapp/prepare-drafts` | owner | Пакетная генерация черновиков для всех лидов (фон) |
| GET | `/whatsapp/drafts-status` | member | Прогресс пакетной генерации черновиков |
| POST | `/whatsapp/clean-phantoms` | owner | Удалить фантомные assistant-сообщения |
| POST | `/whatsapp/brain-sweep` | member | Ручной запуск brain-sweep (вместо крона) |
| POST | `/whatsapp/simulate-lead` | owner | Симулировать нового лида для тестирования |

**Воронка / стадии**

| Метод | Путь | Доступ | Назначение |
|---|---|---|---|
| GET | `/funnel/stages` | member | Получить список стадий (кастомные или встроенные) |
| POST | `/funnel/stages` | owner | Сохранить набор стадий |
| POST | `/funnel/stages/reset` | owner | Сбросить к встроенным 8 |

**Задачник**

| Метод | Путь | Доступ | Назначение |
|---|---|---|---|
| GET | `/today` | member | «Мой день»: overdue/today/upcoming + созвоны |
| GET | `/task-board` | member | CRM-задачник: бакеты + лиды без задачи |
| POST | `/task-board/generate` | owner | Мозг разбирает лидов без задачи → next_action |
| POST | `/tasks` | member | Создать задачу вручную (bot или human) |
| POST | `/scheduled-actions/{id}/done` | member | «Сделано» для human-задачи |
| POST | `/scheduled-actions/{id}/reschedule` | member | Перенести задачу на другое время |
| POST | `/scheduled-actions/{id}/cancel` | member | Отменить задачу |
| GET | `/scheduled-actions` | member | Список задач с фильтрами |

**Календарь**

| Метод | Путь | Доступ | Назначение |
|---|---|---|---|
| GET | `/calendar-events` | member | События FullCalendar за диапазон (start, end) |

**Бэкап**

| Метод | Путь | Доступ | Назначение |
|---|---|---|---|
| GET | `/db-backup` | owner | Скачать gzip-дамп БД |
| POST | `/db-backup/send-telegram` | owner | Отправить дамп владельцу в Telegram |

**Мозг (промпт)**

| Метод | Путь | Доступ | Назначение |
|---|---|---|---|
| GET | `/prompt` | owner | Активный системный промпт (DB или константа) |
| GET | `/prompt/versions` | owner | История версий промпта |
| POST | `/prompt` | owner | Сохранить новую версию промпта |
| POST | `/prompt/activate` | owner | Активировать версию / откат на файл |
| POST | `/prompt/test` | owner | Dry-run: валидация template без LLM |
| POST | `/prompt/preview` | owner | Тест с реальным LLM (с заглушками RAG) |

**Правила обучения**

| Метод | Путь | Доступ | Назначение |
|---|---|---|---|
| GET | `/training-rules` | owner | Список активных коррекций |
| POST | `/training-rules/quick` | owner | Быстрое правило одной строкой (без LLM-тренера) |
| POST | `/training-rules/from-message` | owner | «Бот, учись у меня»: ответ оператора → правило |
| POST | `/training-rules/{id}/deactivate` | owner | Отключить правило (soft) |

**Настройки поведения**

| Метод | Путь | Доступ | Назначение |
|---|---|---|---|
| GET | `/behavior` | owner | Текущие оверрайды (нудж/прогрев/дайджест) |
| POST | `/behavior` | owner | Сохранить оверрайды (без деплоя) |
| GET | `/settings` | owner | Санитизированные env-настройки (без токенов) |
| GET | `/workspace` | member | Данные тенанта + стадии |
| POST | `/workspace` | owner | Обновить настройки воркспейса |
| GET | `/languages` | member | Текущий список языков |
| POST | `/languages` | owner | Обновить список языков |

**KB (база знаний)**

| Метод | Путь | Доступ | Назначение |
|---|---|---|---|
| GET | `/kb` | owner | Список источников KB |
| POST | `/kb/upload` | owner | Загрузить .md файл в KB |
| DELETE | `/kb/{source}` | owner | Удалить источник из KB |

**Онбординг**

| Метод | Путь | Доступ | Назначение |
|---|---|---|---|
| POST | `/onboarding/generate` | owner | LLM-агент: дамп сайта → черновик конфига |
| POST | `/onboarding/apply` | owner | Применить конфиг: KB + промпт + пресет |

**Автоматизации**

| Метод | Путь | Доступ | Назначение |
|---|---|---|---|
| GET | `/automations` | owner | Список правил «Когда→Если→То» |
| POST | `/automations` | owner | Создать/обновить правило |
| GET | `/automations/{id}/runs` | owner | История срабатываний правила |
| POST | `/automations/{id}/toggle` | owner | Вкл/выкл правило |
| POST | `/automations/{id}/delete` | owner | Удалить правило |

**Кастомные поля**

| Метод | Путь | Доступ | Назначение |
|---|---|---|---|
| GET | `/custom-fields` | member | Список определений кастомных полей |
| POST | `/custom-fields` | owner | Добавить кастомное поле |

**Аналитика**

| Метод | Путь | Доступ | Назначение |
|---|---|---|---|
| GET | `/analytics` | member | Конверсионная воронка + активность по каналам |
| GET | `/analytics/objections` | member | Топ возражений (из переписок) |

**Операционные**

| Метод | Путь | Доступ | Назначение |
|---|---|---|---|
| POST | `/cron/sweep` | owner | Ручной запуск одного цикла крона |
| GET | `/presets` | owner | Список пресетов ниш |
| POST | `/presets/apply` | owner | Применить пресет ниши |

---

## 14. ENV-флаги (полная таблица)

### LLM-провайдер

| Переменная | Назначение | Дефолт |
|---|---|---|
| `LLM_PROVIDER` | Принудительно выбрать провайдера: `gemini\|openrouter\|ollama`. Пусто = авто. | `""` |
| `OPENROUTER_API_KEY` | Ключ OpenRouter. Если задан — используется авто. | None |
| `OPENROUTER_MODEL` | Primary модель OpenRouter | `meta-llama/llama-3.3-70b-instruct` |
| `OPENROUTER_FALLBACK_MODEL` | Fallback модель OpenRouter | `deepseek/deepseek-chat` |
| `GOOGLE_API_KEY` | Ключ Gemini | None |
| `GEMINI_MODEL` | Primary модель Gemini | `gemini-2.5-flash` |
| `GEMINI_FALLBACK_MODEL` | Fallback модель Gemini | `gemini-2.5-flash-lite` |
| `OLLAMA_API_KEY` | Ключ Ollama Cloud | None |

### WhatsApp

| Переменная | Назначение | Дефолт |
|---|---|---|
| `WAHA_BASE_URL` | URL self-hosted WAHA (напр. http://IP:3000). Если задан — WhatsApp через WAHA. | None |
| `WAHA_API_KEY` | API-ключ WAHA | None |
| `WAHA_SESSION` | Имя WAHA-сессии | `"default"` |
| `WA_BRAIN` | `1` — включить умное авто-ведение диалогов (analyze_and_advance при вебхуке) | `0` |
| `WA_BRAIN_SWEEP` | `1` — дополнительный sweep в кроне каждые 10 мин. По умолчанию ВЫКЛ (риск пула). | `0` |
| `WA_WEBHOOK_PAUSE` | `1` — вебхуки WAHA принимаются без обработки (экстренная пауза при инциденте) | `0` |
| `WA_AUTO_RECONCILE` | `0` — отключить авто-сверку с WAHA раз в час (каждый 6-й цикл крона) | `1` |
| `WA_DAILY_SEND_CAP` | Максимум исходящих WhatsApp за сутки (0 = без лимита) | `0` |
| `WHATSAPP_TOKEN` | Permanent System User token Meta Cloud API | None |
| `WHATSAPP_PHONE_NUMBER_ID` | Phone Number ID Meta Cloud API | None |
| `WHATSAPP_VERIFY_TOKEN` | Verify token Meta App webhook | None |
| `WHATSAPP_APP_SECRET` | App Secret Meta (верификация вебхуков) | None |
| `GREENAPI_ID_INSTANCE` | Green-API instance ID (резервный WA) | None |
| `GREENAPI_API_TOKEN` | Green-API token | None |
| `GREENAPI_API_URL` | Green-API endpoint | None |

### Telegram

| Переменная | Назначение | Дефолт |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | Токен бота @BotFather | None |
| `TELEGRAM_CHAT_ID` | Chat ID владельца для handoff-брифов и уведомлений | None |
| `TELEGRAM_OPERATOR_GROUP_ID` | ID форум-суперgroup команды (отрицательный, напр. -1001234567890) | None |
| `TELEGRAM_WEBHOOK_SECRET` | Секрет для верификации TG-вебхуков. Если не задан → 503 (fail-closed) | None |

### Meta (Instagram / Messenger)

| Переменная | Назначение | Дефолт |
|---|---|---|
| `META_VERIFY_TOKEN` | Verify token для webhook verification | None |
| `META_APP_SECRET` | App Secret для X-Hub-Signature-256 | None |
| `META_PAGE_ACCESS_TOKEN` | Page Access Token (IG + Messenger) | None |

### Бэкап и авто-задачи

| Переменная | Назначение | Дефолт |
|---|---|---|
| `DB_BACKUP_TG` | `0` — отключить авто-бэкап в Telegram раз в день | `1` |

### Безопасность / Admin UI

| Переменная | Назначение | Дефолт |
|---|---|---|
| `TRAINING_AUTH_TOKEN` | Master Bearer-токен для Admin UI и тренера. Не задан → 503. | None |
| `ADMIN_UI_TOKEN` | Альтернативное имя для TRAINING_AUTH_TOKEN (приоритет) | None |
| `METRICS_AUTH_TOKEN` | Bearer для /metrics (если не задан — открытый доступ) | None |

### Прочее

| Переменная | Назначение | Дефолт |
|---|---|---|
| `TENANT_SLUG` | Тенант (папка в `tenants/<slug>/`) | `"deadline-corp"` |
| `CRM_ENABLED` | Мастер-флаг CRM-интеграции | `False` |
| `CRM_PROVIDER` | `noop\|hubspot\|bitrix24` | `"noop"` |
| `CRM_LAZY_CONTACT` | Создавать HubSpot-контакт только для «реальных» лидов | `False` |
| `RETURNING_LEAD_RECALL` | Phase 13: вспоминать вернувшихся лидов | `True` |
| `HUBSPOT_ACCESS_TOKEN` | HubSpot Service Key (Bearer pat-na2-...) | None |
| `HUBSPOT_PORTAL_ID` | HubSpot Portal ID для deep-links | None |
| `GROQ_API_KEY` | Groq Whisper для транскрипции голосовых Telegram | None |
| `ALLOWED_ORIGINS` | CORS-список через запятую | дефолтные домены |
| `LOG_LEVEL` | Уровень логирования | `"INFO"` |

---

## 15. Миграции Alembic (001–020)

| Номер | Файл | Что добавлено |
|---|---|---|
| 001 | `001_initial_schema.py` | Базовые таблицы: customers, channel_identities, conversations, messages, kb_chunks |
| 002 | `002_operator_takeover.py` | `conversations.operator_takeover` + `forum_topic_id` |
| 003 | `003_training_corrections.py` | `training_corrections` (правила + эмбеддинги, HNSW-индекс) |
| 004 | `004_crm_and_lead_axes.py` | `lead_score`, `lead_temperature`, `interaction_type`, `identity_keys`, `crm_contact_id`, `crm_deal_id`, `lead_stage`, `lost_reason` |
| 005 | `005_warming_dedup.py` | `conversations.last_warmed_at` (для дедупа warming-задач) |
| 006 | `006_returning_lead_memory.py` | `conversations.archived_at`, `parent_conversation_id` (Phase 13 recall) |
| 007 | `007_scheduled_actions.py` | `scheduled_actions` — движок отложенных задач |
| 008 | `008_dedup_and_claim.py` | `scheduled_actions.claimed_at` + `processed_updates` (дедуп апдейтов через рестарт) |
| 009 | `009_crm_events_durable.py` | `crm_events` (durable CRM-очередь, recovery после рестарта) |
| 010 | `010_lead_submissions.py` | `lead_submissions` (история заявок с формы + contact_exists) |
| 011 | `011_prompt_versions.py` | `prompt_versions` (редактируемые версии системного промпта без деплоя) |
| 012 | `012_custom_funnel_and_settings.py` | `pipeline_stages` (кастомные стадии), `bot_settings` (key-value настройки из UI) |
| 013 | `013_automations_fields_analytics.py` | `automation_rules`, `automation_runs`, `custom_field_defs`, `stage_transitions` |
| 014 | `014_workspace_members.py` | `workspace_members` (команда: роли owner/manager, sha256-хэш токена) |
| 015 | `015_assignment_departments.py` | `conversations.assigned_member_id`, `workspace_members.department`, `workspace_members.telegram_chat_id` (P3b: назначение на сотрудника) |
| 016 | `016_pending_wa_draft.py` | `conversations.pending_wa_draft` (черновик ответа бота на одобрение) |
| 017 | `017_wa_autonomous.py` | `conversations.wa_autonomous` (per-conv разрешение боту вести диалог сам) |
| 018 | `018_wa_classification.py` | `conversations.wa_classification` (результат классификации лид/не-лид при импорте) |
| 019 | `019_pending_call_suggestion.py` | `conversations.pending_call_suggestion` (предложение бота о созвоне, ждёт подтверждения) |
| 020 | `020_next_action.py` | `conversations.next_action` (умный следующий шаг: mode/kind/label/draft/reason) |

---

## 16. Как работать над проектом БЕЗОПАСНО

### ГЛАВНЫЙ УРОК: НЕ держать DB-коннект при LLM-вызове

**Симптом инцидента (06-02, 06-15):** Event loop виснет → `/health` не отвечает → вотчдог убивает контейнер → Railway перезапускает.

**Причина:** Sync SQLAlchemy при LLM-вызове держит коннект из пула. Если все коннекты заняты → `checkout` блокирует event loop (pool pre-ping ждёт) → event loop завис → все вебхуки встали.

**Правила:**

```python
# ПЛОХО:
with session_scope() as db:
    conv = db.get(Conversation, cid)
    result = await llm.ainvoke(prompt)  # ← LLM-вызов ДЕРЖИТ коннект
    db.commit()

# ХОРОШО: СВОЯ короткая сессия до LLM и своя после
with session_scope() as db:
    conv = db.get(Conversation, cid)
    transcript = _make_transcript(conv, db)
# ← сессия закрыта, коннект вернулся в пул
result = await llm.ainvoke(prompt)
# ← новая сессия ТОЛЬКО для записи результата
with session_scope() as db:
    conv = db.get(Conversation, cid)
    conv.next_action = result
```

**Все тяжёлые пути работают в ФОНЕ:**
- Вебхук WAHA: `asyncio.create_task(_process_wa_payload(...))` → ack немедленно
- `conversation_brain.analyze_and_advance` — фоновая задача на вебхуке
- `sweep_recent` в кроне — **отпускает коннект между диалогами**
- `/conversations/{id}` (GET карточки) — **НИКОГДА не делает LLM-вызов** (карточка поллится часто)
- `task_board/generate` — каждый лид в `with session_scope()` внутри for-цикла

### Деплой

```bash
# ПРАВИЛЬНО:
railway link -p <project_id> -e production -s deadline-sales-bot
railway up --detach

# НЕПРАВИЛЬНО:
git push   # не вызывает авто-деплой (нет CI/CD хука на эту ветку)
```

**Миграции:** Alembic `upgrade head` запускается автоматически при старте процесса (`lifespan`). Нет ручного шага.

**Проверка выката:** Смотреть на хэш JS-бандла `/admin/ui/index.html` — если hash изменился, значит новая версия поднялась.

### Восстановление при висе

1. **Быстро:** `WA_WEBHOOK_PAUSE=1` → редеплой → лиды не теряются, вебхуки ack но не обрабатываются
2. **Или:** Снять `WA_BRAIN=1` → редеплой → WhatsApp работает без мозга
3. **Вотчдог** сам перезагрузит при пульсе >120с (обычно справляется без вмешательства)
4. **Перед рискованным изменением:** `POST /admin/api/db-backup/send-telegram`

### Безопасные изменения

- **Читать БД (GET карточки, список)** → безопасно всегда
- **Писать БД без LLM** → безопасно (scheduled_actions, stage, reply)
- **Добавить миграцию** → тест локально, `railway up --detach`, Railway перезапустит и примет миграцию
- **Менять мозг/промпт** → через `POST /admin/api/prompt` без деплоя

### Рискованные изменения

- **Новый LLM-вызов в синхронном пути** → риск виса. Проверить: вызов идёт в `asyncio.create_task` или `asyncio.to_thread`, сессия закрыта ДО ainvoke.
- **Изменение `scheduled_actions`** → тест: убедиться что `claimed_at` сбрасывается в None после успеха/неудачи
- **Изменение логики дедупа WhatsApp** → тест на dry-run (`execute=false`) перед `execute=true`

### CRM-очередь

- `crm_queue.py` — durable async очередь CRM-событий
- Все CRM-вызовы через очередь (не синхронно) → не блокируют основной поток
- `crm_events` таблица — recovery: pending-строки переигрываются при старте

---

## 17. Мультитенант (текущее состояние)

Тенантный каркас встроен, но ещё не используется как full multitenancy:

- `tenants/<slug>/` — папка с `config.yaml`, `kb/`, `system_prompt.md`
- `TENANT_SLUG=<slug>` — выбор тенанта при старте
- Каждый тенант = отдельный Railway-сервис с отдельной Postgres
- `tenants/_template/` + `deploy/new-client.sh` — создание нового клиента

Серия P0–P6 добавила: нишевые пресеты, онбординг-агент, кастомные поля, языки, постоянные клиенты.
На DEADLINE prod задеплоено БЕЗ P0–P6 (7 волн до серии). Копия Кирила — с P0–P6.

---

*Файл поддерживается авторитетно: правьте при каждом изменении архитектуры.*
