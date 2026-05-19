# Подключение Instagram + Facebook Messenger к боту

Step-by-step для второго dev'а. Бот уже задеплоен и слушает webhook'и — нужно настроить Meta App, выдать permissions, подписать webhooks и положить 3 токена в Railway. После этого бот:
- **отвечает на DM в Instagram и Facebook Messenger** (full sales-qualification flow с email-handoff и брифом операторам в Telegram)
- **отвечает на публичные комментарии под постами IG и FB Page** (короткое сообщение + редирект в Direct, без сбора контакта, без handoff)

Общее время настройки — 30-60 минут если Meta App уже создан и Permissions Approved. Если permissions ещё не одобрены — Meta App Review занимает 5-10 рабочих дней.

---

## 0. Что должно быть готово до начала

| Артефакт | Где взять | Зачем |
|---|---|---|
| Meta Developer аккаунт | https://developers.facebook.com | Без него ничего |
| Facebook Page | facebook.com → твоя Page | Сюда подключается Messenger, FB-комменты приходят с этой Page |
| Instagram Business (или Creator) account | Linked к FB Page через Page Settings → Linked Accounts | Источник IG DM и IG-комментов |
| Бот живой | `curl https://deadline-sales-bot-production.up.railway.app/health` → `{"ok":true, ...}` | Webhook'и не подпишутся если backend down |

> **Важно**: IG-аккаунт обязательно **Business / Creator**, не Personal. Иначе IG Graph API не работает и DM/comments не приходят.

---

## 1. Создаём (или открываем) Meta App

1. https://developers.facebook.com/apps → **Create App** (если ещё нет) или открываем существующий
2. **Type**: `Business`
3. Привязать к Business portfolio (можно создать новый Deadline-Corp Business)
4. После создания — переходим в **App Dashboard**

---

## 2. Permissions (App Review)

Подаём заявку через **App Review → Permissions and Features**. Нам нужны:

| Permission | Зачем |
|---|---|
| `pages_messaging` | принимать и отправлять Messenger DM |
| `pages_show_list` | бэкенд должен видеть Page id |
| `pages_manage_metadata` | подписаться на feed-webhook (FB-комменты) |
| `pages_read_engagement` | читать комментарии и реакции |
| `pages_manage_engagement` | **отвечать на комментарии** под постами FB Page |
| `instagram_basic` | базовое подключение IG account |
| `instagram_manage_messages` | принимать и отправлять IG DM |
| `instagram_manage_comments` | **отвечать на комментарии** под постами IG |

Каждую permission нужно объяснить use-case'ом — для нас это: «Deadline AI sales agent отвечает клиентам через все каналы единообразно, собирает бриф проекта, передаёт команде в Telegram оператору».

> Если хочешь сначала протестировать на закрытом круге — можно перевести App в **Development Mode** и работать без App Review с собой и Page admin'ами как тестовыми пользователями. На прод — обязательно Live Mode + Approved permissions.

---

## 3. Базовые tokens

### 3.1. App Secret
1. **App Settings → Basic** → поле **App Secret** → Show → скопировать
2. Это уйдёт в Railway как `META_APP_SECRET` (используется для HMAC-проверки подписи каждого incoming webhook)

### 3.2. Page Access Token
1. **Messenger → Settings → Access Tokens** → выбрать твою FB Page → **Generate Token**
2. Сгенерится токен формата `EAAB...` (долгоживущий, привязан к Page)
3. Этим же токеном работают и IG DM, и FB-комменты, и IG-комменты — он единый для всех Meta-операций нашего бота
4. Это уйдёт в Railway как `META_PAGE_ACCESS_TOKEN`

### 3.3. Verify Token
1. Это просто **рандомная строка**, которую мы придумаем сами (например `deadline-meta-verify-2026-x7q9k2`)
2. Должна быть одинаковой и в Railway (`META_VERIFY_TOKEN`), и в Meta App Webhooks-конфиге
3. Meta присылает её при первом GET-запросе на наш webhook — мы проверяем что она равна нашей, и эхо-отвечаем `hub.challenge`
4. Можно генерировать через `openssl rand -hex 16` или просто придумать

---

## 4. Webhook'и — Messenger (DM + FB-комменты)

### 4.1. DM (Messenger)

1. **Messenger → Settings → Webhooks** → **Add Callback URL**
2. **Callback URL**: `https://deadline-sales-bot-production.up.railway.app/webhooks/messenger`
3. **Verify Token**: та же строка что в `META_VERIFY_TOKEN`
4. **Subscription fields** (галочки):
   - `messages` — приём DM текстов
   - `messaging_postbacks` (опционально — для кнопок, пока не используем)
5. Сохранить → Meta сделает GET на наш URL, получит `hub.challenge`, увидит 200 → подписка активна
6. Внутри Messenger → **Add Page** → выбрать твою FB Page → подтвердить (это **обязательно**, без этого DM не приходят)

### 4.2. FB-комменты под постами Page

В том же блоке Messenger → Webhooks **НЕ конфигурируется**! Для feed-комментариев нужен **Pages Product** в Meta App:

1. **Add Product** → **Webhooks** (если ещё нет в проекте)
2. В Webhooks-секции выбрать **Page** объект
3. **Subscription fields**: `feed`
4. **Callback URL** + **Verify Token** — те же что для Messenger (один webhook handler у нас обслуживает оба типа событий — DM и комменты, парсер сам разруливает)
5. Подписаться на свою Page

Альтернативный путь (новее в Meta UI): **App Dashboard → Webhooks → Page → Edit Subscription → `feed`** — то же самое.

---

## 5. Webhook'и — Instagram (DM + IG-комменты)

### 5.1. IG DM

1. **Instagram → Settings → Webhooks** → **Add Callback URL**
2. **Callback URL**: `https://deadline-sales-bot-production.up.railway.app/webhooks/instagram`
3. **Verify Token**: та же строка
4. **Subscription fields**:
   - `messages` — приём IG DM
5. Сохранить → Meta GET-verify → 200

### 5.2. IG-комменты

В том же Instagram → Webhooks секции:

1. **Edit Subscription** → добавить галку `comments`
2. Сохранить

> Для IG-комментов в Meta UI иногда нужно дополнительно зайти в **Instagram → Business → Linked accounts** и убедиться что linked IG-account имеет статус Business / Creator, иначе comments-webhook молчит.

---

## 6. Кладём 3 токена в Railway

Открой Railway-проект → service `deadline-sales-bot` → **Variables** → **Raw Editor** → дописать:

```
META_VERIFY_TOKEN=deadline-meta-verify-2026-x7q9k2
META_APP_SECRET=<скопированный из App Settings → Basic>
META_PAGE_ACCESS_TOKEN=<скопированный из Messenger → Settings → Access Tokens>
```

После Save Railway автоматически перезапустит контейнер (~30-60 сек). Проверь: `curl https://deadline-sales-bot-production.up.railway.app/health` должен вернуть 200.

> **Без этих 3 переменных всё, что ниже, ломается так**:
> - `META_VERIFY_TOKEN` пуст → GET-verify webhook'а в Meta UI возвращает 403, подписка не сохраняется
> - `META_APP_SECRET` пуст → POST-webhook'и принимаются без HMAC-проверки (dev mode, риск spoofing — НЕЛЬЗЯ для прода)
> - `META_PAGE_ACCESS_TOKEN` пуст → бот получает входящие, но ответить не может

---

## 7. Проверка end-to-end

### 7.1. DM-тест Messenger
1. Открыть FB Messenger → найти твою Page → написать любое сообщение
2. Ожидается ответ через 3-5 сек в формате: `// привет. я — AI-агент Deadline, помогу собрать бриф. что у вас за проект — web, automation или AI agents?`
3. Если дать email в диалоге — бот скажет `// передал команде. напишем на email в течение минут. 📩` и в Telegram-чате оператора (chat_id `7433321014`) появится brief с полями Email/Telegram/Phone/Срочность/полный диалог

### 7.2. DM-тест Instagram
1. Открыть IG в приложении → твою Business-страницу → написать DM
2. Тот же ответ, только канал `instagram`

### 7.3. Comments-тест FB Page
1. На FB Page опубликовать любой пост (хоть test)
2. Из другого аккаунта оставить коммент: `а вы AI агентов делаете?`
3. Ожидается **публичный** reply-комментарий от Page через 3-5 сек: `// делаем регулярно — VRP, KeyDrop в кейсах. напишите в Direct, расскажу детали под вашу задачу.`
4. В Telegram-чате оператора **НИЧЕГО не появляется** — это правильно, комменты не идут в брифы

### 7.4. Comments-тест Instagram
1. На IG-аккаунте Business опубликовать пост
2. Из другого IG-аккаунта оставить коммент: `делаете лендинги?`
3. Ожидается публичный reply через 3-5 сек, тот же стиль

### 7.5. Проверка БД
После 4 тестов выше — в Postgres должны появиться 4 customer'а:

```bash
curl https://deadline-sales-bot-production.up.railway.app/metrics
```

В `by_channel` должны быть `messenger` и `instagram` ненулевыми. В `by_status` `open` увеличится на 4 (или меньше, если кто-то дал email и `handed_off`).

---

## 8. Troubleshooting

| Симптом | Лечение |
|---|---|
| GET-verify webhook → 403 | `META_VERIFY_TOKEN` в Railway не совпадает с тем что вписано в Meta UI Webhook |
| GET-verify → timeout | Railway service спит или упал. `curl /health` → если не 200, посмотри в Railway → Deployments → Logs |
| DM приходит, ответа нет | `META_PAGE_ACCESS_TOKEN` пуст / expired / не на ту Page. Проверь Messenger → Settings → Access Tokens, перегенерируй |
| Ответ на comment не приходит | Permissions `instagram_manage_comments` или `pages_manage_engagement` не Approved. Проверь App Review → Permissions |
| Бот отвечает сам себе циклом | Maлoverояtno (наш парсер фильтрует `is_echo` для DM и `from.id == page_id` для комментариев), но если случилось — посмотри логи Railway, скорее всего edge-case payload |
| Comment был, но webhook не пришёл | Меню Webhooks → Page → Recent Deliveries — увидишь pending или failed delivery. Если 4xx — Meta зафрорила, проверь permissions. Если 5xx — посмотри Railway logs |
| Несколько ответов на один comment | Meta retry'ит при non-200. Наш handler всегда возвращает 200, так что не должно быть. Если есть — что-то падает в middleware FastAPI до handler'а — Railway logs |
| HTTP 401 на отправке reply | Page Access Token expired (бывает раз в 60 дней если short-lived). Перегенерируй и обнови в Railway |

---

## 9. Что под капотом (если интересно)

Один `/webhooks/instagram` endpoint обслуживает **и DM, и комменты**:

```
POST /webhooks/instagram
  body = raw bytes
  → verify_meta_signature(META_APP_SECRET, X-Hub-Signature-256, body)
  → payload = json.loads(body)
  → normalized = parse_instagram_webhook(payload)         # DM parser
       or       = parse_instagram_comment_webhook(payload) # comment parser
  → если normalized = None → return 200, ничего не делаем (не наш event)
  → MessageRequest строится с message_type = "dm" | "comment"
  → _handle_message(req, db):
       resolve_or_create_customer (email-anchor identity)
       get_or_create_conversation (per-channel-thread)
       append user message → pgvector RAG → LLM → append assistant
       if message_type == "dm":  handoff check (только если email captured)
       if message_type == "comment":  handoff skip (public ctx)
  → reply через правильный Send API:
       DM       → POST /me/messages (recipient = igsid/psid)
       comment  → POST /{comment_id}/replies (IG) или /comments (FB)
  → return 200 (всегда — Meta retries on non-200)
```

Файлы:
- `channels/instagram.py` — parsers + senders (DM + comment)
- `channels/messenger.py` — то же для FB
- `channels/utils.py` — `verify_meta_signature` (HMAC-SHA256) + `is_proactive_allowed` (24h-окно Meta)
- `main.py` — `/webhooks/instagram`, `/webhooks/messenger` routes, `_handle_message` pipeline
- `prompts.py` — секция `COMMENT MODE` + few-shots 23-27

В Bot tone:
- DM → разговорное «вы», полная sales-qualification, спрашивает email, на email-handoff отправляет brief оператору
- Comment → разговорное «вы», 1 предложение, **никогда** не спрашивает контакт, **всегда** редиректит в Direct, handoff никогда не срабатывает

---

## 10. Если что-то не так

1. **Логи бота** в Railway → service `deadline-sales-bot` → **Deployments** → текущий → **View Logs**. Ищи строки `[<conv-id-короткий>/<channel>/<dm|comment>] Q: ...` и `A: ...`.
2. **Логи доставки** в Meta UI → App Dashboard → Webhooks → нужный продукт → **Recent Deliveries**. Видны статус-коды наших ответов на каждый event.
3. **БД-инспекция**:
   ```bash
   railway run --service deadline-sales-bot python -c "from db.connection import session_scope; from sqlalchemy import text
   with session_scope() as db:
       for r in db.execute(text(\"SELECT channel, count(*) FROM conversations GROUP BY channel\")).fetchall():
           print(r)"
   ```
4. **Откатить** новый деплой если что-то совсем ломается: Railway → Deployments → **Rollback** на предыдущий.

---

## 11. Что ещё стоит знать

- **24-hour rule** Meta: бот может отвечать без message tag только в течение 24 часов после последнего сообщения юзера. Через 24h Send API на DM возвращает ошибку. Для проактивного re-engagement нужны message tags (`HUMAN_AGENT`, `CONFIRMED_EVENT_UPDATE` и т.д.). Phase 2.
- **Rate limits** на IG DM: ~200 сообщений в час на Page. На комменты — ниже. Если бот ловит много трафика — будут 4xx. Phase 2: ставим Redis-rate-limit.
- **Page Access Token** короткоживущий по умолчанию (1-2 часа). Сгенерированный через Messenger → Settings → Access Tokens — долгоживущий (~60 дней). После каждой смены пароля Page-владельца его надо перегенерировать. **TODO**: автоматизировать refresh через System User token.
- **Privacy**: всё что лид написал — у нас в БД (таблицы `messages`, `customers`). При GDPR-запросе на удаление — `DELETE FROM customers WHERE email = '...'` каскадом удалит conversation/messages/identities.

Удачи. Если уперлись — пингани в Telegram-чат, разберём вместе.
