# Operator Inbox — Telegram forum-супергруппа

Команда видит **все** диалоги бота в одной TG-группе. Каждый лид — отдельный topic. По нажатию кнопки оператор перехватывает диалог: пишет в теме — бот пересылает в личку лиду от своего имени.

---

## 0. Что получится

```
Telegram-группа "Deadline · Sales Inbox"  (forum mode, бот = admin)
│
├── 💬 telegram: @ivan_petrov
│   ├── 📥 [telegram] привет
│   ├── 📤 // привет. я — AI-агент Deadline...   [👤 Возьму на себя]
│   ├── 📥 [telegram🎙️] нужен сайт под кофейню       ← голосовое
│   ├── 📤 // понятно. лендинг кофейни...        [👤 Возьму на себя]
│   ↓
│   (оператор нажал кнопку)
│   🔔 OPERATOR TAKEOVER ON
│   ✍️ Иван, привет — давай созвонимся в понедельник?      ← оператор пишет
│   ↓
│   (улетает в личку лиду от имени бота, бот молчит сам)
│
├── 💬 instagram: maria@design.io
│   └── ...
│
├── 💬 website: kolya@coffee.com
│   └── ...
│
└── 💬 messenger: @paul.designs
    └── ...
```

Все три механики работают сами:
1. Каждое сообщение лида → зеркало в topic с эмодзи `📥 [канал]`
2. Каждый ответ бота → зеркало с эмодзи `📤` + кнопка «👤 Возьму на себя»
3. Нажатие кнопки → бот замолкает, тема в режиме takeover
4. Оператор пишет в теме → текст уходит лиду как обычное сообщение бота
5. `/release` в теме → бот снова отвечает сам

---

## 1. Создание супергруппы

1. В Telegram → **New Group** → название (например `Deadline · Sales Inbox`) → нажми Next без участников или добавь команду
2. После создания: открой группу → menu (☰) → **Group Info** → **Edit** (карандаш) → пролистай вниз → **Group Type** → **Convert to Supergroup**
3. Снова Edit → нашёл переключатель **Topics** → **enable**

> Если не видишь «Topics» — обнови Telegram до последней версии (нужен ≥ 9.0).

После включения topics группа становится **forum-supergroup**. Сверху появится список тем (сначала пустой, кроме закреплённого «General»).

---

## 2. Добавляем бота и даём права

1. В группе: **add member** → найди по username (тот же `@... ` под которым бот живёт на проде, у нас 8743503699:...)
2. После добавления тапни на бота в списке участников → **Promote to admin**
3. Включи **обязательно** эти права:
   - ✅ **Manage topics** (без этого `createForumTopic` вернёт 400)
   - ✅ **Send messages**
   - ✅ **Pin messages** (опционально, если хочется закреплять важные темы)
   - ❌ Delete messages — не нужно
   - ❌ Add new admins — не нужно
4. **Confirm**.

---

## 3. Получаем chat_id супергруппы

Бот должен знать id этой группы чтобы туда писать. Самый простой способ:

1. В супергруппе любым сообщением упомяни бота (`@deadline_sales_bot ping` или просто напиши что угодно)
2. Открой в браузере: `https://api.telegram.org/bot<TOKEN>/getUpdates`
3. Найди в JSON-ответе блок `"chat":{"id":-1001234567890,"type":"supergroup",...}` — это твой id
4. **Запиши со знаком минус** (для супергрупп id всегда отрицательный, типа `-1001234567890`)

Альтернативно — бот `@username_to_id_bot` или `@getmyid_bot` дадут тебе id если ты добавишь их временно в группу.

---

## 4. Кладём id в Railway

Railway → service `deadline-sales-bot` → **Variables** → **Raw Editor** → дописать:

```
TELEGRAM_OPERATOR_GROUP_ID=-1001234567890
```

(подставь свой настоящий id). Сохрани — Railway автоматически передеплоит контейнер (~30-60 сек).

Если ещё не делал — параллельно убедись что в Variables есть и `GROQ_API_KEY` (для голосовых):

```
GROQ_API_KEY=gsk_...
```

Регистрация Groq: https://console.groq.com → Create API Key. Free tier покрывает ~100 голосовых в день.

---

## 5. Проверка работы

После того как Railway передеплоил с новыми переменными:

### 5.1. Лид пишет боту

1. Открой бота `@deadline_sales_bot` в Telegram (или попроси кого-то ещё не из команды)
2. Напиши: `привет`
3. В группе `Deadline · Sales Inbox` появится новая тема `telegram: <username>` с зеркалом:
   ```
   📥 [telegram] привет
   📤 // привет. я — AI-агент Deadline, помогу собрать бриф. что у вас за проект...
       [👤 Возьму на себя]
   ```

### 5.2. Лид присылает голосовое

1. Запиши боту голосовое (любая длина до 5 мин)
2. В теме появится транскрипт с пометкой 🎙️:
   ```
   📥 [telegram🎙️] нужен сайт для кофейни с меню и контактами
   📤 // понятно. лендинг кофейни — наш формат. скиньте email...
   ```
3. Бот ответил так же как на текст — для лида ничего не изменилось.

### 5.3. Оператор берёт диалог

1. В теме нажми кнопку **`👤 Возьму на себя`** под последним ответом бота
2. В шапке появится `🔔 OPERATOR TAKEOVER ON`
3. Напиши прямо в тему: `Привет! Давайте созвонимся в понедельник.`
4. Лид у себя в Telegram получит это **от имени бота** (не подозревает что это оператор)
5. Лид отвечает → его сообщение приходит в тему как `📥 [telegram]`, бот **не отвечает сам**
6. Оператор продолжает писать в тему — каждая реплика идёт лиду
7. Когда диалог можно вернуть боту: оператор пишет `/release` в теме → `🤖 Бот снова отвечает автономно.`

### 5.4. Команды для оператора

В теме (с takeover включён или нет):

| Команда | Что делает |
|---|---|
| `/release` | Снять takeover, бот снова отвечает сам |
| `/close` | Закрыть conversation (статус CLOSED, новая тема не создастся пока лид не напишет снова) |
| `/note <текст>` | Внутренняя заметка — сохраняется в БД как system-сообщение, **лиду не уходит**. Удобно для «он мутный, цены не давать» или «вернётся через 2 недели после Discovery» |

Любой текст без `/` — это обычная реплика для лида.

---

## 6. Кросс-канальный takeover

Takeover работает не только для Telegram-лидов. Если лид пришёл с IG / FB / website — тема всё равно создастся, и оператор может перехватить:

| Канал лида | Что бот пересылает оператору | Что улетает лиду из темы |
|---|---|---|
| Telegram | весь диалог в тему | через Bot API Send (instant) |
| Instagram DM | весь диалог + voice transcripts | через Graph API Send API (instant, в пределах 24h-окна Meta) |
| Messenger | весь диалог | через Graph API Send API (instant, 24h-окно) |
| Site widget | весь диалог | **только сохраняется в БД** (виджет — синхронный, нет push к браузеру). Phase 2: SSE-канал → instant. Сейчас лид увидит ответ оператора при следующем своём сообщении в виджете |

Для IG/Messenger важно: Meta разрешает «human messages» вне 24h-окна, если у оператора есть `HUMAN_AGENT` tag (Page-level). Phase 2 — добавим тег в Send API. Сейчас работает только в 24h после последнего сообщения лида.

---

## 7. Что нельзя делать

- ❌ **Не пиши в теме на @-mention бота** (`@deadline_sales_bot привет`) — Telegram такие сообщения помечает как command, бот их парсит как `/привет` команду и игнорирует. Просто пиши обычный текст.
- ❌ **Не редактируй прошлые сообщения бота** в теме — бот их не пере-зеркалит. Если ошибся — пиши новое сообщение.
- ❌ **Не удаляй темы вручную** — у бота нет связи topic↔conversation в обратную сторону при удалении. Лучше `/close` — потом архивируется через cleanup-cron (Phase 2).
- ❌ **Не кидай в темы файлы/voice** — Send API в нашем коде пока шлёт только текст. Лид получит только текст. Phase 2 — proxy attachments через `sendDocument`/`sendVoice`.

---

## 8. Troubleshooting

| Симптом | Лечение |
|---|---|
| Нет новых тем при сообщениях лидов | `TELEGRAM_OPERATOR_GROUP_ID` пуст / не та группа. Проверь Railway Variables |
| 400 на createForumTopic в логах | Бот не admin или нет права `manage_topics`. Перевыдай — нужно `Promote to admin → Manage topics ON` |
| Тема есть, но кнопка «Возьму на себя» не нажимается | Старый Telegram-клиент. Обнови. |
| Нажал кнопку — нет реакции | Webhook не получает callback_query. Проверь setWebhook — `allowed_updates` должен включать `callback_query`. У нас уже так: `["message", "callback_query"]`. Если что — выполни `curl` из META_INTEGRATION.md разделом setWebhook |
| Оператор пишет в тему — лиду не приходит | Проверь: (1) `conv.operator_takeover == True` (бот должен сначала сделать takeover); (2) `from.is_bot == false` в payload (если ты пишешь с админ-аккаунта-бота — он не пересылает); (3) канал поддерживает push (website нет — там в БД ждёт) |
| Сразу два сообщения от бота на один запрос лида | Maлoвeроятно — но если случилось, ищи в Railway logs дубль вызова `_handle_message`. Скорее всего Telegram retry'ит из-за non-200 ответа |
| Внезапно бот стал писать «// привет. я — AI-агент» в середине диалога | Это AI-disclosure при первом сообщении. Если уже не первый — баг в `is_first_turn` detection. Проверь что conversation не пересоздалась (см. `get_or_create_conversation`) |

---

## 9. Что под капотом (для второго dev'а)

```
/webhooks/telegram POST  ── разруливает 3 типа update:
  ├── callback_query (нажата кнопка)
  │     → _handle_operator_callback(callback, db)
  │       ├── parse "takeover:<conv_id>" из callback_data
  │       ├── set_operator_takeover(db, conv_id, not current_state)
  │       ├── answer_callback_query (toast)
  │       └── send_to_topic (state change notification)
  │
  ├── message в supergroup топике
  │     → _handle_operator_message(msg, db)
  │       ├── from.is_bot → skip (наш собственный mirror)
  │       ├── /release /close /note — handle as command
  │       └── otherwise → find_conversation_by_topic → send via right channel
  │
  └── обычный private message (лид)
        → parse_telegram_webhook (text+voice) → _handle_message
          ├── lazy create_forum_topic для conversation (если ещё нет)
          ├── mirror user msg в topic
          ├── if operator_takeover → return early, не зовём LLM
          ├── RAG + LLM + persist assistant
          ├── mirror bot reply + кнопка "Возьму на себя"
          ├── handoff check (как раньше)
          └── send_telegram_reply (если answer не пуст)
```

Файлы:
- `channels/telegram.py` — `create_forum_topic`, `send_to_topic`, `answer_callback_query`
- `services/conversations.py` — `link_forum_topic`, `find_conversation_by_topic`, `set_operator_takeover`
- `db/models.py` — `Conversation.operator_takeover`, `Conversation.forum_topic_id`
- `alembic/versions/002_operator_takeover.py` — миграция

---

## 10. Где помощь если упёрся

1. Логи бота: Railway → service → Deployments → активный → Logs. Ищи строки `[<conv-id>/telegram/...] Q: ...` и `[<conv-id>] operator_takeover=true — skipping LLM`.
2. SQL для проверки состояния:
   ```sql
   SELECT id, channel, operator_takeover, forum_topic_id, status, last_message_at
   FROM conversations
   ORDER BY last_message_at DESC LIMIT 20;
   ```
3. Telegram getWebhookInfo — должен показать `allowed_updates: ["message", "callback_query"]`.

Если что-то ломается в проде — пингани, разберёмся.
