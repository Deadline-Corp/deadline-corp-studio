# Deadline Sales Bot

Lead-qualification chat-bot для сайта `deadlinecorp.com` (и временно `deadline-corp.github.io`).

**Стек:** Python 3.11 + FastAPI + LangChain + Chroma + OpenRouter (GLM-4.6 / Claude Haiku) + ванильный JS-виджет.
**Деплой:** Docker → Railway.
**Стоимость:** ~$10-15/мес на старте (Railway hobby + OpenRouter per-token).

---

## Структура проекта

```
.
├── kb/                    # Knowledge base (markdown), редактируешь руками
│   ├── 01_about.md
│   ├── 02_services_web.md
│   ├── ...
│   └── 12_handoff.md
├── widget/                # Фронт-виджет для встройки на сайт
│   ├── widget.js          # Сам виджет (одним файлом)
│   └── index.html         # Тестовая страница для локальной разработки
├── prompts.py             # System prompt + few-shot примеры + handoff classifier
├── ingest.py              # KB → Chroma vector DB
├── main.py                # FastAPI backend (/chat, /health, /sessions)
├── requirements.txt
├── Dockerfile
├── .env.example           # Шаблон секретов (скопируй в .env)
├── .gitignore
└── README.md
```

---

## Локальный запуск (первый раз)

### 1. Установка

```bash
cd "Deadline sales bot"
python -m venv venv

# Windows
venv\Scripts\activate

# Mac / Linux
source venv/bin/activate

pip install -r requirements.txt
```

### 2. Секреты

```bash
cp .env.example .env
```

Открой `.env`, заполни:
- `OPENROUTER_API_KEY` — возьми на https://openrouter.ai/keys, пополни баланс на $5-10
- `TELEGRAM_BOT_TOKEN` и `TELEGRAM_CHAT_ID` — опционально, для уведомлений о лидах (см. ниже как получить)

### 3. Построй векторную БД из KB

```bash
python ingest.py
```

Первый запуск скачает модель `bge-m3` (~2 ГБ) — это **разовое**. Дальше скрипт работает быстро.

В конце увидишь smoke-test: проверь что под каждый тестовый запрос подтягиваются релевантные чанки. Если нет — допиши `kb/*.md`.

### 4. Запусти бэкенд

```bash
uvicorn main:app --reload --port 8000
```

Проверка:
```bash
curl http://localhost:8000/health
# → {"ok":true, "vectorstore_loaded":true, ...}

curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"session_id":"test1","message":"Привет. Нужен AI-агент для booking-сайта"}'
```

### 5. Открой тестовую страницу с виджетом

Открой `widget/index.html` в браузере (через двойной клик или через `python -m http.server 5500`).

Виджет в правом нижнем углу. Кликни на полоску — раскрылся. Веди диалог.

---

## Как получить Telegram bot token и chat_id

1. Открой в Telegram `@BotFather` → `/newbot` → выбери имя и username. Получишь `TELEGRAM_BOT_TOKEN`.
2. Создай группу `Deadline Leads` (или используй личку), добавь туда твоего бота.
3. Напиши в эту группу что-нибудь.
4. Открой в браузере:
   ```
   https://api.telegram.org/bot<TOKEN>/getUpdates
   ```
5. Найди в ответе `"chat":{"id":-100...}` — это твой `TELEGRAM_CHAT_ID` (для групп — отрицательное число).
6. Положи в `.env` и перезапусти бэкенд.

---

## Деплой на Railway

### 1. Запушить в GitHub

```bash
git init
git add .
git commit -m "deadline sales bot mvp"
git branch -M main
gh repo create deadline-sales-bot --private --source=. --push
```

(если нет `gh` CLI — создай repo через сайт и `git remote add origin ...` руками)

### 2. Создать проект в Railway

1. https://railway.app → **New Project** → **Deploy from GitHub repo** → выбери `deadline-sales-bot`
2. Railway автоматически найдёт `Dockerfile` и начнёт сборку. Первый билд ~10-15 минут (скачивание `bge-m3` и инжест).
3. После сборки в **Settings** → **Networking** → **Generate Domain** — получишь URL вида `https://deadline-sales-bot-production.up.railway.app`

### 3. Добавить переменные окружения

В разделе **Variables** добавь:

| Key | Value |
|---|---|
| `OPENROUTER_API_KEY` | твой ключ |
| `TELEGRAM_BOT_TOKEN` | токен бота |
| `TELEGRAM_CHAT_ID` | id чата |
| `LLM_MODEL` | `z-ai/glm-4.6` |
| `LLM_FALLBACK_MODEL` | `anthropic/claude-3.5-haiku` |
| `ALLOWED_ORIGINS` | `https://deadlinecorp.com,https://www.deadlinecorp.com,https://deadline-corp.github.io` |

Railway автоматически передеплоит.

### 4. Проверить прод

```bash
curl https://your-railway-url/health
```

### 5. Прописать API URL в виджете и встроить на сайт

В `widget/widget.js` найди константу `API_URL` и замени на свой Railway URL.

Скопируй `widget.js` в репозиторий сайта `deadline-corp-studio` и в `index.html` добавь:

```html
<script src="widget.js" defer></script>
```

`git push` → GitHub Pages обновится за минуту → виджет на сайте.

---

## Workflow обновлений

**Поменял контент в `kb/`:**
```bash
python ingest.py    # ребилд Chroma локально (для теста)
git add kb/ && git commit -m "kb update" && git push
# Railway автоматически пересоберёт образ (в Dockerfile есть `RUN python ingest.py`)
```

**Поменял system prompt в `prompts.py`:**
```bash
git add prompts.py && git commit -m "prompt tweak" && git push
# Railway пересоберёт за 2-3 минуты
```

**Поменял виджет:**
```bash
# В репозитории сайта (не в боте)
git add widget.js index.html && git commit -m "widget tweak" && git push
# GitHub Pages обновится за 1-2 минуты
```

---

## Дебаг и мониторинг

**Логи в Railway:** Project → Deployments → View Logs. Все запросы и ответы пишутся туда.

**Локальный дебаг — посмотреть активные сессии:**
```bash
curl http://localhost:8000/sessions
```

**Сбросить сессию (полезно при тесте):**
```bash
curl -X DELETE http://localhost:8000/sessions/test1
```

**Проверить retrieval отдельно:** написать скрипт `test_retrieval.py` (см. `deadline-bot-stepbystep.md`).

---

## Типичные грабли

| Симптом | Причина | Лечение |
|---|---|---|
| `ModuleNotFoundError: langchain_chroma` | Не установлены deps | `pip install -r requirements.txt` |
| `Chroma DB not found` при /chat | Не запустил ingest | `python ingest.py` |
| LLM ответ «нет ключа» | `.env` не подхватился | Проверь что `.env` в корне проекта, не в подпапке |
| CORS error в браузере | Домен сайта не в `ALLOWED_ORIGINS` | Добавь домен в `.env` или Railway Variables |
| Telegram-алерты не приходят | Бот не добавлен в чат / неправильный chat_id | Проверь `/getUpdates`, добавь бота в группу |
| Railway build падает на `python ingest.py` | OOM на скачивании bge-m3 | Перейди на платный план Railway или замени на OpenAI embeddings (см. ниже) |
| Бот лагает > 5 сек | OpenRouter перегружен на текущей модели | Меняй `LLM_MODEL` на `anthropic/claude-3.5-haiku` |
| Бот «съезжает» с tone of voice | Few-shots слабые для случая | Добавь новый пример в `FEW_SHOT_EXAMPLES` в `prompts.py` |

---

## Что НЕ делать

- ❌ Не коммитить `.env` — там секреты
- ❌ Не загружать в `kb/` или Chroma 7-гигабайтный sales-датасет (это ломает доменную релевантность)
- ❌ Не делать fine-tuning, пока нет 500+ реальных диалогов с разметкой
- ❌ Не убирать `09_pricing_policy.md` из kb/ — без него бот будет называть выдуманные цены
- ❌ Не разрешать боту обещать сроки в неделях/днях для конкретного проекта лида

---

## Стоимость в проде

LLM-провайдер — **Ollama Cloud** (`gemma4:31b` primary, `gemma3:27b` fallback). Embeddings — **bge-m3 локально** (внутри Docker-образа на Railway, не тратит токены).

| Сценарий | Railway | Ollama Cloud (LLM) | Embeddings | **Итого/мес** |
|---|---|---|---|---|
| 100 диалогов/мес | $5 hobby | ~$1-2 | $0 (bge-m3) | **~$6-7** |
| 500 диалогов/мес | $5-10 hobby | ~$3-8 | $0 (bge-m3) | **~$8-18** |
| 2000 диалогов/мес | $10-15 | ~$15-30 | $0 (bge-m3) | **~$25-45** |
| 5000+/мес | $15-25 | ~$40-80 | $0 (bge-m3) | **~$55-105** |

Для сравнения: Intercom Fin = $0.99 за resolved conversation. На 1000 диалогах = $990/мес.

---

## Когда пересматривать стек

Триггеры — измеримые pain points, а НЕ просто рост трафика. Объём диалогов сам по себе не повод что-то менять — текущий стек масштабируется до ~10k/мес без архитектурных правок.

### LLM — `gemma4:31b` (Ollama Cloud)
Пересмотреть при **5000+ диалогов/мес**, если:
- Месячный счёт Ollama Cloud стал больше, чем стоимость self-hosted GPU → перенести LLM на свой Ollama-сервер (RTX 4090 на RunPod ≈ $300-500/мес flat, окупится при ~5000-7000 диалогов)
- Качество ответов на сложных кейсах перестало устраивать → попробовать `glm-4.7`, `kimi-k2.6` или `qwen3.5:397b-cloud` (последние две — reasoning, нужно `max_tokens >= 4000`, см. [decisions в MemPalace](#))
- Латентность gemma4 >3-5 сек → переключить primary/fallback местами (gemma3:27b меньше и быстрее)

### Embeddings — `BAAI/bge-m3` (локально)
**НЕ менять «при росте трафика».** Embeddings вызываются 1 раз на сообщение лида (RAG retrieval) — даже при 50k сообщений/мес это копейки. Пересмотреть **только если**:
- Cold-start на Railway: первый запрос после спина контейнера занимает >5 сек → перенести embeddings в облако (`text-embedding-3-small` от OpenAI, $0.02/1M токенов), модель грузится мгновенно из API, образ Railway падает с ~2.5 GB до ~200 MB
- Docker image >4 GB → bge-m3 ест ~2 GB, при росте других зависимостей это первое что выкидывается
- KB вырос до 200+ файлов / 3000+ chunks → CPU-эмбеддинг при ingest начнёт занимать >10 мин, тогда batch-embed через API
- Replicate retrieval провалов на русских/смешанных запросах → попробовать `voyage-3-multilingual` или Cohere `embed-multilingual-v3`

Свап embeddings — 4 строки в `ingest.py` + `main.py` (`HuggingFaceEmbeddings` → `OpenAIEmbeddings`), `python ingest.py` для перестройки Chroma. **Важно**: вектора bge-m3 (1024-dim) несовместимы с другими моделями — старая Chroma выбрасывается, индекс строится заново.

### Когда НЕ менять стек
- 200 диалогов/мес и хочется «улучшить» — нет. Текущая конфигурация спроектирована именно для этого объёма.
- Reasoning-модели «выглядят круче» — `qwen3.5:397b-cloud`, `glm-4.6:cloud`, `gpt-oss:120b-cloud` тратят весь `max_tokens` на скрытый `reasoning`, контент возвращается пустым (проверено на 1500 токенах). Включать только с `max_tokens >= 4000`, что втрое дороже за каждый ответ.

---

## Roadmap

После того как MVP работает в проде и накопил реальные диалоги:

1. **Месяц 1:** ежедневный review логов, добавление в `kb/` недостающих ответов, расширение `FEW_SHOT_EXAMPLES`
2. **Месяц 2-3:** добавить Redis для persistent sessions, accept-формат файлов в чате (если лиды кидают ТЗ)
3. **Месяц 3-6:** при 500+ реальных диалогах — RAGAS-evaluation set, A/B-тесты моделей
4. **Месяц 6+:** если RAG в чём-то стабильно проваливается — рассмотреть QLoRA на cloud (RunPod ~$3-5 за run) на реальных данных

Никакого fine-tune на синтетике. Никаких "self-play двух инстансов". Только производственный pipeline.

---

## License

Internal — Deadline. Не для публикации.

---

## Контакты

- Telegram: https://t.me/deadline_corp
- Email: corpdeadline@gmail.com
