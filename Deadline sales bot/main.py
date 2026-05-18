"""
Deadline Sales Bot — FastAPI backend.

Endpoints:
    POST /chat       — main chat endpoint used by the website widget
    GET  /health     — liveness probe (Railway uses this)
    GET  /sessions   — quick debug view of active sessions (dev only)

Run locally:
    uvicorn main:app --reload --port 8000

Deploy to Railway: see README.md
"""

import os
import json
import logging
import uuid
from pathlib import Path
from typing import Optional

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings

from langchain_chroma import Chroma
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_openai import ChatOpenAI

from prompts import build_chat_prompt, HANDOFF_CHECK_PROMPT, format_handoff_brief


# ============================================================================
# CONFIG
# ============================================================================

load_dotenv()

class Settings(BaseSettings):
    ollama_api_key: str
    ollama_base_url: str = "https://ollama.com/v1"
    llm_model: str = "qwen3.5:397b-cloud"
    llm_fallback_model: str = "glm-4.6:cloud"
    telegram_bot_token: Optional[str] = None
    telegram_chat_id: Optional[str] = None
    email_notify: Optional[str] = None
    allowed_origins: str = "https://deadlinecorp.com,https://www.deadlinecorp.com,https://deadline-corp.github.io,http://localhost:3000,http://localhost:5500,http://127.0.0.1:5500"
    log_level: str = "INFO"

    class Config:
        env_file = ".env"
        extra = "ignore"

settings = Settings()

logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("deadline-bot")

ROOT = Path(__file__).parent
CHROMA_DIR = ROOT / "chroma_db"
EMBEDDING_MODEL = "BAAI/bge-m3"


# ============================================================================
# APP + MIDDLEWARE
# ============================================================================

app = FastAPI(title="Deadline Sales Bot", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.allowed_origins.split(",") if o.strip()],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


# ============================================================================
# LLM CLIENTS (OpenRouter — OpenAI-compatible API)
# ============================================================================

def make_llm(model_name: str, *, temperature: float = 0.2, max_tokens: int = 1200) -> ChatOpenAI:
    # Ollama Cloud — OpenAI-compatible endpoint at /v1/chat/completions, Bearer auth.
    # max_tokens bumped to 1200: reasoning-capable cloud models (qwen3.5, glm-4.6, gpt-oss)
    # split the budget between hidden `reasoning` and visible `content` — too little and
    # content comes back empty.
    return ChatOpenAI(
        model=model_name,
        api_key=settings.ollama_api_key,
        base_url=settings.ollama_base_url,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=60,
        default_headers={
            "X-Title": "Deadline Sales Bot",
        },
    )

primary_llm = make_llm(settings.llm_model)
fallback_llm = make_llm(settings.llm_fallback_model)
# Handoff-классификатору тоже нужно ≥1000 токенов: reasoning сожрёт большую часть бюджета,
# на JSON-ответ должно остаться. temperature=0 для детерминированного классификатора.
handoff_llm = make_llm(settings.llm_fallback_model, temperature=0.0, max_tokens=1000)


# ============================================================================
# VECTOR STORE
# ============================================================================

if not CHROMA_DIR.exists():
    log.error(f"Chroma DB not found at {CHROMA_DIR}. Run `python ingest.py` first.")
    # Не падаем — даём приложению запуститься, /health работает, /chat вернёт 503

embeddings = HuggingFaceEmbeddings(
    model_name=EMBEDDING_MODEL,
    model_kwargs={"device": "cpu"},
    encode_kwargs={"normalize_embeddings": True},
)
vectorstore: Optional[Chroma] = None
if CHROMA_DIR.exists():
    vectorstore = Chroma(persist_directory=str(CHROMA_DIR), embedding_function=embeddings)
    log.info(f"Loaded Chroma DB from {CHROMA_DIR}")


# ============================================================================
# SESSION STORE (in-memory; OK for MVP, заменишь на Redis позже)
# ============================================================================

# session_id -> {"history": [...], "handoff_done": bool}
SESSIONS: dict[str, dict] = {}

def get_session(session_id: str) -> dict:
    return SESSIONS.setdefault(session_id, {"history": [], "handoff_done": False})


# ============================================================================
# MODELS
# ============================================================================

class ChatRequest(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=128)
    message: str = Field(..., min_length=1, max_length=4000)

class ChatResponse(BaseModel):
    answer: str
    handoff: bool = False
    session_id: str


# ============================================================================
# LLM CALL with fallback on failure
# ============================================================================

async def call_llm(prompt: str, *, max_tokens: int = 400) -> str:
    """Try primary model; fall back to secondary on error or timeout."""
    try:
        response = await primary_llm.ainvoke(prompt)
        return response.content.strip()
    except Exception as e:
        log.warning(f"Primary LLM ({settings.llm_model}) failed: {e}. Falling back.")
        try:
            response = await fallback_llm.ainvoke(prompt)
            return response.content.strip()
        except Exception as e2:
            log.error(f"Fallback LLM also failed: {e2}")
            raise HTTPException(503, "LLM provider unavailable")


# ============================================================================
# HANDOFF DETECTION
# ============================================================================

async def check_handoff(history: list[dict]) -> Optional[dict]:
    """
    Ask the classifier whether the conversation is ready for handoff.
    Returns None if not ready; returns parsed dict (project_type, task_summary, ...) if ready.
    """
    if len(history) < 2:
        return None

    conversation = "\n".join([f"{m['role']}: {m['content']}" for m in history])
    prompt = HANDOFF_CHECK_PROMPT.format(conversation=conversation)

    try:
        response = await handoff_llm.ainvoke(prompt)
        raw = response.content.strip()
        # Удаляем markdown-обёртку если модель её добавила
        raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        data = json.loads(raw)
    except (json.JSONDecodeError, Exception) as e:
        log.warning(f"Handoff classifier failed to parse JSON: {e}")
        return None

    if data.get("ready_for_handoff"):
        return data
    return None


# ============================================================================
# TELEGRAM NOTIFICATION
# ============================================================================

async def send_telegram_brief(session_id: str, handoff_data: dict, history: list[dict]) -> None:
    """Send handoff brief to Telegram. No-op if token/chat_id not configured."""
    token = settings.telegram_bot_token
    chat_id = settings.telegram_chat_id
    if not token or not chat_id:
        log.info("Telegram not configured — skipping notification")
        return

    conversation = "\n".join([f"{m['role']}: {m['content']}" for m in history])
    text = format_handoff_brief(session_id, handoff_data, conversation)

    # Telegram limit ~4096 chars per message
    text = text[:4000]

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
            )
            if r.status_code != 200:
                log.warning(f"Telegram returned {r.status_code}: {r.text}")
            else:
                log.info(f"Sent Telegram brief for session {session_id[:8]}")
    except Exception as e:
        log.error(f"Telegram send failed: {e}")


# ============================================================================
# ROUTES
# ============================================================================

@app.get("/health")
async def health():
    return {
        "ok": True,
        "vectorstore_loaded": vectorstore is not None,
        "model": settings.llm_model,
        "active_sessions": len(SESSIONS),
    }


# ----------------------------------------------------------------------------
# Lead form endpoint — receives submissions from deadlinecorp.com/lead-form/
# Forwards a structured message to the configured Telegram chat.
# ----------------------------------------------------------------------------

class LeadFormRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    contact: str = Field(..., min_length=1, max_length=200)
    need: str = Field("", max_length=500)
    business: str = Field("", max_length=300)
    when: str = Field("", max_length=50)
    source: str = Field("direct", max_length=100)
    campaign: str = Field("", max_length=200)
    timestamp: str = Field("", max_length=64)


async def send_lead_to_telegram(lead: LeadFormRequest) -> bool:
    """Forward lead-form submission to Telegram. Returns True on success."""
    token = settings.telegram_bot_token
    chat_id = settings.telegram_chat_id
    if not token or not chat_id:
        log.warning("Telegram not configured — lead received but not forwarded")
        return False

    text = (
        f"🔥 НОВЫЙ ЛИД С САЙТА\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"👤 Имя: {lead.name}\n"
        f"📱 Контакт: {lead.contact}\n"
        f"🎯 Хочет: {lead.need or '—'}\n"
        f"🏢 Бизнес: {lead.business or '—'}\n"
        f"⏰ Срок: {lead.when or '—'}\n\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📊 UTM: {lead.source} / {lead.campaign or 'no-campaign'}\n"
        f"🕐 {lead.timestamp or 'now'}\n"
    )

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
            )
            if r.status_code != 200:
                log.warning(f"Telegram returned {r.status_code}: {r.text}")
                return False
            log.info(f"Lead from {lead.name} forwarded to Telegram")
            return True
    except Exception as e:
        log.error(f"Telegram send failed for lead: {e}")
        return False


@app.post("/lead-submit")
async def lead_submit(lead: LeadFormRequest):
    """
    Accepts form data from deadlinecorp.com/lead-form/.
    Forwards to Telegram. Always returns 200 to the form (avoid leaking errors to user).
    """
    success = await send_lead_to_telegram(lead)
    log.info(f"Lead received: {lead.name} | {lead.contact} | need={lead.need!r}")
    return {"ok": True, "delivered": success}


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    if vectorstore is None:
        raise HTTPException(503, "Vector DB not initialized. Run `python ingest.py` first.")

    session = get_session(req.session_id)
    history = session["history"]

    log.info(f"[{req.session_id[:8]}] Q: {req.message[:200]}")

    # 1. RAG retrieval
    docs = vectorstore.similarity_search(req.message, k=4)
    context = "\n\n".join([
        f"[source: {d.metadata.get('source', '?')}]\n{d.page_content}"
        for d in docs
    ])

    # 2. Build prompt with history (last 6 turns max)
    recent = history[-6:]
    history_str = "\n".join([f"{m['role']}: {m['content']}" for m in recent]) or "(новый диалог)"
    prompt = build_chat_prompt(context=context, history=history_str, question=req.message)

    # 3. LLM call
    answer = await call_llm(prompt)
    log.info(f"[{req.session_id[:8]}] A: {answer[:200]}")

    # 4. Update history
    history.append({"role": "user", "content": req.message})
    history.append({"role": "assistant", "content": answer})

    # 5. Handoff check (only if not already handed off)
    handoff_triggered = False
    if not session["handoff_done"]:
        handoff_data = await check_handoff(history)
        if handoff_data:
            # Hard guard: do NOT fire a brief if no real contact was captured.
            # The classifier (small LLM, fallback model) sometimes returns
            # ready_for_handoff=true even when lead_contact is empty or is
            # just a channel name like "telegram" / "в телегу".
            contact = (handoff_data.get("lead_contact") or "").strip()
            contact_lower = contact.lower()
            CHANNEL_NOISE = {
                "", "telegram", "telega", "тг", "в telegram", "в телегу",
                "в личку", "в тг", "email", "почта", "phone", "телефон",
                "не оставил", "не указан", "—", "-", "n/a", "none", "null",
            }
            looks_like_real_address = (
                contact.startswith("@") and len(contact) > 1  # Telegram @username
                or "@" in contact and "." in contact  # email
                or sum(c.isdigit() for c in contact) >= 7  # phone-ish
            )

            if contact_lower in CHANNEL_NOISE or not looks_like_real_address:
                log.info(
                    f"[{req.session_id[:8]}] classifier said ready_for_handoff=true "
                    f"but contact={contact!r} is empty/channel-only — suppressing brief"
                )
            else:
                await send_telegram_brief(req.session_id, handoff_data, history)
                session["handoff_done"] = True
                handoff_triggered = True

    return ChatResponse(answer=answer, handoff=handoff_triggered, session_id=req.session_id)


@app.get("/sessions")
async def list_sessions():
    """Debug endpoint — list active sessions (do not expose in prod without auth)."""
    return {
        "count": len(SESSIONS),
        "sessions": {
            sid: {
                "messages": len(s["history"]),
                "handoff_done": s["handoff_done"],
                "preview": s["history"][:2] if s["history"] else [],
            }
            for sid, s in SESSIONS.items()
        }
    }


@app.delete("/sessions/{session_id}")
async def reset_session(session_id: str):
    """Wipe a single session (useful when testing)."""
    if session_id in SESSIONS:
        del SESSIONS[session_id]
        return {"deleted": session_id}
    raise HTTPException(404, "Session not found")


# ============================================================================
# STARTUP LOG
# ============================================================================

@app.on_event("startup")
async def startup():
    log.info("=" * 60)
    log.info(f"Deadline Sales Bot v{app.version}")
    log.info(f"Model:    {settings.llm_model} (fallback: {settings.llm_fallback_model})")
    log.info(f"Chroma:   {'loaded' if vectorstore else 'NOT LOADED — run ingest.py'}")
    log.info(f"Telegram: {'configured' if settings.telegram_bot_token else 'NOT configured'}")
    log.info(f"Origins:  {settings.allowed_origins}")
    log.info("=" * 60)
