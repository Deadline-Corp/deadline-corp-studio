# Week 1 — Phase 1 Foundation

*Бэкенд миграции и identity. Команда: 2 dev'а. Срок: 5-7 рабочих дней.*

---

## Цель недели

К концу Week 1 у нас работает:
- PostgreSQL с pgvector вместо Chroma (старая Chroma остаётся работать в parallel run, не удаляется до Week 3)
- Новая schema: `customers`, `channel_identities`, `conversations`, `messages`, `kb_chunks`
- Universal `/message` endpoint принимает payload в нормализованном формате
- Identity resolver (email-anchor, по channel_id)
- Persistent sessions (вместо in-memory dict)
- Старый `/chat` endpoint остаётся работать для сайта-виджета пока не переведём виджет на новый формат

**Что НЕ делаем в Week 1:** новые каналы (Telegram/IG/FB), Chatwoot, Attio. Это Week 2-3.

---

## Day 1 — Postgres setup + schema (готовится мной сейчас)

### Что делает Dev #1 (бэкенд / DB)

1. Провизионить Postgres:
   - **Railway:** `+ New` → `Database` → `PostgreSQL` (~$5/мес)
   - В переменных Railway автоматически появятся `DATABASE_URL`, `PGHOST`, `PGUSER`, ...
   - **Альтернатива (бесплатно для старта):** Supabase free tier — даёт Postgres с pgvector предустановленным
2. Включить расширение pgvector:
   ```sql
   CREATE EXTENSION IF NOT EXISTS vector;
   CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
   ```
3. Установить новые зависимости:
   ```bash
   pip install -r requirements.txt  # обновится с моими файлами
   ```
4. Запустить alembic миграцию:
   ```bash
   alembic upgrade head
   ```
5. Smoke-тест: подключиться к БД через `python -c "from db.connection import engine; ..."`

### Что делает Dev #2 (channels)

В это время dev #2 готовит инфру для channel adapters:
1. Зарегистрировать Telegram бот через @BotFather. Получить `TELEGRAM_BOT_TOKEN`. Тестировать `getUpdates`.
2. Проверить, что у Meta-приложения есть permissions:
   - `instagram_business_manage_messages`
   - `pages_messaging`
   - Если нет — submit через App Review немедленно (это идёт 1-2 недели)
3. Подготовить webhook endpoint на Railway (заглушка): `POST /webhooks/telegram`, `POST /webhooks/instagram`, `POST /webhooks/messenger` — пока возвращают 200, реальная логика в Day 4.

---

## Day 2 — Ingest на pgvector

### Dev #1

1. Написать `ingest_pg.py` — параллельный к существующему `ingest.py`, но кладёт в pgvector вместо Chroma
2. Migrate существующие KB chunks из Chroma → pgvector через один запуск
3. Smoke-тест retrieval через новый код:
   ```python
   from db.vector import similarity_search
   results = similarity_search("делаете ли AI-агентов?", k=4)
   ```
4. Сравнить retrieval-quality старого (Chroma) и нового (pgvector) на тех же 14 запросах из `test_retrieval.py`. Должно быть **примерно одинаково**

### Dev #2

1. Изучить Telegram Bot API webhook flow
2. Написать `channels/telegram.py` — функция `parse_telegram_webhook(payload) -> NormalizedMessage` (заглушка, без отправки ответа)
3. Локально через `getUpdates` тестировать форматы входящих сообщений (текст, медиа, voice)

---

## Day 3 — Identity resolver + universal /message endpoint

### Dev #1

1. `services/identity.py`:
   - `resolve_or_create_customer(channel, external_id, email=None) -> Customer`
   - `link_identity(customer_id, channel, external_id, username=None)`
   - `find_customer_by_email(email) -> Customer | None`
2. `services/conversations.py`:
   - `get_or_create_conversation(customer_id, channel, channel_conversation_id) -> Conversation`
   - `append_message(conversation_id, role, content, metadata=None)`
   - `get_recent_messages(conversation_id, limit=10)`
3. Unit-тесты на identity:
   - Лид пишет с TG → customer создан, identity{tg} привязан
   - Лид даёт email в диалоге → email сохранён, customer обновлён
   - Лид пишет с website с тем же email → identity{website} линкуется к **тому же** customer'у
   - Лид пишет с IG c новым external_id → новый customer (правильно, email ещё не дан)

### Dev #2

1. `channels/instagram.py` — parsing webhook payload IG DM
2. `channels/messenger.py` — parsing webhook payload FB Messenger
3. Изучить 24h window правила Meta — фиксировать в `channels/utils.py` функцию `is_proactive_allowed(last_user_message_at)`

---

## Day 4 — /message endpoint + Telegram live

### Dev #1

1. Новый endpoint в `main.py`:
   ```python
   @app.post("/message", response_model=MessageResponse)
   async def message(req: MessageRequest):
       # 1. Resolve/create customer through identity
       # 2. Get/create conversation
       # 3. RAG retrieval from pgvector
       # 4. LLM call with full conversation history from DB
       # 5. Append messages to DB
       # 6. Handoff check
       # 7. Return response
   ```
2. Migrate handoff logic — теперь brief берётся из conversations таблицы, не из in-memory
3. Сохранить старый `/chat` endpoint как deprecated alias на `/message` для обратной совместимости виджета

### Dev #2

1. Telegram webhook live:
   - `POST /webhooks/telegram` принимает payload
   - Парсит через `channels/telegram.py`
   - Конвертирует в `MessageRequest`
   - Вызывает `/message`
   - Отправляет ответ обратно через Telegram Bot API
2. Установить webhook через `setWebhook` API: `https://deadline-sales-bot-production.up.railway.app/webhooks/telegram`
3. Smoke-тест: написать боту в Telegram → получить ответ → проверить, что customer создался в БД

---

## Day 5 — IG + FB Messenger live

### Dev #2 (главный по этому дню)

1. FB Messenger webhook + verify signature
2. IG DM webhook + 24h window enforcement + rate limit guard (200/час)
3. Установить webhooks через Meta Webhook Subscriptions
4. Smoke-тест: написать боту в IG → получить ответ → проверить identity и conversation в БД

### Dev #1

1. AI Act disclosure в первом сообщении бота (поправка `prompts.py`)
2. Логирование metrics:
   - response_time_ms на каждый /message
   - daily_active_conversations
   - channel_breakdown (сколько лидов с какого канала)
3. Простой endpoint `GET /metrics` (для дебага, без auth — Phase 2 добавим protection)

---

## Day 6-7 — Polish + smoke-test

### Оба

1. End-to-end тест: написать с IG → получить ответ → пройти диалог до handoff → проверить Telegram-алерт админу
2. End-to-end тест identity: тот же тестер пишет с TG (другой external_id) → бот не распознает (правильно, email ещё нет) → дать email → бот распознает на следующем сообщении
3. Поправить retrieval (если за Day 2 был drift)
4. Обновить README с новыми переменными и инструкциями
5. Deploy на Railway
6. Smoke-тест на production: реальные сообщения с тестового IG-аккаунта

---

## Что на выходе

К концу Week 1:
- ✅ Postgres + pgvector работает
- ✅ Identity resolution работает
- ✅ Telegram + IG + FB Messenger принимают сообщения и отвечают через единый бэкенд
- ✅ Все диалоги в одной БД
- ✅ Старый сайт-виджет продолжает работать через deprecated `/chat`
- ✅ Простые метрики для проверки

К концу Week 1 **НЕ работает** (это Week 2-3):
- ❌ Chatwoot inbox
- ❌ Attio CRM sync
- ❌ Базовый скоринг лидов (P1-P6)
- ❌ Температура и decay
- ❌ Voice сообщения через Whisper
- ❌ Pre-call card
- ❌ Discount suggest-approve flow

---

## Critical Path и риски

| Риск | Mitigation |
|---|---|
| Постгрес provisioning тормозит (Railway или Supabase лагают) | Day 1: оба dev'а работают параллельно — пока Dev#1 ждёт Postgres, Dev#2 готовит Telegram |
| Meta App Review не подтвердил permissions | Day 1 — submit немедленно. Если откажут — escalate через FB Developer Support. Параллельно работает Telegram (он бесплатный и не требует review) |
| Identity resolver edge cases (race condition, дубли) | Unit-тесты в Day 3 покрывают 4 главных сценария + UNIQUE constraints в БД защищают от races |
| Деградация retrieval quality после Chroma → pgvector | Day 2 — sanity-чек на 14 тестовых запросах. Если деградация > 20% — корректируем chunk_size / embedding params |
| Сломаем live website widget при переходе на новый endpoint | Day 4: старый `/chat` остаётся как alias. Widget переключается на `/message` только в Week 2 после полного теста |
| Postgres free tier (Supabase) лимит | Если упрёмся — миграция на Railway Postgres за 30 минут (pg_dump + pg_restore) |

---

## Что нужно от тебя ДО старта Day 1

1. Решить: **Railway Postgres ($5/мес)** или **Supabase free tier**?
2. Подтвердить, что у Meta-приложения есть permissions `instagram_business_manage_messages` и `pages_messaging` (либо submit прямо сейчас)
3. Создать Telegram бота через @BotFather, получить токен
4. Подтвердить, что готов к ситуации "сайт-виджет работает по deprecated path Week 1-2"

После этого — пишу актуальный код Day 1.
