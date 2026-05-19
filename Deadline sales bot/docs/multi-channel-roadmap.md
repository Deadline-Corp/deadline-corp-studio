# Multi-Channel Bot Roadmap для Deadline

*Версия 2 · 18 мая 2026 · Обновлено: Meta approval готов, DM-driven traffic из соцсетей, single-tenant сейчас, multi-tenant позже*

---

## TL;DR — Жёстко по делу

1. **Правильный паттерн: один backend + adapter'ы каналов.** Не "один бот на всё" и не "много ботов". Channel adapter получает webhook → нормализует payload → отдаёт в FastAPI → ответ возвращается в свой канал. Это стандарт 2026.
2. **Параллельный запуск Telegram + IG + FB Messenger в Phase 1.** Раз Meta approval уже получен, а трафик пойдёт с ad creative с DM-CTA — все три канала запускаются разом за **2-3 недели**. Не последовательно.
3. **Telegram — 1-2 часа работы.** Никаких одобрений, нет 24h окна, бесплатно. Технически проще всех.
4. **IG/FB ограничения остаются даже с approval'ом:** 200 DM/час, 24h окно для proactive сообщений, 1-trigger-per-user-per-24h. Это надо заложить в код.
5. **WhatsApp откладывается до явной потребности.** С июля 2025 — per-message billing. Marketing-сообщения $0.025-$0.136 каждое. Запускать только когда лиды реально просят "напишите мне в WA".
6. **Chroma → PostgreSQL + pgvector.** Один DB вместо двух. На вашей шкале (под 10M векторов) разницы в производительности нет, а ops упрощается серьёзно.
7. **CRM: Attio (free для 3 seats), не HubSpot.** HubSpot free план блокирует поведенческие webhooks.
8. **Multi-tenancy откладывается** — single-tenant сейчас, multi-tenant rewrite **только когда 3-5 paying customers сами захотят такого бота**. Не строй framework до того, как есть рынок.

---

## Архитектура — рекомендуемый паттерн

```
┌──────────────────────────────────────────────────────────────┐
│  CHANNELS — параллельно, не последовательно                  │
│  Instagram DM  ──┐                                           │
│  FB Messenger  ──┤                                           │
│  Telegram      ──┼───→  n8n (channel router)                 │
│  Website chat  ──┤      • verify webhook signature           │
│  WhatsApp (P2) ──┘      • normalize payload                  │
│                         • enforce 24h window if applicable   │
│                         • rate-limit IG (200/час, 1/user/24h)│
│                                  │                           │
│                                  ▼                           │
│                         FastAPI /message endpoint            │
│                         {channel, external_user_id,          │
│                          message, conversation_id}           │
│                                  │                           │
│           ┌──────────────────────┼──────────────────────┐    │
│           ▼                      ▼                      ▼    │
│      Identity resolver      LangChain agent       Chatwoot   │
│      (customer_id +         RAG over pgvector     (unified   │
│       channel_id link)      OpenRouter LLM        inbox      │
│           │                 history & summary     для команды)│
│           │                 handoff trigger              │   │
│           ▼                      │                       │   │
│       PostgreSQL + pgvector      │                       │   │
│       (один DB для всего)        │                       │   │
│       • customers                │                       │   │
│       • channel_identities       │                       │   │
│       • conversations            │                       │   │
│       • messages (+embeddings)   │                       │   │
│       • kb_chunks (replaces      │                       │   │
│          Chroma)                 │                       │   │
│                                  ▼                       │   │
│                          Attio CRM webhook ◄─────────────┘   │
│                          (auto-create lead on handoff)       │
└──────────────────────────────────────────────────────────────┘
```

### Почему именно так

- **FastAPI остаётся ядром.** Твой текущий код почти не меняется — добавляешь один endpoint, который принимает нормализованный payload.
- **n8n — НЕ устарел.** Активно развивается, $60M funding, 60k+ stars, индустриальный стандарт 2026 для channel routing. Это **не AI оркестратор**, это парсер webhook'ов и нормализатор payload'ов. Бесплатный self-hosted, ставится в Docker рядом с Chatwoot. Альтернативы для multi-tenant продуктов (когда дойдёшь до этой стадии) — нативные Python adapter'ы.
- **OpenClaw и Hermes AI agent ≠ замена n8n.** OpenClaw — CLI-инструмент для агентного кодинга (видел на Ollama-странице как "Application" рядом с Claude Code, Codex). Hermes — серия fine-tuned моделей от Nous Research. Оба решают совершенно другие задачи.
- **Chatwoot — для людей, не для AI.** Когда бот делает handoff — оператор открывает Chatwoot inbox и видит весь диалог (из любого канала). Сам Chatwoot не запускает LLM.
- **pgvector вместо Chroma** убирает второй database. Меньше ops, и SQL-запросы могут фильтровать по каналу + векторное сходство в одном запросе.

---

## Каналы — таблица сложности (с учётом готового Meta approval)

| Канал | Время на запуск | Главные ограничения | Стоимость |
|---|---|---|---|
| **Website** | Готово | — | $0 incremental |
| **Telegram** | 1-2 часа | 30 msg/sec broadcast, без 24h окна, без одобрений | **Бесплатно** |
| **FB Messenger** | 2-3 дня | 24h окно для promo, deprecated message tags (апрель 2026), opt-in для marketing | **Free API** (approval готов) |
| **Instagram DM** | 3-5 дней | 24h окно, **200 DM/час**, 1 автомат-DM на пользователя в 24h от триггера | **Free API** (approval готов) |
| **WhatsApp Cloud API** | 2-4 недели (Phase 2) | Pre-approval шаблонов, per-message billing | **$20-$200+/мес** |

⚠️ **Проверка перед стартом IG/FB:** убедись, что у одобренного Meta-приложения **есть конкретные permissions**:
- `instagram_business_manage_messages` — для автоматизации IG DM
- `pages_messaging` — для FB Messenger автоматизации

Если только general App Review без этих scopes — придётся проходить дополнительный review (1-2 недели).

---

## Identity Resolution — практичный план

**Реальность:** Полностью автоматически связать пользователя через каналы (IG ↔ email ↔ phone) **нельзя**. Это требует, чтобы клиент сам дал данные. Хранение таких связей без consent — нарушение GDPR.

### Схема базы

```sql
-- Главный идентификатор клиента
CREATE TABLE customers (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  email         TEXT UNIQUE,           -- основной anchor для merge'а
  name          TEXT,
  phone         TEXT,
  first_channel TEXT,                  -- 'website' | 'telegram' | ...
  utm_source    TEXT,                  -- откуда пришёл лид (ad campaign)
  utm_campaign  TEXT,
  created_at    TIMESTAMPTZ DEFAULT now()
);

-- Channel-specific IDs привязанные к customer
CREATE TABLE channel_identities (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  customer_id  UUID REFERENCES customers(id) ON DELETE CASCADE,
  channel      TEXT NOT NULL,
  external_id  TEXT NOT NULL,
  username     TEXT,
  linked_at    TIMESTAMPTZ DEFAULT now(),
  UNIQUE(channel, external_id)
);

-- Все разговоры из всех каналов
CREATE TABLE conversations (
  id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  customer_id              UUID REFERENCES customers(id),
  channel                  TEXT NOT NULL,
  channel_conversation_id  TEXT,
  status                   TEXT DEFAULT 'open',
  summary                  TEXT,
  last_message_at          TIMESTAMPTZ,
  created_at               TIMESTAMPTZ DEFAULT now()
);

-- Все сообщения + их эмбеддинги для semantic search
CREATE TABLE messages (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  conversation_id UUID REFERENCES conversations(id) ON DELETE CASCADE,
  role            TEXT NOT NULL,
  content         TEXT NOT NULL,
  metadata        JSONB,
  embedding       VECTOR(1024),         -- bge-m3 = 1024 dim
  created_at      TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX ON messages USING hnsw (embedding vector_cosine_ops)
  WITH (m = 16, ef_construction = 64);

-- KB chunks (заменяет Chroma)
CREATE TABLE kb_chunks (
  id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  source    TEXT NOT NULL,
  content   TEXT NOT NULL,
  embedding VECTOR(1024)
);

CREATE INDEX ON kb_chunks USING hnsw (embedding vector_cosine_ops);
```

**Подготовка к multi-tenancy позже:** обрати внимание — схема уже модульная. Когда (если) пойдёшь в SaaS-продукт, добавишь `tenant_id` в каждую таблицу и Row-Level Security в Postgres. Сейчас не делать.

### Процедура linking

1. Лид пишет с Instagram DM (`@ivan_ig`, ig_user_id=98765, из IG ad). Создаём `customer` без email, `channel_identity{instagram, 98765, ivan_ig}`, `utm_source='instagram_ads'`.
2. В диалоге бот спрашивает email + Telegram username для следующих коммуникаций. Лид даёт `ivan@gmail.com` и `@ivan_petrov`.
3. `UPDATE customers SET email='ivan@gmail.com'`; добавляем `channel_identity{telegram, ivan_petrov}`.
4. Через неделю тот же `ivan@gmail.com` пишет с сайта или в Telegram. Adapter ищет `customer WHERE email='ivan@gmail.com'` или по telegram_id → находит → новый `channel_identity{website, sess_xyz}` привязывается к **тому же** `customer_id`.
5. RAG-агент видит всю историю по `customer_id` — даже из других каналов: "Иван писал нам неделю назад из IG про booking-платформу, тип проекта Web + AI".

### GDPR — что важно

- `instagram_user_id`, `fb_messenger_psid` — это personal data
- Нужен Privacy Policy с упоминанием cross-channel storage
- Право на удаление (delete cascade в схеме поддерживает)
- TTL на conversations 12 месяцев max если customer не превратился в клиента

---

## CRM — рекомендация

**Берём Attio.** Free план 3 seats + 50K records — закроет первый год точно.

| | HubSpot Free | Pipedrive Lite | **Attio Free** | Custom Postgres |
|---|---|---|---|---|
| Стоимость | $0 (limited) | $14/seat/mo | **$0 / $29/seat** | $0 + dev time |
| Контакты | 1000 hard cap | unlimited | 50K | unlimited |
| Webhooks behavioral | ❌ blocked в free | ✅ all plans | ✅ all plans | DIY |
| API quality | OK | Лучший на рынке | Modern REST + OAuth2 | DIY |
| Подходит Deadline | ⚠️ контактный cap бьёт быстро | ✅ если упор на sales pipeline | ✅ **modern, гибкий** | ⚠️ требует cycles |

---

## Phased Rollout — что когда

### Phase 1 — Full Multi-Channel Foundation (2-3 недели)

**Триггер запуска:** прямо сейчас. Меняем стратегию с "подождём органику" на "трафик уже идёт с ad campaigns, бот нужен везде сразу".

**Параллельно:**

**Track A — Backend foundation (Week 1):**
- Миграция Chroma → pgvector (migration script)
- Деплой новой схемы БД (customers / identities / conversations / messages / kb_chunks)
- Универсальный endpoint `/message` с нормализованным payload
- Identity resolver: lookup по email или channel_id → создание/обновление customer
- Conversation summarization job (когда conv > 20 сообщений)

**Track B — Каналы (Week 1-3):**
- **Telegram adapter (1-2 часа)** — через @BotFather, webhook на /message
- **FB Messenger adapter (2-3 дня)** — Page subscription, webhook signature verification, 24h window guard
- **Instagram DM adapter (3-5 дней)** — Business account webhook, rate limit (200/час hard cap), 1-DM-per-user-per-24h guard
- Все три через n8n как router/normalizer

**Track C — Infrastructure (Week 2-3):**
- Chatwoot self-hosted в Docker (Postgres + Redis + Rails app, +$20-30/мес на VPS)
- Подключаешь все три канала к Chatwoot inbox → команда видит все диалоги в одном месте
- Attio account + webhook на handoff
- UTM-параметры пробрасываются от ad-campaign до customer record

**Стоимость Phase 1:** ~$60-100/мес total

**Критерий завершения:**
- Все три канала отвечают на тестовые сообщения
- При handoff'е лид появляется в Attio с правильным utm_source
- В Chatwoot видны диалоги из всех трёх каналов одинаково
- Identity resolver работает: если лид написал с IG и потом с Telegram, давая один email — бот видит общую историю

### Phase 2 — WhatsApp (только при явной потребности)

**Жёсткий критерий:** есть минимум 5 лидов, которые САМИ просили "напишите мне в WhatsApp" в других каналах. Без этого — пропускаешь.

**Что делаешь:**
- Регистрируешь WABA на Deadline через Meta Business Suite
- Submitt'ишь 3-5 utility шаблонов на approval (1-3 недели)
- WhatsApp Cloud API adapter (плюс к существующим)
- Spending monitor с алертом если > $50/мес на marketing шаблонах

**Стоимость:** $125-390/мес (WhatsApp per-message billing)

### Phase 3 — Multi-Tenant Rewrite (только при validated demand)

**Жёсткий критерий:** минимум 3-5 платежеспособных клиентов Deadline-проектов **сами** просят "хочу такого же бота для своего бизнеса". Без этого — пропускаешь и фокусируешься на сервис-модели.

**Что меняется (грубо):**
- `tenant_id` в каждую таблицу + Row-Level Security
- Onboarding flow для нового клиента (KB upload, channels config, brand-voice setup)
- Per-tenant Stripe billing
- Admin panel для управления tenants
- Изоляция KB, prompts, channels между tenants
- API для self-service настройки

**Это полноценный SaaS-продукт.** 2-4 месяца работы команды 1-2 dev'ов.

**Альтернатива (рекомендую):** **service-based модель.** Не делаешь продукт, а **продаёшь сервис**: "построим вам такого же бота за 6 недель, потом support". Это **уже ваш бизнес-модель Deadline**. Каждый бот = отдельный fork/deploy под клиента. Никакой multi-tenancy. Дороже за единицу, но **без риска потратить 4 месяца на SaaS, который никто не купит**.

---

## Стоимость на разных стадиях

| Сценарий | Канал(ы) | Total/мес |
|---|---|---|
| Сейчас (Phase 0) | Web | $30-40 |
| Phase 1 done | Web + TG + IG + FB | **$60-100** |
| Phase 2 done | + WhatsApp | $125-390 |
| Scale 1000+ leads/мес | Все 5 | $367-767 |
| Phase 3 (SaaS) | + multi-tenant ops | $500-2000+ (зависит от scale) |

**Скрытые расходы:**
- WhatsApp шаблоны marketing: $0.025-$0.136 за каждое сообщение. 1000 promo = $25-$136
- Chatwoot self-hosted: 2-4 часа initial + 1-2 часа в месяц на обновления
- n8n self-host на том же VPS что Chatwoot — иначе +$20/мес n8n Cloud
- IG/FB token refresh: токены живут 60 дней, нужен auto-refresh скрипт

---

## Что я бы сделал на твоём месте — конкретный план на ближайшие 3 недели

### Week 1 — Backend foundation
**Day 1-2:**
- Проверить, что Meta-приложение реально имеет permissions `instagram_business_manage_messages` и `pages_messaging` (если нет — подать запрос немедленно, идёт 1-2 недели)
- Создать миграционный скрипт Chroma → pgvector. Развернуть Postgres рядом с FastAPI (Railway addon — Postgres $5/мес, либо Supabase free tier)
- Деплоить новую схему БД (customers, channel_identities, conversations, messages, kb_chunks)

**Day 3-5:**
- Универсальный endpoint `/message` принимает `{channel, external_user_id, message, channel_conversation_id, metadata}`
- Identity resolver: при каждом сообщении находим/создаём customer, добавляем channel_identity
- Адаптировать существующий handoff на Attio (вместо одного только Telegram)

### Week 2 — Telegram + FB Messenger
**Day 6-7:**
- Telegram bot через @BotFather, простой webhook на /message
- Telegram → /message работает end-to-end

**Day 8-10:**
- Регистрация FB Messenger webhook на странице Deadline
- 24-часовое окно: enforcement в n8n (не пропускать proactive messages если последнее сообщение от юзера >24h)
- FB Messenger → /message работает end-to-end

### Week 3 — Instagram + Chatwoot
**Day 11-13:**
- Instagram DM webhook
- Rate limiter в коде: max 200 DM/час (queue с backpressure), 1-trigger-per-user-per-24h guard
- IG DM → /message работает end-to-end

**Day 14-17:**
- Chatwoot self-hosted Docker compose на VPS
- Подключение всех трёх каналов к Chatwoot inbox
- Команда видит все диалоги из IG/FB/Telegram + сайт в одном инбоксе

**Day 18-21:**
- Attio webhook на handoff с utm_source tagging
- Тесты cross-channel identity: один тестер пишет с IG → потом с Telegram → бот должен распознать
- A/B на ad creative с двумя CTA ("кликни на сайт" vs "DM нам") — смотришь, какой канал лучше конвертит

---

## Параллельная задача — ad creative с правильным tracking

Раз ты льёшь платный трафик — заложи в каждое creative:

| Поле | Значение | Зачем |
|---|---|---|
| Custom UTM `utm_source` | `instagram_ads` / `facebook_ads` / `telegram_ads` | Знать, откуда пришёл лид |
| Custom UTM `utm_campaign` | название кампании | A/B тесты |
| Custom UTM `utm_content` | вариант creative | какой creative работает лучше |
| Click destination | для сайт-CTA: `deadlinecorp.com/?utm_source=...` | UTM попадает в customer record |
| DM CTA | "Напишите нам в @deadline_corp" | Direct DM funnel |

Для IG/FB ads с DM-CTA: Meta автоматически прикрепляет `ad_id` в payload входящего сообщения через `referral` field. Это можно мапить на campaign → utm_source автоматически.

---

## Что НЕ делать

- ❌ **Не делать multi-tenancy сейчас.** Single-tenant работает быстрее, проще, и риск ниже. Делать SaaS только когда есть 3-5 платежеспособных потенциальных клиентов
- ❌ **Не использовать OpenClaw / Hermes как "оркестраторы каналов"** — это не их задачи
- ❌ **Не пускать identity resolution без email-anchor** — параллельные профили одного человека
- ❌ **Не запускать WhatsApp marketing шаблоны без spending monitor** — улетит бюджет
- ❌ **Не пытаться обойти 200 DM/час лимит IG** — Meta банит. Очередь с backpressure — единственный правильный путь
- ❌ **Не хранить identity-данные дольше нужного** — TTL 12 месяцев

---

## Что я обновил в этой версии (v2)

- Phase 1 теперь — параллельный запуск Telegram + IG + FB Messenger (не последовательно)
- Убрана рекомендация "подождать 2 недели" — Phase 1 стартует сейчас, под платный трафик
- Исправлен миф про "n8n устарел" — он не устарел, и это правильный выбор для single-tenant
- Добавлен раздел про multi-tenancy как Phase 3 с честным предупреждением о рисках
- Добавлен раздел про UTM tracking для платного трафика
- Уточнены Meta permissions, которые нужно подтвердить (instagram_business_manage_messages, pages_messaging)
- Альтернатива multi-tenant SaaS: service-based модель (продаёшь как услугу, не как продукт)

---

## Источники (unchanged)

- [Instagram DM Automation Rules 2026 — Spur](https://www.spurnow.com/en/blogs/instagram-dm-automation-rules)
- [Instagram Messaging API 24-Hour Window — KeyAPI](https://www.keyapi.ai/blog/instagram-messaging-api-policy)
- [Instagram DM Compliance 2026 — CreatorFlow](https://creatorflow.so/blog/instagram-dm-compliance-meta-rules/)
- [Messenger Platform Policy — Meta Developers](https://developers.facebook.com/documentation/business-messaging/messenger-platform/policy)
- [WhatsApp Business Platform Pricing — Meta Developers](https://developers.facebook.com/documentation/business-messaging/whatsapp/pricing)
- [Chatwoot Self-Hosted](https://www.chatwoot.com/pricing/self-hosted-plans/)
- [Chatwoot Instagram Setup](https://developers.chatwoot.com/self-hosted/configuration/features/integrations/instagram-channel-setup)
- [Telegram Bot API](https://core.telegram.org/bots/api)
- [pgvector vs ChromaDB — Elestio](https://blog.elest.io/pgvector-vs-chromadb-when-to-extend-postgresql-and-when-to-go-dedicated/)
- [Attio CRM Review 2026 — Hackceleration](https://hackceleration.com/attio-review/)
- [Attio API Documentation](https://attio.com/)
- [n8n GitHub](https://github.com/n8n-io/n8n)
- [LLM Chat History Summarization 2025 — Mem0](https://mem0.ai/blog/llm-chat-history-summarization-guide-2025)
- [GDPR Cross-Platform Messaging — SyncRivo](https://syncrivo.ai/en/blog/gdpr-cross-platform-messaging-data-residency-obligations)
