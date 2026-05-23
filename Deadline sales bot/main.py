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

import asyncio
import gc
import json
import logging
import threading
import time
from collections import OrderedDict
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
from db.connection import get_db, check_connection, session_scope
from db.models import Message as MessageRow, Conversation as ConvRow, ConversationStatusEnum
from uuid import UUID as PyUUID
from db.vector import similarity_search as pgvector_search
from services.identity import resolve_or_create_customer
from services.conversations import (
    get_or_create_conversation,
    append_message,
    get_recent_messages,
    mark_handoff_done,
    link_forum_topic,
    find_conversation_by_topic,
    set_operator_takeover,
)
from channels.telegram import (
    parse_telegram_webhook,
    send_telegram_reply,
    send_typing_action,
    create_forum_topic,
    send_to_topic,
    answer_callback_query,
    close_forum_topic,
    reopen_forum_topic,
    extract_attachment,
    forward_attachment,
)
from channels.messenger import (
    parse_messenger_webhook,
    parse_messenger_comment_webhook,
    send_messenger_reply,
    send_messenger_comment_reply,
)
from channels.instagram import (
    parse_instagram_webhook,
    parse_instagram_comment_webhook,
    send_instagram_reply,
    send_instagram_comment_reply,
)
from channels.utils import verify_meta_signature


# ============================================================================
# CONFIG
# ============================================================================

load_dotenv()

class Settings(BaseSettings):
    # ---- LLM provider selection ----
    # We prefer OpenRouter because it gives us a single OpenAI-compatible
    # API surface in front of many model providers (Anthropic, Meta, Google,
    # DeepSeek, Qwen, etc.) with one billing account. Ollama Cloud was the
    # original primary but hit a weekly free-tier limit on 2026-05-23, after
    # which we wired OpenRouter as the active provider.
    #
    # Selection rule (in get_llm_config below): if OPENROUTER_API_KEY is set,
    # use OpenRouter with openrouter_model / openrouter_fallback_model. Else
    # fall back to the legacy Ollama config (ollama_api_key + llm_model).
    openrouter_api_key: Optional[str] = None
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    # Default model: Llama 3.3 70B Instruct — strong on RU/EN, OpenAI-style
    # tool-following, ~$0.30/1M tokens at OpenRouter list price. Change via
    # OPENROUTER_MODEL env var without code changes.
    openrouter_model: str = "meta-llama/llama-3.3-70b-instruct"
    # Fallback: DeepSeek Chat v3.1 — cheap (~$0.14/1M), good multilingual,
    # works when llama hits provider-specific throttling.
    openrouter_fallback_model: str = "deepseek/deepseek-chat"

    # ---- Legacy Ollama Cloud (kept so the switch can be reversed in 1 env var) ----
    ollama_api_key: Optional[str] = None
    ollama_base_url: str = "https://ollama.com/v1"
    llm_model: str = "qwen3.5:397b-cloud"
    llm_fallback_model: str = "glm-4.6:cloud"
    telegram_bot_token: Optional[str] = None
    telegram_chat_id: Optional[str] = None
    # Telegram operator supergroup (forum-mode). Created by team in TG, bot
    # added as admin with manage_topics. Each lead gets a topic; team can
    # take over a conversation from the bot via inline button.
    # chat_id is negative for supergroups (e.g. -1001234567890).
    telegram_operator_group_id: Optional[str] = None
    email_notify: Optional[str] = None
    # Groq for voice transcription (Whisper-large-v3 via OpenAI-compatible
    # endpoint). Free tier covers small volumes. Get a key at console.groq.com.
    # If unset, Telegram voice messages get an apologetic "напишите текстом"
    # reply instead of being transcribed.
    groq_api_key: Optional[str] = None
    # Meta (Instagram + Messenger). Set in Meta App dashboard:
    # - META_VERIFY_TOKEN: any random string, must match what you enter in
    #   App Dashboard → Webhooks → Verify Token
    # - META_APP_SECRET: from App Settings → Basic → App Secret. Used to
    #   verify X-Hub-Signature-256 on every incoming webhook.
    # - META_PAGE_ACCESS_TOKEN: from Messenger → Settings → Access Tokens.
    #   Same token sends both Messenger and IG DM replies (IG is linked to
    #   the same Facebook Page).
    meta_verify_token: Optional[str] = None
    meta_app_secret: Optional[str] = None
    meta_page_access_token: Optional[str] = None
    allowed_origins: str = "https://deadlinecorp.com,https://www.deadlinecorp.com,https://deadline-corp.github.io,http://localhost:3000,http://localhost:5500,http://127.0.0.1:5500"
    log_level: str = "INFO"
    # Optional bearer-token gate for /metrics. If unset, /metrics is open
    # (development convenience). If set, every /metrics request must carry
    # `Authorization: Bearer <token>` matching this value. Use a long random
    # string in production — even though /metrics has no PII, leaking
    # operational counts to competitors is bad hygiene.
    metrics_auth_token: Optional[str] = None

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
# LLM CLIENTS — provider selection by env var (OpenRouter preferred, Ollama fallback)
# ============================================================================

def _resolve_llm_config() -> tuple[str, str, str, str, str]:
    """Pick the active LLM provider based on which env keys are present.

    Returns a tuple (provider_label, api_key, base_url, primary_model, fallback_model).
    OpenRouter wins if its key is set (intentional — Ollama Cloud free tier
    has weekly limits that hit production traffic). Fallback path keeps the
    legacy Ollama config working if OPENROUTER_API_KEY is unset.
    """
    if settings.openrouter_api_key:
        return (
            "openrouter",
            settings.openrouter_api_key,
            settings.openrouter_base_url,
            settings.openrouter_model,
            settings.openrouter_fallback_model,
        )
    if settings.ollama_api_key:
        return (
            "ollama",
            settings.ollama_api_key,
            settings.ollama_base_url,
            settings.llm_model,
            settings.llm_fallback_model,
        )
    # Neither configured — fail loudly at import time so deployment doesn't
    # silently start with no working LLM.
    raise RuntimeError(
        "No LLM provider configured. Set OPENROUTER_API_KEY (preferred) "
        "or OLLAMA_API_KEY in env."
    )


_LLM_PROVIDER, _LLM_API_KEY, _LLM_BASE_URL, _LLM_PRIMARY_MODEL, _LLM_FALLBACK_MODEL = _resolve_llm_config()


def make_llm(model_name: str, *, temperature: float = 0.2, max_tokens: int = 1200) -> ChatOpenAI:
    """Build a ChatOpenAI client pointed at the currently-active provider.
    OpenRouter recommends HTTP-Referer + X-Title headers for attribution and
    rate-limit class; harmless when sent to Ollama Cloud (it ignores them).
    """
    return ChatOpenAI(
        model=model_name,
        api_key=_LLM_API_KEY,
        base_url=_LLM_BASE_URL,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=60,
        default_headers={
            "HTTP-Referer": "https://deadlinecorp.com",
            "X-Title": "Deadline Sales Bot",
        },
    )

primary_llm = make_llm(_LLM_PRIMARY_MODEL)
fallback_llm = make_llm(_LLM_FALLBACK_MODEL)
handoff_llm = make_llm(_LLM_FALLBACK_MODEL, temperature=0.0, max_tokens=1000)
log.info(f"LLM provider: {_LLM_PROVIDER} · primary={_LLM_PRIMARY_MODEL} · fallback={_LLM_FALLBACK_MODEL}")


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
                             description="Channel-side user id: tg_user_id / ig_psid / session_id / commenter_id")
    content: str = Field(..., min_length=1, max_length=4000)
    email: Optional[str] = Field(None, max_length=320)
    username: Optional[str] = Field(None, max_length=200, description="@handle for display")
    channel_conversation_id: Optional[str] = Field(
        None, max_length=200,
        description="Channel-native thread id (Telegram chat_id, IG thread_id, post id for comments). "
                    "For website, pass session_id.",
    )
    # message_type: "dm" (private direct message — full sales flow) OR
    # "comment" (public comment under a post — short reply + redirect to DM,
    # NO handoff, NO email ask).
    message_type: str = Field("dm", pattern="^(dm|comment)$")
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
        log.warning(f"Primary LLM ({_LLM_PRIMARY_MODEL}) failed: {e}. Falling back.")
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


import re as _re_normalize

_LEGACY_PREFIX_RE = _re_normalize.compile(r'^\s*(?://+|>>+|—+)\s*')


def _normalize_bot_reply(text: str) -> str:
    """Defense-in-depth: enforce the post-2026-05-20 reply convention on
    every assistant message, regardless of what the LLM actually returned.

    Convention:
      - No leading marker prefix (`// `, `>> `, `— ` and similar from the
        old slash-comment style)
      - First character is uppercase if it's a letter

    Why this exists:
      Even with an updated SYSTEM_PROMPT and few-shots, the LLM can copy
      the legacy `// ` + lowercase style from older bot replies still
      sitting in the conversation history. We make the new convention
      binding by stripping/capitalizing the model output AND by
      transforming assistant history before feeding it back to the model
      (see usage in `_messages_to_dicts` and history_str builder).
    """
    if not text:
        return text
    t = _LEGACY_PREFIX_RE.sub('', text)
    if t and t[0].islower():
        t = t[0].upper() + t[1:]
    return t


def _messages_to_dicts(messages: list[MessageRow]) -> list[dict]:
    """Convert DB Message rows to the simple list[dict] shape that
    check_handoff and send_telegram_brief expect. Assistant messages are
    passed through `_normalize_bot_reply` so legacy `// `-prefixed history
    does not contaminate downstream prompts."""
    out: list[dict] = []
    for m in messages:
        role = m.role.value if hasattr(m.role, "value") else str(m.role)
        content = m.content
        if role == "assistant":
            content = _normalize_bot_reply(content)
        out.append({"role": role, "content": content})
    return out


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

    # Lazy-create a forum topic in the operator supergroup on first message
    # of this conversation. Topic name = "<channel>: <username or short id>".
    # Skip silently if TELEGRAM_OPERATOR_GROUP_ID isn't configured (the team
    # is fine reading conversations elsewhere or just in DB).
    if (
        settings.telegram_operator_group_id
        and settings.telegram_bot_token
        and not conversation.forum_topic_id
        and not conversation.handoff_done  # don't open topics for already-closed convs
    ):
        topic_label = req.username or req.email or req.external_id[:20]
        topic_name = f"{req.channel}: {topic_label}"
        new_topic_id = await create_forum_topic(
            settings.telegram_bot_token,
            settings.telegram_operator_group_id,
            topic_name,
        )
        if new_topic_id is not None:
            link_forum_topic(db, conversation.id, new_topic_id)
            # Close the topic immediately so operators see the conversation
            # but cannot type into it — the bot is the active speaker until
            # someone presses "Возьму на себя", which reopens the topic.
            # The bot itself (and admins) can still post into a closed topic
            # via the API, so LEAD/BOT mirroring keeps working.
            await close_forum_topic(
                settings.telegram_bot_token,
                settings.telegram_operator_group_id,
                new_topic_id,
            )

    # Persist user message before RAG so it's in the history if anything fails after
    append_message(
        db, conversation.id, role="user",
        content=req.content, extra_meta=req.extra_meta,
    )

    log.info(f"[{str(conversation.id)[:8]}/{req.channel}/{req.message_type}] Q: {req.content[:200]}")

    # Mirror the user message to the operator topic.
    # Format: "📥 LEAD · @username  (🎙️ 12s)\n<text>"  — label on a separate
    # line from content so operators see who's talking before they read the
    # message itself. Username falls back to TG#<id> when not available.
    if conversation.forum_topic_id and settings.telegram_operator_group_id and settings.telegram_bot_token:
        is_voice = isinstance(req.extra_meta, dict) and req.extra_meta.get("source") == "voice"
        lead_label = req.username or f"{req.channel.upper()}#{req.external_id}"
        voice_hint = ""
        if is_voice:
            dur = (req.extra_meta or {}).get("duration_sec", 0)
            voice_hint = f"  🎙️ {dur}s" if dur else "  🎙️"
        mirror_in = f"📥 LEAD · {lead_label}{voice_hint}\n{req.content[:3700]}"
        await send_to_topic(
            settings.telegram_bot_token,
            settings.telegram_operator_group_id,
            conversation.forum_topic_id,
            mirror_in,
        )

    # ---- OPERATOR TAKEOVER: skip LLM and let the human reply manually ----
    # When takeover is on, the bot must NOT respond on its own. We persist
    # the lead's message + mirror it to the topic (above) and return an
    # empty answer — the caller (webhook handler) sees handoff=False and
    # answer="" and skips sending anything to the lead. The operator then
    # types in the topic and the dedicated operator-message handler forwards
    # their reply to the lead. Re-enabled via the inline 🤖 Release button.
    if conversation.operator_takeover:
        log.info(f"[{str(conversation.id)[:8]}] operator_takeover=true — skipping LLM")
        db.commit()
        return MessageResponse(
            answer="",  # caller must not send this to lead
            handoff=False,
            customer_id=str(customer.id),
            conversation_id=str(conversation.id),
        )

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
    # Trim to last 12 entries for prompt budget (6 turns of user+assistant).
    # Assistant messages pass through _normalize_bot_reply so legacy
    # `// `-prefixed history doesn't seed the LLM with the old style.
    def _line_for_prompt(m: MessageRow) -> str:
        role = m.role.value if hasattr(m.role, "value") else str(m.role)
        content = _normalize_bot_reply(m.content) if role == "assistant" else m.content
        return f"{role}: {content}"
    history_str = "\n".join(
        [_line_for_prompt(m) for m in history_for_prompt[-12:]]
    ) or "(новый диалог)"

    # First-turn detection for AI Act Art. 50 disclosure. If after appending
    # the user message we have exactly 1 message in the conversation, this
    # is the lead's first ever message → bot must identify as AI in its reply.
    is_first_turn = len(recent) == 1

    # Comment mode (public IG/FB comment, not private DM). Triggers short
    # reply + redirect-to-DM; no email ask; no handoff brief.
    is_comment_mode = req.message_type == "comment"

    prompt = build_chat_prompt(
        context=context,
        history=history_str,
        question=req.content,
        is_first_turn=is_first_turn,
        is_comment_mode=is_comment_mode,
    )

    # Show "печатает..." indicator in the lead's Telegram chat while the LLM
    # is generating. Disappears on its own in 5s or when we send the reply.
    # Fire-and-forget — purely cosmetic, never fails the request.
    if (
        req.channel == "telegram"
        and req.message_type != "comment"
        and req.channel_conversation_id
        and settings.telegram_bot_token
    ):
        await send_typing_action(settings.telegram_bot_token, req.channel_conversation_id)

    # 6. LLM
    raw_answer = await call_llm(prompt)
    # Defense-in-depth: enforce the no-prefix + capitalize-first-letter
    # convention regardless of what the LLM returned. See _normalize_bot_reply.
    answer = _normalize_bot_reply(raw_answer)
    log.info(f"[{str(conversation.id)[:8]}/{req.channel}/{req.message_type}] A: {answer[:200]}")

    # 7. Persist assistant reply (normalized form — keeps DB clean for future reads)
    append_message(db, conversation.id, role="assistant", content=answer)

    # Mirror bot reply to operator topic with a takeover button.
    # Format: "📤 BOT\n<answer>"  — same convention as the LEAD mirror so
    # operators scan the thread by first line of each post.
    if conversation.forum_topic_id and settings.telegram_operator_group_id and settings.telegram_bot_token:
        button_text = "👤 Возьму на себя"
        callback_data = f"takeover:{conversation.id}"
        mirror_out = f"📤 BOT\n{answer[:3900]}"
        await send_to_topic(
            settings.telegram_bot_token,
            settings.telegram_operator_group_id,
            conversation.forum_topic_id,
            mirror_out,
            reply_markup={
                "inline_keyboard": [[
                    {"text": button_text, "callback_data": callback_data}
                ]]
            },
        )

    # 8. Handoff — gated on a real email (policy 2026-05-19).
    #    Email is the only mandatory contact: it's stable identity, while
    #    Telegram @username is mutable and would break our identity mapping
    #    if the user later renames it.
    #    SKIP entirely for public comments — contacts are not exchanged in
    #    public threads, and operator briefs there would be noise.
    handoff_triggered = False
    if not conversation.handoff_done and not is_comment_mode:
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
        "model": _LLM_PRIMARY_MODEL,
        "llm_provider": _LLM_PROVIDER,
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


async def _handle_operator_callback(callback: dict, db: Session) -> None:
    """Inline-button taps from operators. The `callback_data` payload encodes
    an action and a conversation id, separated by `:`. Supported actions:
      - `takeover:<conv_id>` — TOGGLE operator_takeover (attached under each
         bot reply, so operators flip into takeover with one tap and back
         with the same button if they change their mind)
      - `release:<conv_id>` — FORCE operator_takeover off (attached under
         each operator-forwarded message during takeover, so the team can
         return the conversation to the bot with one tap, no /release typing)
    """
    cb_id = callback.get("id")
    data = callback.get("data", "")
    token = settings.telegram_bot_token

    action, _, conv_id_str = data.partition(":")
    if action not in ("takeover", "release"):
        await answer_callback_query(token, cb_id, text="Unknown action")
        return

    try:
        conv_id = PyUUID(conv_id_str)
    except (ValueError, IndexError):
        await answer_callback_query(token, cb_id, text="Bad conversation id")
        return

    conv = db.get(ConvRow, conv_id)
    if conv is None:
        await answer_callback_query(token, cb_id, text="Conversation not found")
        return

    # takeover: toggle. release: force OFF (idempotent).
    if action == "takeover":
        new_state = not conv.operator_takeover
    else:  # release
        new_state = False
        if conv.operator_takeover is False:
            # Already released — give a friendly toast and skip state churn
            await answer_callback_query(token, cb_id, text="Уже на боте.")
            return
    set_operator_takeover(db, conv_id, new_state)
    db.commit()

    toast = (
        "👤 Принято. Пишите в тему — бот пересылает лиду."
        if new_state
        else "🤖 Освободил. Бот снова отвечает сам."
    )
    await answer_callback_query(token, cb_id, text=toast)

    if conv.forum_topic_id and settings.telegram_operator_group_id:
        # Mirror the takeover state in the topic's lock status:
        #   takeover ON  → reopen topic (operator can type)
        #   takeover OFF → close topic   (only bot speaks, no accidental typing)
        if new_state:
            await reopen_forum_topic(
                token,
                settings.telegram_operator_group_id,
                conv.forum_topic_id,
            )
        else:
            await close_forum_topic(
                token,
                settings.telegram_operator_group_id,
                conv.forum_topic_id,
            )

        state_msg = (
            "🔔 OPERATOR TAKEOVER ON — каждое сообщение в теме идёт лиду напрямую. "
            "Команды: /release — снять takeover · /close — закрыть · /note <текст> — внутренняя пометка."
            if new_state
            else "🔔 OPERATOR RELEASED — бот снова отвечает автономно. Тема снова закрыта для ввода."
        )
        await send_to_topic(
            token,
            settings.telegram_operator_group_id,
            conv.forum_topic_id,
            state_msg,
        )


async def _handle_operator_message(msg: dict, db: Session) -> None:
    """Operator wrote in a forum topic in the operator supergroup. We:
      - skip if it's our own bot's mirror message (is_bot=True),
      - handle commands /release, /close, /note,
      - otherwise forward the text to the lead's original channel and
        persist it as role=operator in the conversation.
    """
    from_user = msg.get("from") or {}
    if from_user.get("is_bot"):
        return  # don't loop on our own mirrored messages

    # Text or caption — both treated as the operator's textual reply.
    # Caption applies when the operator sends media (photo/voice/etc.) with
    # an explanatory line attached.
    text = (msg.get("text") or msg.get("caption") or "").strip()
    attachment = extract_attachment(msg)

    if not text and not attachment:
        return  # service messages, joined/left events, etc.

    topic_id = msg.get("message_thread_id")
    if not topic_id:
        return

    conv = find_conversation_by_topic(db, topic_id)
    if conv is None:
        log.warning(f"operator message in topic {topic_id}: no matching conversation")
        return

    op_label = from_user.get("username") or from_user.get("first_name") or "operator"
    token = settings.telegram_bot_token

    # ---- commands ----
    # Commands are text-only — if the operator attached media to a slash
    # command, we ignore the command intent and treat it as a regular media
    # forward with caption (text). This matches Telegram's UX where slash
    # commands are typed alone.
    if text and not attachment and text.startswith("/release"):
        set_operator_takeover(db, conv.id, False)
        db.commit()
        # Re-close the topic so the bot speaker mode is reflected in UI:
        # operators can no longer type into this conversation until they
        # press "Возьму на себя" again.
        await close_forum_topic(
            token, settings.telegram_operator_group_id, topic_id,
        )
        await send_to_topic(token, settings.telegram_operator_group_id, topic_id,
                            "🤖 Бот снова отвечает автономно. Тема закрыта для ввода — нажмите «Возьму на себя» чтобы снова перехватить.")
        return

    if text and not attachment and text.startswith("/close"):
        conv.status = ConversationStatusEnum.CLOSED.value
        db.commit()
        await send_to_topic(token, settings.telegram_operator_group_id, topic_id,
                            "🔒 Conversation closed.")
        return

    if text and not attachment and text.startswith("/note"):
        note = text[len("/note"):].strip()
        if note:
            append_message(db, conv.id, role="system",
                           content=f"[NOTE by {op_label}] {note}")
            db.commit()
        return

    # ---- forward to lead ----
    # Text path goes through the channel-specific reply helper (Telegram /
    # Instagram / Messenger / website). Media attachments currently work
    # only for Telegram leads — Meta channels (IG/FB) require an explicit
    # attachment upload via /me/message_attachments which is a separate
    # implementation. For now we log if an attachment was sent but the lead
    # is on Meta — operator sees the topic mirror and knows to follow up.
    #
    # For Meta text replies we use HUMAN_AGENT message tag so operator
    # replies work even beyond Meta's standard 24-hour reply window.
    # HUMAN_AGENT extends the window to 7 days. Requires the `human_agent`
    # permission on the Meta App (Standard Access in App Review).
    delivered = False
    if conv.channel == "telegram" and conv.channel_conversation_id:
        if attachment:
            attachment_type, file_id, _ = attachment
            delivered = await forward_attachment(
                token, conv.channel_conversation_id,
                attachment_type, file_id,
                caption=text or None,
            )
            log.info(
                f"[{str(conv.id)[:8]}] operator forwarded {attachment_type} "
                f"to telegram lead (delivered={delivered})"
            )
        else:
            delivered = await send_telegram_reply(token, conv.channel_conversation_id, text)
    elif conv.channel == "instagram" and conv.channel_conversation_id:
        if attachment:
            log.warning(
                f"[{str(conv.id)[:8]}] operator sent {attachment[0]} to IG lead — "
                f"attachment forwarding not implemented for Meta channels yet "
                f"(would need /me/message_attachments upload). Operator should send text only."
            )
            delivered = False
        else:
            delivered = await send_instagram_reply(
                settings.meta_page_access_token,
                conv.channel_conversation_id,
                text,
                messaging_type="MESSAGE_TAG",
                tag="HUMAN_AGENT",
            )
    elif conv.channel == "messenger" and conv.channel_conversation_id:
        if attachment:
            log.warning(
                f"[{str(conv.id)[:8]}] operator sent {attachment[0]} to Messenger lead — "
                f"attachment forwarding not implemented for Meta channels yet "
                f"(would need /me/message_attachments upload). Operator should send text only."
            )
            delivered = False
        else:
            delivered = await send_messenger_reply(
                settings.meta_page_access_token,
                conv.channel_conversation_id,
                text,
                messaging_type="MESSAGE_TAG",
                tag="HUMAN_AGENT",
            )
    else:
        # Website — no push. Message just lives in DB; the widget will see
        # it on the next /chat poll (Phase 2: switch widget to long-poll or SSE).
        log.info(f"[{str(conv.id)[:8]}] operator wrote on channel={conv.channel} (no push, stored only)")
        delivered = True

    append_message(
        db, conv.id, role="operator", content=text,
        extra_meta={"by": op_label, "delivered": delivered},
    )
    db.commit()

    # Attach an inline "Вернуть боту" button so the operator can release
    # the conversation back to the bot with one tap, without typing /release.
    # We send this as a separate confirmation message under the operator's
    # input so it's visually anchored to their reply. The release callback
    # is handled in _handle_operator_callback (action="release").
    if conv.forum_topic_id and settings.telegram_operator_group_id and token:
        delivery_emoji = "✅" if delivered else "⚠️"
        confirm_text = (
            f"{delivery_emoji} Доставлено лиду." if delivered
            else f"{delivery_emoji} НЕ доставлено (см. логи)."
        )
        await send_to_topic(
            token,
            settings.telegram_operator_group_id,
            conv.forum_topic_id,
            confirm_text,
            reply_markup={
                "inline_keyboard": [[
                    {"text": "🤖 Вернуть боту", "callback_data": f"release:{conv.id}"}
                ]]
            },
        )


# ============================================================================
# Telegram webhook — fast-200 + dedup + BackgroundTasks
# ============================================================================
# Why this architecture (incident 2026-05-20 08:34 UTC):
#   Telegram webhook has a ~10-second handler timeout. For a long voice
#   message: Whisper transcription (~3-5s) + RAG (~100ms) + LLM call (2-5s)
#   + handoff classifier (2-3s) → 8-13s total → Telegram considers the
#   webhook failed → retries the SAME update_id → second handler instance
#   starts processing the same payload concurrently → two bge-m3 / Whisper
#   contexts in RAM → OOM kill → container down for hours.
#
# Fix: return 200 OK in <100ms (no work in the request handler), schedule
# the actual processing as a BackgroundTask that runs AFTER the response is
# sent. Telegram is happy (fast 200), no retries. As a defense-in-depth
# layer, dedup by update_id in an in-memory TTL set so even if a retry
# slips through (network blip, deploy gap), the second handler exits early.

# In-memory dedup state. Single-process FastAPI on Railway — no need for
# Redis or cross-instance coordination yet. OrderedDict maps
# update_id -> timestamp. FIFO insertion order is preserved.
_PROCESSED_UPDATES: "OrderedDict[int, float]" = OrderedDict()
# Telegram normally retries within 30-90 seconds. 5 min TTL is generous
# without growing unbounded.
_DEDUP_MAX_AGE_SEC = 300
# Hard cap on dedup set size — protects against pathological burst patterns
# where TTL hasn't caught up. ~30KB of RAM at most.
_DEDUP_MAX_SIZE = 1000


def _seen_update(update_id: Optional[int]) -> bool:
    """Mark `update_id` as processed and return whether we'd seen it before.

    - Fresh update_id → store with current timestamp, return False
    - Repeat update_id → return True without re-storing
    - update_id is None → return False, do NOT store (Telegram occasionally
      omits the field on malformed payloads; we don't want a None-key entry
      to swallow ALL future Nones as "duplicates")

    Eviction sweeps run on every call:
      1. TTL: drop any entries older than _DEDUP_MAX_AGE_SEC
      2. Size cap: if still over _DEDUP_MAX_SIZE, FIFO-evict oldest
    """
    if update_id is None:
        return False

    now = time.time()
    # TTL sweep: pop from the front (oldest) while too old.
    while _PROCESSED_UPDATES:
        oldest_id = next(iter(_PROCESSED_UPDATES))
        if _PROCESSED_UPDATES[oldest_id] < now - _DEDUP_MAX_AGE_SEC:
            _PROCESSED_UPDATES.popitem(last=False)
        else:
            break

    # Already seen? Quick exit.
    if update_id in _PROCESSED_UPDATES:
        return True

    # Size cap: evict oldest before inserting if we're at/above capacity.
    while len(_PROCESSED_UPDATES) >= _DEDUP_MAX_SIZE:
        _PROCESSED_UPDATES.popitem(last=False)

    _PROCESSED_UPDATES[update_id] = now
    return False


async def _process_telegram_update(payload: dict) -> None:
    """Heavy-lifting handler for a Telegram update — runs in a BackgroundTask
    AFTER the webhook has returned 200 OK to Telegram. Opens its own DB
    session via `session_scope` because the request-scoped session from
    Depends(get_db) is already closed by the time we run.

    All exceptions caught and logged — there's no one to return them to here.
    The webhook caller has already responded 200. Re-raising would just spam
    the FastAPI background-task error log and not change client behavior.
    """
    try:
        with session_scope() as db:
            # 1. Inline button taps (callback_query) — operator pressed
            # "Возьму на себя" / "Освободить" on a bot reply.
            if callback := payload.get("callback_query"):
                try:
                    await _handle_operator_callback(callback, db)
                except Exception as e:
                    log.error(f"_handle_operator_callback failed: {e}", exc_info=True)
                return

            msg = payload.get("message") or {}
            chat = msg.get("chat") or {}

            # 2. Operator wrote in a forum topic of the operator supergroup
            # (during takeover) — forward to the lead.
            if (
                chat.get("type") == "supergroup"
                and settings.telegram_operator_group_id
                and str(chat.get("id")) == str(settings.telegram_operator_group_id)
                and msg.get("message_thread_id")
            ):
                try:
                    await _handle_operator_message(msg, db)
                except Exception as e:
                    log.error(f"_handle_operator_message failed: {e}", exc_info=True)
                return

            # 3. Lead's DM — full pipeline (voice transcription happens in
            # parse_telegram_webhook, then RAG + LLM + handoff in _handle_message)
            normalized = await parse_telegram_webhook(
                payload,
                bot_token=settings.telegram_bot_token,
                groq_api_key=settings.groq_api_key,
            )
            if normalized is None:
                return

            msg_req = MessageRequest(
                channel=normalized.channel,
                external_id=normalized.external_id,
                content=normalized.content,
                username=normalized.username,
                channel_conversation_id=normalized.channel_conversation_id,
                message_type=normalized.message_type,
                extra_meta=normalized.extra_meta,
            )

            try:
                resp = await _handle_message(msg_req, db)
            except Exception as e:
                log.error(f"_process_telegram_update: _handle_message failed — {e}", exc_info=True)
                return

            # If operator_takeover is on, _handle_message returns empty answer → skip send.
            if resp.answer:
                await send_telegram_reply(
                    settings.telegram_bot_token,
                    normalized.channel_conversation_id,
                    resp.answer,
                )
    finally:
        # Memory hygiene: force GC after each update so heavy objects
        # (Whisper audio buffer, RAG embeddings, LLM payload, retrieved KB
        # chunks) are reclaimed immediately, not at the next natural GC
        # cycle. Without this, burst loads (3-4 voice messages back to back)
        # caused RAM to creep up and eventually OOM.
        gc.collect()


def _process_in_thread(payload: dict) -> None:
    """Thread entry point — spins up its own asyncio event loop because the
    main event loop is back to serving FastAPI requests by the time we run.

    Why a thread instead of asyncio.create_task on the main loop:
      The pipeline calls bge-m3 embeddings (CPU-bound, ~100-300ms on cold
      first call) which BLOCK the asyncio event loop. While blocked, the
      response can't be flushed to the socket, so Telegram (and curl)
      keeps the connection open and waits — exactly the bug we're fixing.
      A separate OS thread with its own event loop is fully decoupled.
    """
    try:
        asyncio.run(_process_telegram_update(payload))
    except Exception as e:
        log.error(f"_process_in_thread crashed: {e}", exc_info=True)


@app.post("/webhooks/telegram")
async def telegram_webhook(request: Request):
    """Telegram Bot API webhook — returns 200 OK in <100ms regardless of
    payload type. Heavy work runs in a separate OS thread (see
    _process_in_thread above).

    Defense-in-depth dedup on update_id: even if Telegram retries (network
    blip, deploy gap, our background task crashed mid-flight), the second
    delivery exits early without re-processing the same payload.
    """
    try:
        payload = await request.json()
    except Exception as e:
        log.warning(f"telegram_webhook: invalid JSON — {e}")
        return {"ok": True}

    update_id = payload.get("update_id")
    if _seen_update(update_id):
        log.info(f"telegram_webhook: dedup hit for update_id={update_id}")
        return {"ok": True, "dedup": True}

    # Spawn a daemon thread for the heavy pipeline. Daemon=True so the
    # thread doesn't block process shutdown if Railway sends SIGTERM mid-task.
    threading.Thread(
        target=_process_in_thread,
        args=(payload,),
        daemon=True,
        name=f"tg-update-{update_id}",
    ).start()
    return {"ok": True}


# ============================================================================
# Meta webhooks — Facebook Messenger + Instagram DM
# ============================================================================
# Two endpoints share most of the logic. The split is for clarity (different
# channels, different log prefixes) and so Meta's dashboard config maps 1:1.
#
# Meta webhook protocol (for both Messenger and Instagram):
#   GET /webhooks/{channel}?hub.mode=subscribe&hub.verify_token=X&hub.challenge=Y
#       → if X matches META_VERIFY_TOKEN → return body = Y (raw text)
#   POST /webhooks/{channel} with X-Hub-Signature-256 header
#       → verify HMAC against META_APP_SECRET
#       → parse → /message → reply via Send API

from fastapi.responses import PlainTextResponse


def _meta_verify_challenge(mode: str, token: str, challenge: str) -> Optional[str]:
    """Return the challenge string if mode and token match, else None."""
    if mode == "subscribe" and settings.meta_verify_token and token == settings.meta_verify_token:
        return challenge
    return None


@app.get("/webhooks/messenger")
async def messenger_webhook_verify(request: Request):
    """One-time webhook verification when adding the URL in Meta App dashboard."""
    params = request.query_params
    challenge = _meta_verify_challenge(
        params.get("hub.mode", ""),
        params.get("hub.verify_token", ""),
        params.get("hub.challenge", ""),
    )
    if challenge is None:
        log.warning("messenger verify failed: bad mode or token")
        raise HTTPException(403, "verification failed")
    return PlainTextResponse(challenge)


@app.get("/webhooks/instagram")
async def instagram_webhook_verify(request: Request):
    """One-time webhook verification when adding the URL in Meta App dashboard."""
    params = request.query_params
    challenge = _meta_verify_challenge(
        params.get("hub.mode", ""),
        params.get("hub.verify_token", ""),
        params.get("hub.challenge", ""),
    )
    if challenge is None:
        log.warning("instagram verify failed: bad mode or token")
        raise HTTPException(403, "verification failed")
    return PlainTextResponse(challenge)


@app.post("/webhooks/messenger")
async def messenger_webhook(request: Request, db: Session = Depends(get_db)):
    """Receive a Page-scoped Meta update. Routes both DMs (messaging.message)
    and feed comments (changes.field=feed item=comment) through /message and
    replies via the correct Graph API endpoint.

    Always returns 200 — Meta retries on non-200 and we don't want loops on
    transient internal errors. All real errors are logged.
    """
    body = await request.body()

    if not verify_meta_signature(
        settings.meta_app_secret,
        request.headers.get("x-hub-signature-256"),
        body,
    ):
        return {"ok": True}

    try:
        payload = json.loads(body)
    except Exception as e:
        log.warning(f"messenger_webhook: invalid JSON — {e}")
        return {"ok": True}

    # Try DM first, then comment. parse_* return None when the event doesn't
    # match — order doesn't matter except for cost (DM is more common).
    normalized = parse_messenger_webhook(payload) or parse_messenger_comment_webhook(payload)
    if normalized is None:
        return {"ok": True}

    msg_req = MessageRequest(
        channel=normalized.channel,
        external_id=normalized.external_id,
        content=normalized.content,
        username=normalized.username,
        channel_conversation_id=normalized.channel_conversation_id,
        message_type=normalized.message_type,
        extra_meta=normalized.extra_meta,
    )

    try:
        resp = await _handle_message(msg_req, db)
    except Exception as e:
        log.error(f"messenger_webhook: _handle_message failed — {e}")
        return {"ok": True}

    # Dispatch reply through the right Graph API surface.
    if normalized.message_type == "comment":
        comment_id = (normalized.extra_meta or {}).get("comment_id", "")
        await send_messenger_comment_reply(
            settings.meta_page_access_token, comment_id, resp.answer,
        )
    else:
        await send_messenger_reply(
            settings.meta_page_access_token,
            normalized.channel_conversation_id,
            resp.answer,
        )
    return {"ok": True}


@app.post("/webhooks/instagram")
async def instagram_webhook(request: Request, db: Session = Depends(get_db)):
    """Receive an IG-scoped Meta update. Routes both DMs (messaging.message)
    and comments (changes.field=comments) through /message and replies via
    the correct Graph API endpoint.

    Always returns 200 — same rationale as the Messenger handler.
    """
    body = await request.body()

    if not verify_meta_signature(
        settings.meta_app_secret,
        request.headers.get("x-hub-signature-256"),
        body,
    ):
        return {"ok": True}

    try:
        payload = json.loads(body)
    except Exception as e:
        log.warning(f"instagram_webhook: invalid JSON — {e}")
        return {"ok": True}

    normalized = parse_instagram_webhook(payload) or parse_instagram_comment_webhook(payload)
    if normalized is None:
        return {"ok": True}

    msg_req = MessageRequest(
        channel=normalized.channel,
        external_id=normalized.external_id,
        content=normalized.content,
        username=normalized.username,
        channel_conversation_id=normalized.channel_conversation_id,
        message_type=normalized.message_type,
        extra_meta=normalized.extra_meta,
    )

    try:
        resp = await _handle_message(msg_req, db)
    except Exception as e:
        log.error(f"instagram_webhook: _handle_message failed — {e}")
        return {"ok": True}

    if normalized.message_type == "comment":
        comment_id = (normalized.extra_meta or {}).get("comment_id", "")
        await send_instagram_comment_reply(
            settings.meta_page_access_token, comment_id, resp.answer,
        )
    else:
        await send_instagram_reply(
            settings.meta_page_access_token,
            normalized.channel_conversation_id,
            resp.answer,
        )
    return {"ok": True}


# ============================================================================
# /metrics — read-only ops view, optional bearer-token gate
# ============================================================================

def _verify_metrics_token(request: Request) -> None:
    """FastAPI dependency that enforces bearer-token auth on /metrics when
    METRICS_AUTH_TOKEN env var is set. If unset, the endpoint is open
    (development mode — convenient for local smoke testing).

    Production setup: generate `openssl rand -hex 32`, store in Railway as
    METRICS_AUTH_TOKEN, then call:
        curl -H "Authorization: Bearer <token>" .../metrics
    """
    expected = settings.metrics_auth_token
    if not expected:
        return  # endpoint is open in dev mode
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Bearer token required")
    if auth.split(None, 1)[1].strip() != expected:
        raise HTTPException(status_code=403, detail="Invalid token")


@app.get("/metrics")
async def metrics(
    _: None = Depends(_verify_metrics_token),
    db: Session = Depends(get_db),
):
    """Aggregate counts useful for daily ops review. Bearer-token guarded
    when METRICS_AUTH_TOKEN is configured in env."""
    from sqlalchemy import func as sql_func, select as sql_select
    from db.models import Customer, Conversation as ConvRow

    total_customers = db.execute(sql_select(sql_func.count()).select_from(Customer)).scalar()
    total_conversations = db.execute(sql_select(sql_func.count()).select_from(ConvRow)).scalar()
    total_messages = db.execute(sql_select(sql_func.count()).select_from(MessageRow)).scalar()
    handoffs_done = db.execute(
        sql_select(sql_func.count()).select_from(ConvRow).where(ConvRow.handoff_done == True)  # noqa: E712
    ).scalar()

    by_channel = db.execute(
        sql_select(ConvRow.channel, sql_func.count()).group_by(ConvRow.channel)
    ).fetchall()
    by_status = db.execute(
        sql_select(ConvRow.status, sql_func.count()).group_by(ConvRow.status)
    ).fetchall()

    return {
        "totals": {
            "customers": total_customers,
            "conversations": total_conversations,
            "messages": total_messages,
            "handoffs": handoffs_done,
        },
        "by_channel": {row[0]: row[1] for row in by_channel},
        "by_status": {row[0]: row[1] for row in by_status},
    }


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
    lang: str = Field("ru", max_length=5)  # ru | en — landing page language


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

    # Language flag — на каком языке отвечать клиенту
    lang_flag = "🇬🇧 ОТВЕЧАТЬ НА АНГЛИЙСКОМ" if (lead.lang or "ru").lower() == "en" else "🇷🇺 RU"
    header = f"{header}  ·  {lang_flag}"

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
    log.info(f"Model:    {_LLM_PRIMARY_MODEL} (fallback: {_LLM_FALLBACK_MODEL}) via {_LLM_PROVIDER}")
    log.info(f"Chroma:   {'loaded' if vectorstore else 'NOT LOADED (legacy)'}")
    log.info(f"Postgres: {'connected' if check_connection() else 'NOT CONNECTED'}")
    log.info(f"Telegram: {'configured' if settings.telegram_bot_token else 'NOT configured'}")
    log.info(f"Origins:  {settings.allowed_origins}")
    log.info("=" * 60)
