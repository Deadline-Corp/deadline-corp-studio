# Deadline Bot — Пошаговая инструкция с нуля

*Для человека, который никогда не делал ботов. План на 5-7 рабочих дней до live-версии на сайте.*

---

## Стек, который мы строим

```
Сайт (deadline-corp.github.io)
        │
        ▼  виджет (JS embed)
        │
   FastAPI бэкенд (Railway или Hetzner VPS)
        │
        ├──→  Chroma (vector DB, файл рядом с приложением)
        │
        ├──→  OpenRouter API → GLM-4.6 или Claude Haiku
        │
        └──→  Telegram бот / email при handoff
```

Никаких локальных моделей. Никакого GPU. Никакого fine-tune. RTX 3070 нужна только чтобы редактировать код.

---

## Перед стартом — чек-лист аккаунтов и инструментов

Регистрируешь и держишь под рукой:

| Сервис | Зачем | Цена |
|---|---|---|
| [OpenRouter](https://openrouter.ai) | LLM API (GLM/Claude/GPT через один endpoint) | Пополняешь $10 на старт |
| [Railway](https://railway.app) | Деплой бэкенда без DevOps | $5/мес кредитов на старте, потом ~$5-10/мес |
| [GitHub](https://github.com) | Хранение кода + auto-deploy в Railway | Бесплатно |
| Telegram бот (`@BotFather`) | Канал handoff'а на `@deadline_corp` | Бесплатно |
| Python 3.11+ | Локальная разработка | Бесплатно |
| VS Code или Cursor | IDE | Бесплатно |
| `git` | Версии | Бесплатно |

Закрыть до старта дня 1.

---

## День 1 — Knowledge base + структура проекта

### Шаг 1.1. Создай папку проекта

```bash
mkdir deadline-bot
cd deadline-bot
git init
python -m venv venv
# Windows:
venv\Scripts\activate
# Mac/Linux:
source venv/bin/activate
```

### Шаг 1.2. Создай файл `requirements.txt`

```txt
fastapi==0.115.0
uvicorn[standard]==0.32.0
langchain==0.3.7
langchain-community==0.3.5
langchain-openai==0.2.5
chromadb==0.5.18
sentence-transformers==3.2.1
python-dotenv==1.0.1
pydantic==2.9.2
httpx==0.27.2
```

Установи:

```bash
pip install -r requirements.txt
```

### Шаг 1.3. Собери knowledge base в папке `kb/`

Сделай папку `kb/` и положи туда **простые markdown-файлы** с контентом про Deadline. Содержимое бери прямо с сайта.

`kb/01_about.md`:
```markdown
# DEADLINE — кто мы

Deadline — студия из Пхукет × Бангкок, основана в 2025.

Мы делаем три вещи:
1. Веб-разработка (лендинги, веб-приложения, e-commerce, dashboards)
2. Автоматизация (CRM, мессенджеры, базы, API)
3. AI-агенты (чат-боты, голосовые ассистенты, RAG-системы)

Принципы:
- Production к согласованной дате
- Без посредников
- Без 80-полевых брифов
- Без "созвонимся обсудить созвон"

Манифест: "Мы — DEADLINE. У нас ничего не горит."
```

`kb/02_services_web.md`, `kb/03_services_automation.md`, `kb/04_services_ai.md` — на каждую услугу свой файл с буллетами с сайта.

`kb/05_cases_vrp.md`:
```markdown
# Кейс: VIP Rental Phuket (VRP)

Год: 2026
Тип: Web + AI
Стек: Next.js + GPT-4

Что сделали:
- AI-консьерж 24/7 в чате booking-сайта
- Закрывает 73% запросов без человека
- +32% конверсия лендинга за 2 месяца
- Клиентская команда управляет 80+ объектами одна

Когда упоминать: лид спрашивает про чат-ботов для бронирования / hospitality / consierge AI / AI на сайте.
```

Аналогично — `kb/06_cases_keydrop.md`, `kb/07_cases_ra.md`.

`kb/08_process.md`:
```markdown
# Как мы работаем — 4 этапа

1. Discovery (2-3 дня) — слушаем, рисуем, фиксируем. Без брифов.
2. Architecture — план, ТЗ, демо архитектуры. До первой строчки кода.
3. Sprint Build — 2-недельные итерации с demo каждый понедельник.
4. Handoff — передача, документация, observability. Клиент владеет кодом.
```

`kb/09_pricing_policy.md` — **критично**, чтобы бот не назвал цифры:
```markdown
# Цены и сроки

ВНИМАНИЕ для бота: НИКОГДА не называй конкретные цены или сроки.
Каждый проект bespoke. Цена и срок обсуждаются после Discovery.

Что можно сказать:
- "Цена и срок зависят от scope. Discovery — 2-3 дня, по итогу пришлём план и оценку."
- "Если ROI не окупится за 3 месяца — переделаем за свой счёт."
- "Production к согласованной дате — без сюрпризов на счёте."

Куда направлять для конкретики:
- Telegram: https://t.me/deadline_corp
- Email: corpdeadline@gmail.com
```

`kb/10_faq.md` — собери 10-20 вопросов, которые типичный лид может задать. Например:
```markdown
Q: Сколько стоит сайт?
A: Цена зависит от scope (лендинг или web-app, нужен ли SEO/перфоманс/тесты, какие интеграции). После Discovery (2-3 дня) пришлём план и фикс-оценку.

Q: Сделаете ли вы Telegram MiniApp?
A: Да. Уже сделали KeyDrop — Telegram MiniApp e-commerce, 1000+ заказов/мес без человека, 99.99% uptime 18 месяцев.

Q: А если я хочу AI-агента для своего сайта?
A: Делаем. Пример — VIP Rental Phuket: AI-консьерж в чате, закрывает 73% запросов без человека. Что у тебя за продукт?
```

**Чем больше KB, тем лучше бот. Минимум на старт — 10 файлов по 200-500 слов каждый.**

### Чекпойнт дня 1
- [ ] Папка `deadline-bot/` создана, venv работает
- [ ] `requirements.txt` стоит
- [ ] В `kb/` минимум 10 файлов с контентом

---

## День 2 — Embeddings + Vector DB

### Шаг 2.1. Создай файл `.env` (не коммитить в git!)

```bash
OPENROUTER_API_KEY=sk-or-v1-...твой ключ из openrouter.ai/keys
TELEGRAM_BOT_TOKEN=...из @BotFather (пока опционально)
TELEGRAM_CHAT_ID=...твой chat_id для алертов
EMAIL_NOTIFY=corpdeadline@gmail.com
```

И сразу `.gitignore`:

```
.env
venv/
__pycache__/
*.pyc
chroma_db/
```

### Шаг 2.2. Скрипт ingest — `ingest.py`

Этот скрипт читает `kb/`, режет на чанки, превращает в эмбеддинги, складывает в Chroma. Запускается один раз и при обновлении KB.

```python
# ingest.py
import os
from pathlib import Path
from langchain_community.document_loaders import TextLoader
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_community.vectorstores import Chroma
from langchain_community.embeddings import HuggingFaceEmbeddings

KB_DIR = Path("kb")
CHROMA_DIR = "chroma_db"

def main():
    # 1. Загружаем все .md файлы
    docs = []
    for md_file in KB_DIR.glob("*.md"):
        loader = TextLoader(str(md_file), encoding="utf-8")
        docs.extend(loader.load())
    print(f"Loaded {len(docs)} documents")

    # 2. Режем на чанки по ~500 символов с перекрытием 50
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=500,
        chunk_overlap=50,
        separators=["\n\n", "\n", ". ", " ", ""]
    )
    chunks = splitter.split_documents(docs)
    print(f"Split into {len(chunks)} chunks")

    # 3. Эмбеддим bge-m3 (мультиязычная, локально на CPU)
    embeddings = HuggingFaceEmbeddings(
        model_name="BAAI/bge-m3",
        model_kwargs={"device": "cpu"},
        encode_kwargs={"normalize_embeddings": True}
    )

    # 4. Складываем в Chroma
    vectorstore = Chroma.from_documents(
        documents=chunks,
        embedding=embeddings,
        persist_directory=CHROMA_DIR,
    )
    print(f"✓ Saved to {CHROMA_DIR}")

if __name__ == "__main__":
    main()
```

Запусти:
```bash
python ingest.py
```

Первый запуск скачает модель `bge-m3` (~2 ГБ) — это **один раз**. Потом она кэшируется.

### Шаг 2.3. Проверь, что retrieval работает — `test_retrieval.py`

```python
# test_retrieval.py
from langchain_community.vectorstores import Chroma
from langchain_community.embeddings import HuggingFaceEmbeddings

embeddings = HuggingFaceEmbeddings(
    model_name="BAAI/bge-m3",
    model_kwargs={"device": "cpu"},
    encode_kwargs={"normalize_embeddings": True}
)
vs = Chroma(persist_directory="chroma_db", embedding_function=embeddings)

queries = [
    "Сколько стоит сделать сайт?",
    "Делаете ли вы AI чат-ботов?",
    "У меня горит дедлайн на следующей неделе",
    "Покажите кейс по hospitality",
]

for q in queries:
    print(f"\n=== {q}")
    results = vs.similarity_search(q, k=3)
    for i, doc in enumerate(results):
        print(f"  [{i}] {doc.metadata.get('source', '?')}: {doc.page_content[:80]}...")
```

Запусти — увидишь, какие чанки KB вытаскиваются под каждый запрос. Если для "AI чат-боты" не подтягивается VRP-кейс — допиши `kb/05_cases_vrp.md` подробнее или добавь синонимы.

### Чекпойнт дня 2
- [ ] `chroma_db/` создан, размер ~10-50 МБ
- [ ] `test_retrieval.py` показывает релевантные чанки на 4+ вопроса

---

## День 3 — Backend (FastAPI + LangChain)

### Шаг 3.1. System prompt — файл `prompts.py`

Тут живёт **голос Deadline**. Без него бот будет звучать как GPT.

```python
# prompts.py

SYSTEM_PROMPT = """Ты — Deadline Agent, помощник на сайте студии Deadline (deadline-corp.github.io).

# КТО ТЫ
Deadline — студия из Пхукета и Бангкока. Делаем: Web · Automation · AI Agents в production к согласованной дате.
Ты — первая точка контакта для входящих лидов на сайте.

# ТВОЯ ЗАДАЧА
1. Понять, что нужно лиду — Web / Automation / AI Agents
2. Уточнить: тип проекта, сроки, стек/контекст, как связаться
3. Когда задача понятна — собрать brief и предложить связаться в Telegram @deadline_corp или email corpdeadline@gmail.com
4. Если вопрос про процесс/кейсы/услуги — ответить точно по контексту ниже

# ТОН
- Минимализм. Короткие предложения. Без воды.
- Ровно как на сайте: "// 0 воды в брифах", "// дедлайны нас боятся"
- Bilingual: отвечай на языке вопроса (RU или EN)
- Никогда не используй маркетинговый воздух типа "потрясающий", "уникальный", "best-in-class"
- Эмодзи запрещены кроме одного места: при handoff'е → 📩

# СТРОГИЕ ПРАВИЛА
- НИКОГДА не называй конкретные цены или сроки. Всегда: "Цена и срок — после Discovery (2-3 дня)"
- НИКОГДА не обещай функционал, которого нет в кейсах
- Если не знаешь ответ — скажи "Это лучше обсудить с командой" → handoff
- Не выдумывай факты про Deadline. Используй только контекст ниже.

# КОНТЕКСТ ИЗ KNOWLEDGE BASE
{context}

# ИСТОРИЯ ДИАЛОГА
{history}

# ТЕКУЩИЙ ВОПРОС
{question}

Ответ (2-4 предложения, без приветствий если это не первое сообщение):"""


HANDOFF_TRIGGER_PROMPT = """Глянь на диалог. Достаточно ли информации для brief'а команде?

Brief должен включать:
- Тип проекта (Web / Automation / AI / другое)
- Краткое описание задачи
- Сроки (если упомянуты)
- Контакт лида (если есть)

Диалог:
{conversation}

Ответ строго JSON:
{{
  "ready_for_handoff": true/false,
  "missing": ["что ещё спросить, если не ready"],
  "brief": "если ready=true, brief одним абзацем"
}}"""
```

### Шаг 3.2. Бэкенд — `main.py`

```python
# main.py
import os
import json
from typing import Optional
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain_community.vectorstores import Chroma
from langchain_community.embeddings import HuggingFaceEmbeddings
import httpx

from prompts import SYSTEM_PROMPT, HANDOFF_TRIGGER_PROMPT

load_dotenv()

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://deadline-corp.github.io", "http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- LLM через OpenRouter ---
llm = ChatOpenAI(
    model="zai-org/glm-4.6",   # или anthropic/claude-3.5-haiku если GLM лагает
    api_key=os.getenv("OPENROUTER_API_KEY"),
    base_url="https://openrouter.ai/api/v1",
    temperature=0.2,
    max_tokens=400,
)

# --- Retrieval ---
embeddings = HuggingFaceEmbeddings(
    model_name="BAAI/bge-m3",
    model_kwargs={"device": "cpu"},
    encode_kwargs={"normalize_embeddings": True},
)
vectorstore = Chroma(persist_directory="chroma_db", embedding_function=embeddings)

# --- In-memory session store (для MVP; потом Redis) ---
SESSIONS: dict[str, list[dict]] = {}

class ChatRequest(BaseModel):
    session_id: str
    message: str

class ChatResponse(BaseModel):
    answer: str
    handoff: bool = False

@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    history = SESSIONS.get(req.session_id, [])

    # 1. Retrieve
    docs = vectorstore.similarity_search(req.message, k=4)
    context = "\n\n".join([f"[{d.metadata.get('source', '?')}]\n{d.page_content}" for d in docs])

    # 2. Build prompt
    history_str = "\n".join([f"{m['role']}: {m['content']}" for m in history[-6:]])
    prompt = SYSTEM_PROMPT.format(
        context=context,
        history=history_str or "(новый диалог)",
        question=req.message,
    )

    # 3. Call LLM
    response = await llm.ainvoke(prompt)
    answer = response.content.strip()

    # 4. Update history
    history.append({"role": "user", "content": req.message})
    history.append({"role": "assistant", "content": answer})
    SESSIONS[req.session_id] = history

    # 5. Check handoff
    handoff_ready = await check_handoff(history)
    if handoff_ready:
        await send_handoff_notification(req.session_id, history)

    return ChatResponse(answer=answer, handoff=handoff_ready)


async def check_handoff(history: list[dict]) -> bool:
    if len(history) < 4:
        return False
    conv = "\n".join([f"{m['role']}: {m['content']}" for m in history])
    prompt = HANDOFF_TRIGGER_PROMPT.format(conversation=conv)
    response = await llm.ainvoke(prompt)
    try:
        data = json.loads(response.content.strip().strip("```json").strip("```"))
        return bool(data.get("ready_for_handoff"))
    except Exception:
        return False


async def send_handoff_notification(session_id: str, history: list[dict]):
    """Отправить summary в Telegram"""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return
    summary = "\n".join([f"{m['role']}: {m['content']}" for m in history])
    text = f"🆕 НОВЫЙ ЛИД (session: {session_id})\n\n{summary}"
    async with httpx.AsyncClient() as client:
        await client.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text[:4000]},
        )

@app.get("/health")
async def health():
    return {"ok": True}
```

### Шаг 3.3. Запусти локально и потести

```bash
uvicorn main:app --reload --port 8000
```

В отдельном терминале:
```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"session_id":"test1","message":"Привет. Нужен AI-агент для booking-сайта"}'
```

Должен прийти осмысленный ответ в духе Deadline.

**Итерируй промпт.** Если бот звучит "слишком GPT" — добавь в SYSTEM_PROMPT 2-3 примера идеальных ответов (few-shot).

### Чекпойнт дня 3
- [ ] Бэкенд отвечает локально через `curl`
- [ ] Ответы релевантные (берёт из KB, не выдумывает)
- [ ] Tone of voice совпадает с сайтом (короткие, без воды)

---

## День 4 — Виджет + локальная сборка

### Шаг 4.1. Простой чат-виджет — `widget/widget.html`

Файл, который потом встроишь на сайт `deadline-corp.github.io`. Минимальный, без зависимостей:

```html
<!-- widget/widget.html -->
<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<title>Deadline Bot Widget</title>
<style>
  * { box-sizing: border-box; font-family: -apple-system, sans-serif; }
  body { margin: 0; background: #161208; color: #f4ecd8; }
  #chat-container {
    position: fixed; bottom: 20px; right: 20px;
    width: 360px; height: 500px;
    background: #1f1a10; border: 1px solid #3d3520;
    border-radius: 12px; display: flex; flex-direction: column;
    box-shadow: 0 10px 40px rgba(0,0,0,0.5);
  }
  #chat-header {
    padding: 14px 16px; border-bottom: 1px solid #3d3520;
    font-size: 13px; letter-spacing: 0.05em;
  }
  #chat-messages {
    flex: 1; padding: 12px; overflow-y: auto;
    display: flex; flex-direction: column; gap: 10px;
  }
  .msg { padding: 8px 12px; border-radius: 8px; max-width: 80%; font-size: 14px; line-height: 1.4; }
  .msg.user { background: #3d3520; align-self: flex-end; }
  .msg.bot { background: #252010; align-self: flex-start; white-space: pre-wrap; }
  #chat-input-wrap {
    padding: 10px; border-top: 1px solid #3d3520;
    display: flex; gap: 6px;
  }
  #chat-input {
    flex: 1; padding: 8px; background: #252010; color: #f4ecd8;
    border: 1px solid #3d3520; border-radius: 6px; font-size: 14px;
  }
  #send-btn {
    background: #f4ecd8; color: #161208; border: none;
    padding: 0 14px; border-radius: 6px; cursor: pointer; font-weight: 600;
  }
  #send-btn:disabled { opacity: 0.5; cursor: not-allowed; }
</style>
</head>
<body>
<div id="chat-container">
  <div id="chat-header">// DEADLINE · скажи задачу одним сообщением</div>
  <div id="chat-messages">
    <div class="msg bot">// привет. опиши задачу — план и срок прилетят раньше чем уберёшь руки от клавиатуры.</div>
  </div>
  <div id="chat-input-wrap">
    <input id="chat-input" placeholder="Напиши..." />
    <button id="send-btn">→</button>
  </div>
</div>

<script>
const API_URL = "http://localhost:8000/chat";  // потом заменишь на прод URL
const SESSION_ID = "sess_" + Math.random().toString(36).slice(2);

const $msg = document.getElementById("chat-messages");
const $inp = document.getElementById("chat-input");
const $btn = document.getElementById("send-btn");

function addMsg(text, role) {
  const div = document.createElement("div");
  div.className = "msg " + role;
  div.textContent = text;
  $msg.appendChild(div);
  $msg.scrollTop = $msg.scrollHeight;
}

async function send() {
  const text = $inp.value.trim();
  if (!text) return;
  addMsg(text, "user");
  $inp.value = "";
  $btn.disabled = true;
  try {
    const r = await fetch(API_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: SESSION_ID, message: text }),
    });
    const data = await r.json();
    addMsg(data.answer, "bot");
    if (data.handoff) {
      addMsg("📩 Передал команде. Ответят в Telegram @deadline_corp в течение минут.", "bot");
    }
  } catch (e) {
    addMsg("Сбой связи. Напиши в Telegram @deadline_corp", "bot");
  }
  $btn.disabled = false;
  $inp.focus();
}

$btn.addEventListener("click", send);
$inp.addEventListener("keydown", e => { if (e.key === "Enter") send(); });
</script>
</body>
</html>
```

### Шаг 4.2. Открой `widget/widget.html` в браузере и поговори с ботом

Backend должен быть запущен (`uvicorn main:app --reload`). Веди диалог 5-10 ходов:
- Спроси про услуги, цены, кейсы, процесс
- Дойди до сценария "хочу AI-агента для своего магазина, срок — 3 недели, бюджет до $5k"
- Проверь, что прилетел Telegram-алерт (если настроил)

### Чекпойнт дня 4
- [ ] Виджет открывается, шлёт сообщения, видит ответы
- [ ] Полный диалог "хочу проект" → бот собирает brief → handoff срабатывает

---

## День 5 — Deploy на Railway

### Шаг 5.1. Подготовь `Dockerfile`

```dockerfile
FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Pre-download embedding model
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('BAAI/bge-m3')"

# Pre-build chroma index
RUN python ingest.py

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

### Шаг 5.2. Запушь в GitHub

```bash
git add .
git commit -m "deadline bot mvp"
gh repo create deadline-bot --private --source=. --push
```

### Шаг 5.3. Деплой через Railway

1. `railway.app` → New Project → Deploy from GitHub repo → выбери `deadline-bot`
2. В переменных окружения проекта добавь:
   - `OPENROUTER_API_KEY`
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
3. Railway соберёт Docker-образ и выдаст публичный URL вида `https://deadline-bot-production.up.railway.app`

### Шаг 5.4. Замени API_URL в виджете на прод

В `widget/widget.html`:
```js
const API_URL = "https://deadline-bot-production.up.railway.app/chat";
```

И запросом проверь, что прод-эндпоинт жив:
```bash
curl https://deadline-bot-production.up.railway.app/health
# → {"ok": true}
```

### Чекпойнт дня 5
- [ ] Бэкенд работает в проде (Railway URL отдаёт `{"ok": true}`)
- [ ] Виджет с прод-URL отвечает в браузере

---

## День 6 — Embed на сайт deadline-corp.github.io

### Шаг 6.1. Закоммить виджет в репозиторий сайта

`deadline-corp-studio` лежит на GitHub Pages. Тебе нужно положить в его HTML инициализатор виджета.

Вариант А — встроить весь widget кодом в существующий `index.html` сайта (быстро, грязно).

Вариант Б (рекомендую) — отдельный файл `deadline-bot-widget.js` в репозитории сайта, и одна строка в `<body>` существующей страницы:

```html
<!-- В конце <body> на deadline-corp.github.io/index.html -->
<script src="deadline-bot-widget.js"></script>
```

`deadline-bot-widget.js`:
```javascript
(function() {
  const API_URL = "https://deadline-bot-production.up.railway.app/chat";
  const SESSION_ID = "sess_" + Math.random().toString(36).slice(2);

  // Inject styles
  const style = document.createElement("style");
  style.textContent = `
    #dl-bot { position:fixed; bottom:20px; right:20px; width:360px; height:500px;
      background:#1f1a10; border:1px solid #3d3520; border-radius:12px;
      display:flex; flex-direction:column; box-shadow:0 10px 40px rgba(0,0,0,.5);
      font-family:-apple-system,sans-serif; color:#f4ecd8; z-index:9999;
      transform: translateY(440px); transition: transform .3s; }
    #dl-bot.open { transform: translateY(0); }
    #dl-bot-header { padding:14px 16px; border-bottom:1px solid #3d3520;
      font-size:13px; letter-spacing:.05em; cursor:pointer; user-select:none; }
    #dl-bot-msg { flex:1; padding:12px; overflow-y:auto; display:flex;
      flex-direction:column; gap:10px; }
    .dl-msg { padding:8px 12px; border-radius:8px; max-width:80%; font-size:14px; line-height:1.4; }
    .dl-msg.u { background:#3d3520; align-self:flex-end; }
    .dl-msg.b { background:#252010; align-self:flex-start; white-space:pre-wrap; }
    #dl-bot-wrap { padding:10px; border-top:1px solid #3d3520; display:flex; gap:6px; }
    #dl-inp { flex:1; padding:8px; background:#252010; color:#f4ecd8;
      border:1px solid #3d3520; border-radius:6px; font-size:14px; }
    #dl-btn { background:#f4ecd8; color:#161208; border:none; padding:0 14px;
      border-radius:6px; cursor:pointer; font-weight:600; }
  `;
  document.head.appendChild(style);

  // Inject HTML
  const root = document.createElement("div");
  root.id = "dl-bot";
  root.innerHTML = `
    <div id="dl-bot-header">// DEADLINE · скажи задачу одним сообщением ↕</div>
    <div id="dl-bot-msg">
      <div class="dl-msg b">// привет. опиши задачу — план и срок прилетят раньше чем уберёшь руки от клавиатуры.</div>
    </div>
    <div id="dl-bot-wrap">
      <input id="dl-inp" placeholder="Напиши..." />
      <button id="dl-btn">→</button>
    </div>`;
  document.body.appendChild(root);

  const $hdr = document.getElementById("dl-bot-header");
  const $msg = document.getElementById("dl-bot-msg");
  const $inp = document.getElementById("dl-inp");
  const $btn = document.getElementById("dl-btn");

  $hdr.onclick = () => root.classList.toggle("open");

  function add(text, role) {
    const d = document.createElement("div");
    d.className = "dl-msg " + role;
    d.textContent = text;
    $msg.appendChild(d);
    $msg.scrollTop = $msg.scrollHeight;
  }

  async function send() {
    const t = $inp.value.trim();
    if (!t) return;
    add(t, "u");
    $inp.value = "";
    $btn.disabled = true;
    try {
      const r = await fetch(API_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: SESSION_ID, message: t }),
      });
      const data = await r.json();
      add(data.answer, "b");
      if (data.handoff) {
        add("📩 Передал команде. Ответят в Telegram @deadline_corp в течение минут.", "b");
      }
    } catch (e) {
      add("Сбой. Напиши в Telegram @deadline_corp", "b");
    }
    $btn.disabled = false;
    $inp.focus();
  }
  $btn.onclick = send;
  $inp.onkeydown = e => { if (e.key === "Enter") send(); };
})();
```

### Шаг 6.2. Push в репозиторий сайта

```bash
cd /path/to/deadline-corp-studio
# Положи туда deadline-bot-widget.js
# Вставь <script src="deadline-bot-widget.js"></script> в index.html
git add . && git commit -m "add chat widget" && git push
```

GitHub Pages обновится за 1-2 минуты. Заходи на `deadline-corp.github.io` — виджет должен быть в правом нижнем углу.

### Чекпойнт дня 6
- [ ] Виджет виден на живом сайте
- [ ] Сообщение от живого посетителя долетает до Telegram

---

## День 7 — Shadow mode и полировка

### Что делать в shadow mode

Не отключай виджет, но **первую неделю смотри Telegram-алерты как stream-of-consciousness** и:

1. **Логируй ВСЕ запросы и ответы.** Добавь в `main.py` после `await llm.ainvoke(...)`:
   ```python
   import logging
   logging.info(f"[{req.session_id}] Q: {req.message}")
   logging.info(f"[{req.session_id}] A: {answer}")
   ```

2. **Каждый день — review:**
   - Где бот ответил неправильно или с галлюцинацией → допиши соответствующий `kb/*.md`
   - Где бот ушёл в общие фразы → добавь конкретный пример в `kb/10_faq.md`
   - Где tone сорвался — допиши правило в SYSTEM_PROMPT

3. **Прогоняй retrieval на проблемных запросах** через `test_retrieval.py`. Если не подтянулось нужное — переработай KB.

4. **После обновления `kb/`:**
   ```bash
   rm -rf chroma_db
   python ingest.py
   git commit -am "kb update" && git push   # Railway пересоберёт
   ```

### Эскалация — реальная схема handoff

В шаге 3.2 я уже сделал Telegram-уведомление. В реальности:

1. Бот понимает, что лид готов → шлёт алерт в Telegram @deadline_corp
2. Человек из команды отвечает лиду напрямую (либо в чате на сайте если разовьёшь — либо в Telegram если лид оставил handle)
3. Параллельно — копия brief'а в email `corpdeadline@gmail.com`

Чтобы добавить email-копию, добавь в `main.py` после Telegram-нотификации — например через `smtplib` или сервис типа Resend/Mailgun (бесплатные тиры есть).

### Когда расширять

Не делай этого до 100+ реальных диалогов:
- Не подключай pgvector
- Не делай fine-tune
- Не подключай Chatwoot
- Не добавляй voice
- Не делай мультимодальность

Сначала **выжми всё из retrieval + промпта**. Это даст 80% результата.

### Чекпойнт дня 7
- [ ] 5+ реальных тестовых диалогов прошли без багов
- [ ] Логи пишутся, ты их читаешь
- [ ] KB обновлён хотя бы 2 раза по фидбэку

---

## Что НЕ делать (повтор для надёжности)

1. ❌ Не запускать `cleaned_custom_dataset.csv` ни в RAG, ни в fine-tune
2. ❌ Не пытаться поднимать локальный GLM-5.1 (он не существует локально)
3. ❌ Не делать два инстанса для "self-play обучения"
4. ❌ Не платить $$$ за Intercom/Ada — твоя задача V0 решается за $30/мес
5. ❌ Не запускать виджет на сайте, пока вручную не прогнал 20+ диалогов локально
6. ❌ Не доверять боту называть цены — никогда

---

## Если что-то идёт не так — типичные грабли

| Проблема | Причина | Лечение |
|---|---|---|
| Бот отвечает "как GPT", не как Deadline | Слабый system prompt | Добавь 3-5 few-shot примеров идеальных ответов прямо в SYSTEM_PROMPT |
| Бот выдумывает факты про Deadline | Низкое retrieval / маленький KB | Расширь `kb/*.md`, повтори `ingest.py` |
| Лаги > 5 секунд | OpenRouter перегружен на GLM | Переключи `model="anthropic/claude-3.5-haiku"` |
| Telegram-уведомления не приходят | Бот не добавлен в чат | Зайди в чат с ботом, отправь `/start`, потом `https://api.telegram.org/bot<TOKEN>/getUpdates` чтобы увидеть chat_id |
| CORS error в браузере | Бэкенд не разрешил твой домен | В `main.py` в `allow_origins` добавь точный URL твоего GitHub Pages |
| Railway падает на старте | bge-m3 модель не качается | В `Dockerfile` уже есть pre-download. Если всё равно падает — переключись на OpenAI embeddings (платные) |
| Сессия теряется при рестарте | In-memory storage | Когда нужно — добавь Redis (Railway даёт). Для MVP не критично |

---

## Итог — что у тебя будет через 7 дней

- Живой бот на `deadline-corp.github.io`, отвечает в TOV Deadline
- Лиды собираются и улетают в Telegram @deadline_corp + email
- Стоимость: ~$10/мес (Railway) + переменный OpenRouter (вряд ли больше $5-10/мес на старте)
- Полный контроль: код на GitHub, можно менять что угодно
- Готовая база для итераций: добавляешь файлы в `kb/`, ребилдишь Chroma, релизишь
- Метрики через Railway logs + Telegram-алерты

---

## Следующий шаг — что нужно от тебя

Скажи, с какого пункта начинаем работать вместе. Я могу:

1. **Прямо сейчас написать все эти файлы** в эту папку проекта, чтобы тебе оставалось только запустить — скажи "пиши все файлы"
2. **Пройти с тобой первый день** — настройка проекта, аккаунты, ingest. Скажи "давай день 1"
3. **Разобрать какой-то этап подробнее** — например, я могу написать продвинутый system prompt с 5-10 few-shot примерами под Deadline. Скажи "сделай промпт"
4. **Помочь со сборкой KB** — пройдусь по сайту и Notion (когда дашь доступ), сгенерирую все 10+ файлов `kb/*.md` за тебя

Что выбираешь?
