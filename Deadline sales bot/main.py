"""Deadline Sales Bot — FastAPI backend.

Endpoints:
    POST /message            — universal entry point (any channel)
    POST /chat               — DEPRECATED alias for the website widget
    POST /webhooks/telegram  — Telegram Bot API inbound webhook
    POST /lead-submit        — lead-form submission (separate flow)
    GET  /health             — liveness probe

Run locally:
    uvicorn main:app --reload --port 8000

Deploy to Railway: see README.md
"""

import json
import logging
from pathlib import Path
from typing import Optional

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings
from sqlalchemy.orm import Session

from langchain_chroma import Chroma
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_openai import ChatOpenAI

from prompts import build_chat_prompt, HANDOFF_CHECK_PROMPT, format_handoff_brief

# DB-backed services (Day 3-4 migration from in-memory SESSIONS dict + Chroma)
from db.connection import get_db, check_connection
from db.models import Message as MessageRow
from db.vector import similarity_search as pgvector_search
from services.identity import resolve_or_create_customer
from services.conversations import (
    get_or_create_conversation,
    append_message,
    get_recent_messages,
    mark_handoff_done,
)
from channels.telegram import (
    parse_telegram_webhook,
    send_telegram_reply,
)


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

app = FastAPI(title="Deadline Sales Bot", version="0.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.allowed_origins.split(",") if o.strip()],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


# ============================================================================
# LLM CLIENTS (Ollama Cloud — OpenAI-compatible API)
# ============================================================================

def make_llm(model_name: str, *, temperature: float = 0.2, max_tokens: int = 1200) -> ChatOpenAI:
    return ChatOpenAI(
        model=model_name,
        api_key=settings.ollama_api_key,
        base_url=settings.ollama_base_url,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=60,
        default_headers={"X-Title": "Deadline Sales Bot"},
    )

primary_llm = make_llm(settings.llm_model)
fallback_llm = make_llm(settings.llm_fallback_model)
handoff_llm = make_llm(settings.llm_fallback_model, temperature=0.0, max_tokens=1000)


# ============================================================================
# VECTOR STORE — Chroma kept loaded only as a backstop. /message uses pgvector.
# ============================================================================

embeddings = HuggingFaceEmbeddings(
    model_name=EMBEDDING_MODEL,
    model_kwargs={"device": "cpu"},
    encode_kwargs={"normalize_embeddings": True},
)
vectorstore: Optional[Chroma] = None
if CHROMA_DIR.exists():
    vectorstore = Chroma(persist_directory=str(CHROMA_DIR), embedding_function=embeddings)
    log.info(f"Loaded Chroma DB from {CHROMA_DIR} (legacy backstop)")


# ============================================================================
# MODELS — Pydantic request/response shapes
# ============================================================================

# Universal multi-channel message
class MessageRequest(BaseModel):
    channel: str = Field(..., description="website | telegram | instagram | messenger")
    external_id: str = Field(..., min_length=1, max_length=200,
                             description="Channel-side user id: tg_user_id / ig_psid / session_id")
    content: str = Field(..., min_length=1, max_length=4000)
    email: Optional[str] = Field(None, max_length=320)
    username: Optional[str] = Field(None, max_length=200, description="@handle for display")
    channel_conversation_id: Optional[str] = Field(
        None, max_length=200,
        description="Channel-native thread id (Telegram chat_id, IG thread_id). "
                    "For website, pass session_id.",
    )
    extra_meta: Optional[dict] = None


class MessageResponse(BaseModel):
    answer: str
    handoff: bool = False
    customer_id: str
    conversation_id: str


# Legacy /chat shape (kept for the website widget — widget.js sends this)
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

async def call_llm(prompt: str) -> str:
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

async def check_handoff(history_dicts: list[dict]) -> Optional[dict]:
    """Run the classifier over conversation history. Returns parsed JSON
    dict if ready_for_handoff=true, else None."""
    if len(history_dicts) < 2:
        return None

    conversation = "\n".join([f"{m['role']}: {m['content']}" for m in history_dicts])
    prompt = HANDOFF_CHECK_PROMPT.format(conversation=conversation)

    try:
        response = await handoff_llm.ainvoke(prompt)
        raw = response.content.strip()
        raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        data = json.loads(raw)
    except (json.JSONDecodeError, Exception) as e:
        log.warning(f"Handoff classifier failed to parse JSON: {e}")
        return None

    if data.get("ready_for_handoff"):
        return data
    return None


import re

# Pragmatic email regex — local@domain.tld with no whitespace. Not RFC 5322
# strict (those regexes are 600 chars and rarely needed for capture/validate).
EMAIL_REGEX = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def _is_valid_email(s: str) -> bool:
    """Defense-in-depth predicate: even if classifier marks ready_for_handoff,
    we will not fire Telegram brief unless a valid email was captured.

    Policy (decided 2026-05-19): email is the ONLY mandatory contact.
    Telegram @username is mutable and breaks identity if user renames it.
    Phone is also accepted as additional info but does not gate handoff.
    """
    if not s:
        return False
    return bool(EMAIL_REGEX.match(s.strip()))


def _extract_email_from_handoff(handoff_data: dict) -> str:
    """Pull the email from classifier output, tolerant of both the new
    `lead_email` field and the pre-2026-05-19 `lead_contact` field where
    email might have landed."""
    candidate = (handoff_data.get("lead_email") or "").strip()
    if _is_valid_email(candidate):
        return candidate

    # Backward-compat: old classifier returned a single `lead_contact` field
    legacy = (handoff_data.get("lead_contact") or "").strip()
    if _is_valid_email(legacy):
        return legacy
    return ""


def _messages_to_dicts(messages: list[MessageRow]) -> list[dict]:
    """Convert DB Message rows to the simple list[dict] shape that
    check_handoff and send_telegram_brief expect."""
    return [
        {
            "role": m.role.value if hasattr(m.role, "value") else str(m.role),
            "content": m.content,
        }
        for m in messages
    ]


# ============================================================================
# TELEGRAM HANDOFF BRIEF (to operator chat) — unchanged signature
# ============================================================================

async def send_telegram_brief(session_id: str, handoff_data: dict, history_dicts: list[dict]) -> None:
    """Send the handoff brief to the configured operator Telegram chat.
    No-op if token/chat_id not configured (logged)."""
    token = settings.telegram_bot_token
    chat_id = settings.telegram_chat_id
    if not token or not chat_id:
        log.info("Telegram not configured — skipping handoff brief")
        return

    conversation = "\n".join([f"{m['role']}: {m['content']}" for m in history_dicts])
    text = format_handoff_brief(session_id, handoff_data, conversation)[:4000]

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
            )
            if r.status_code != 200:
                log.warning(f"Telegram brief returned {r.status_code}: {r.text[:200]}")
            else:
                log.info(f"Sent Telegram brief for conversation {session_id[:8]}")
    except Exception as e:
        log.error(f"Telegram brief send failed: {e}")


# ============================================================================
# CORE — universal message handler (the brains of /message and /chat alias)
# ============================================================================

async def _handle_message(req: MessageRequest, db: Session) -> MessageResponse:
    """Pipeline:
      1. Identity: (channel, external_id, email?) → Customer (existing or new)
      2. Conversation: get_or_create_conversation per channel thread
      3. Persist the user message
      4. RAG retrieval via pgvector
      5. Build LLM prompt with DB-stored history (last 12 messages)
      6. LLM call
      7. Persist assistant message
      8. Handoff check + (if real contact) fire brief + mark conversation HANDED_OFF
      9. Commit transaction once at the end
    """
    customer = resolve_or_create_customer(
        db,
        channel=req.channel,
        external_id=req.external_id,
        email=req.email,
        username=req.username,
    )

    # For website widget, channel_conversation_id == session_id (which is also external_id).
    # For TG/IG/FB, it's the channel-side thread id (chat_id, thread_id).
    conv_thread_id = req.channel_conversation_id or req.external_id
    conversation = get_or_create_conversation(
        db,
        customer_id=customer.id,
        channel=req.channel,
        channel_conversation_id=conv_thread_id,
    )

    # Persist user message before RAG so it's in the history if anything fails after
    append_message(
        db, conversation.id, role="user",
        content=req.content, extra_meta=req.extra_meta,
    )

    log.info(f"[{str(conversation.id)[:8]}/{req.channel}] Q: {req.content[:200]}")

    # 4. RAG over kb_chunks via pgvector
    docs = pgvector_search(req.content, k=4)
    context = "\n\n".join([
        f"[source: {d.metadata.get('source', '?')}]\n{d.page_content}" for d in docs
    ])

    # 5. History from DB. get_recent_messages returns chronological order.
    # Pull 13 to include the just-appended user message; drop it for the
    # "history before this question" string.
    recent = get_recent_messages(db, conversation.id, limit=13)
    history_for_prompt = recent[:-1] if recent else []
    # Trim to last 12 entries for prompt budget (6 turns of user+assistant)
    history_str = "\n".join(
        [f"{m.role}: {m.content}" for m in history_for_prompt[-12:]]
    ) or "(новый диалог)"

    prompt = build_chat_prompt(context=context, history=history_str, question=req.content)

    # 6. LLM
    answer = await call_llm(prompt)
    log.info(f"[{str(conversation.id)[:8]}/{req.channel}] A: {answer[:200]}")

    # 7. Persist assistant reply
    append_message(db, conversation.id, role="assistant", content=answer)

    # 8. Handoff — gated on a real email (policy 2026-05-19).
    #    Email is the only mandatory contact: it's stable identity, while
    #    Telegram @username is mutable and would break our identity mapping
    #    if the user later renames it.
    handoff_triggered = False
    if not conversation.handoff_done:
        all_recent = get_recent_messages(db, conversation.id, limit=20)
        history_dicts = _messages_to_dicts(all_recent)
        handoff_data = await check_handoff(history_dicts)
        if handoff_data:
            email = _extract_email_from_handoff(handoff_data)
            if email:
                # Persist email on the customer (idempotent if already set).
                # update_email also handles cross-channel merges if another
                # customer happens to own the same email.
                try:
                    from services.identity import update_email
                    update_email(db, customer.id, email)
                except Exception as e:
                    log.warning(
                        f"[{str(conversation.id)[:8]}] update_email failed: {e}"
                    )

                await send_telegram_brief(str(conversation.id), handoff_data, history_dicts)
                mark_handoff_done(db, conversation.id)
                handoff_triggered = True
            else:
                # Build a small diagnostic so logs show why we suppressed
                preview = {
                    k: handoff_data.get(k)
                    for k in ("lead_email", "lead_contact", "lead_telegram_username", "lead_phone")
                    if handoff_data.get(k)
                }
                log.info(
                    f"[{str(conversation.id)[:8]}] classifier ready but no valid email — "
                    f"suppressed. fields={preview!r}"
                )

    # 9. Commit the whole turn atomically
    db.commit()

    return MessageResponse(
        answer=answer,
        handoff=handoff_triggered,
        customer_id=str(customer.id),
        conversation_id=str(conversation.id),
    )


# ============================================================================
# ROUTES
# ============================================================================

@app.get("/health")
async def health():
    return {
        "ok": True,
        "vectorstore_loaded": vectorstore is not None,
        "db_connected": check_connection(),
        "model": settings.llm_model,
    }


@app.post("/message", response_model=MessageResponse)
async def message_endpoint(req: MessageRequest, db: Session = Depends(get_db)):
    """Universal channel-agnostic chat entry point."""
    return await _handle_message(req, db)


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest, db: Session = Depends(get_db)):
    """DEPRECATED alias for the website widget. Identical externally to v0.1.0 —
    keeps the widget on deadlinecorp.com working unchanged."""
    msg_req = MessageRequest(
        channel="website",
        external_id=req.session_id,
        content=req.message,
        channel_conversation_id=req.session_id,
    )
    resp = await _handle_message(msg_req, db)
    return ChatResponse(answer=resp.answer, handoff=resp.handoff, session_id=req.session_id)


@app.post("/webhooks/telegram")
async def telegram_webhook(request: Request, db: Session = Depends(get_db)):
    """Telegram Bot API webhook. Telegram retries on non-200, so we always
    return 200 even on parse/LLM failures (errors are logged)."""
    try:
        payload = await request.json()
    except Exception as e:
        log.warning(f"telegram_webhook: invalid JSON — {e}")
        return {"ok": True}

    normalized = parse_telegram_webhook(payload)
    if normalized is None:
        return {"ok": True}  # non-text update, skip silently

    msg_req = MessageRequest(
        channel=normalized.channel,
        external_id=normalized.external_id,
        content=normalized.content,
        username=normalized.username,
        channel_conversation_id=normalized.channel_conversation_id,
    )

    try:
        resp = await _handle_message(msg_req, db)
    except Exception as e:
        log.error(f"telegram_webhook: _handle_message failed — {e}")
        return {"ok": True}

    # Send reply to the SAME chat the user wrote from
    await send_telegram_reply(
        settings.telegram_bot_token,
        normalized.channel_conversation_id,
        resp.answer,
    )

    return {"ok": True}


# ----------------------------------------------------------------------------
# Lead form endpoint — kept from commit 117e617, independent flow.
# Receives submissions from deadlinecorp.com/lead-form/ and forwards a
# structured message to the configured Telegram operator chat.
# ----------------------------------------------------------------------------

class LeadFormRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    contact: str = Field(..., min_length=1, max_length=200)
    need: str = Field("", max_length=500)
    business: str = Field("", max_length=300)
    task: str = Field("", max_length=2000)  # optional free-text description
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

    # Source-based header — мгновенно видно откуда лид
    source = (lead.source or "direct").lower()
    if source == "meta":
        header = "📢 ЛИД ИЗ META РЕКЛАМЫ"
    elif source in ("google", "google_ads"):
        header = "🔍 ЛИД ИЗ GOOGLE РЕКЛАМЫ"
    elif source in ("tiktok",):
        header = "🎵 ЛИД ИЗ TIKTOK РЕКЛАМЫ"
    elif source == "direct":
        header = "🌐 ПРЯМОЙ ЛИД (без рекламы)"
    else:
        header = f"🔥 ЛИД ({source.upper()})"

    # Расшифровка нашего utm_content в человекочитаемый формат
    creative_labels = {
        "01_typographic": "Универсальный (расчёт за 24ч)",
        "02_dentistry":   "Стоматологии (AI-бот)",
        "03_wanted":      "Ищем предпринимателей",
        "08_no_markup":   "Без наценки агентств",
        "05_idea":        "Идея → Продакшен",
        "06_team":        "Опытная команда + цифры",
        "07_question":    "Знаешь сколько стоит?",
        "04_checklist":   "Чек-лист услуг",
    }
    creative_human = ""
    if lead.campaign:
        for key, label in creative_labels.items():
            if key in lead.campaign:
                creative_human = f"\n🎨 Креатив: {label}"
                break

    # Optional task description — only show if filled in
    task_block = f"\n📝 Описание задачи:\n{lead.task.strip()}\n" if lead.task and lead.task.strip() else ""

    text = (
        f"{header}\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"👤 Имя: {lead.name}\n"
        f"📱 Контакт: {lead.contact}\n"
        f"🎯 Хочет: {lead.need or '—'}\n"
        f"🏢 Бизнес: {lead.business or '—'}\n"
        f"⏰ Срок: {lead.when or '—'}"
        f"{task_block}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📊 Источник: {lead.source} / {lead.campaign or 'no-campaign'}"
        f"{creative_human}\n"
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
    """Accepts form data from deadlinecorp.com/lead-form/.
    Always returns 200 (we don't want to leak errors to the form UI)."""
    success = await send_lead_to_telegram(lead)
    log.info(f"Lead received: {lead.name} | {lead.contact} | need={lead.need!r}")
    return {"ok": True, "delivered": success}


# ============================================================================
# STARTUP LOG
# ============================================================================

@app.on_event("startup")
async def startup():
    log.info("=" * 60)
    log.info(f"Deadline Sales Bot v{app.version}")
    log.info(f"Model:    {settings.llm_model} (fallback: {settings.llm_fallback_model})")
    log.info(f"Chroma:   {'loaded' if vectorstore else 'NOT LOADED (legacy)'}")
    log.info(f"Postgres: {'connected' if check_connection() else 'NOT CONNECTED'}")
    log.info(f"Telegram: {'configured' if settings.telegram_bot_token else 'NOT configured'}")
    log.info(f"Origins:  {settings.allowed_origins}")
    log.info("=" * 60)
