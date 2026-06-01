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
import hmac
import json
import logging
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Optional

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, Depends, Response
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
from services.identity import resolve_or_create_customer, resolve_or_create_customer_with_meta
from services.conversations import (
    get_or_create_conversation,
    append_message,
    get_recent_messages,
    mark_handoff_done,
    link_forum_topic,
    find_conversation_by_topic,
    set_operator_takeover,
)
# Tenant + CRM (Phase 1+7, 2026-05-26 — see ADR v1.3 in Obsidian Vault)
from services.tenant import load_tenant, Tenant
from services.crm import build_adapter, CRMAdapter
from services import crm_queue as crm_queue
from channels.telegram import (
    parse_telegram_webhook,
    send_telegram_reply,
    send_typing_action,
    create_forum_topic,
    build_forum_topic_name,
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
    # Shared secret authenticating inbound Telegram webhooks. Telegram echoes
    # it back in the `X-Telegram-Bot-Api-Secret-Token` header on every update
    # IF the webhook was registered with `secret_token` (see set_telegram_webhook).
    # The /webhooks/telegram handler fails CLOSED: unset → refuse all updates
    # (503); set → every update must carry a matching header (constant-time
    # compare) or it's rejected (401). Generate with `openssl rand -hex 32`.
    # Without it, anyone who knows the public webhook URL could forge operator
    # messages to real leads via the bot's own outbound tokens.
    telegram_webhook_secret: Optional[str] = None
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

    # Bearer-token gate for the /admin/training/* endpoints (and for the
    # trainer widget UI in widget.js — it activates when the URL has
    # ?admin=<this_token>). Required to be set in production — if unset, the
    # endpoints return 503 (refusing to even validate, in case someone
    # accidentally exposes them by misconfiguration).
    training_auth_token: Optional[str] = None

    # ---- Tenant layer (Phase 1, 2026-05-26) ----
    # Which tenant config to load on startup. Default = deadline-corp (us).
    # When extracting the skeleton for resale, each client gets their own
    # tenants/<slug>/ folder with their own config.yaml, kb/, system_prompt.md
    # and secrets — same code, different tenant_slug env var.
    tenant_slug: str = "deadline-corp"

    # ---- Phase 13 — Returning lead memory feature flag ----
    # Включено: бот узнаёт вернувшегося лида (по email / telegram @username) и
    # вспоминает прошлый контекст («CRM как база — понимает, что человек уже был»).
    # Можно выключить через env RETURNING_LEAD_RECALL=false.
    returning_lead_recall: bool = True

    # ---- CRM integration (Phase 0b feature flag + Phase 1+ adapters) ----
    # Master switch. While we're rolling out CRM features incrementally,
    # this stays False in production — every CRM call is gated by it. With
    # crm_enabled=False the bot behaves 1:1 like the pre-CRM version.
    # Flip to True in Railway env only after Phase 7 (event queue) is done
    # AND the integration is smoke-tested locally end-to-end.
    crm_enabled: bool = False

    # Which adapter to instantiate when crm_enabled=True.
    # noop = log to our Postgres only, no external CRM (safe default).
    # hubspot = HubSpotAdapter (Phase 2).
    # bitrix24 = Bitrix24Adapter (Phase 3, deferred per Nikolay 2026-05-26).
    crm_provider: str = "noop"

    # ---- HubSpot credentials (Phase 0c done 2026-05-26) ----
    # Service Key (Beta), Bearer-format pat-na2-xxxx. Created in Settings →
    # Integrations → Service Keys. Scopes: crm.objects.contacts/companies/
    # deals r+w, crm.schemas.contacts/deals r+w, conversations r+w.
    hubspot_access_token: Optional[str] = None
    # HubSpot Account ID (aka Portal ID, Hub ID) — visible in any URL after
    # /portal/<id>/. Needed for building deep-links to contact/deal cards
    # so operators can jump from our admin UI directly into HubSpot.
    hubspot_portal_id: Optional[str] = None
    # Data center region (na1/na2/eu1/etc) — from app-<region>.hubspot.com.
    # Most v3 endpoints are region-agnostic; this is for legacy endpoints
    # that need region-specific subdomains.
    hubspot_region: str = "na2"
    # HubSpot owner id задач/сделок (на кого вешать). Из env HUBSPOT_OWNER_ID.
    # Пусто → задачи без владельца (как было). Заполни — все задачи/сделки
    # пойдут на этого менеджера. Owner id ≠ user id; берётся из /crm/v3/owners
    # или назначь себя владельцем контакта и прочитай hubspot_owner_id обратно.
    hubspot_owner_id: Optional[str] = None

    # ---- Bitrix24 credentials (Phase 0d deferred per Nikolay 2026-05-26) ----
    # Inbound Webhook URL — created in Developer Resources → Inbound webhook.
    # Format: https://<portal>.bitrix24.ru/rest/<user_id>/<webhook_code>/
    # Whole URL is the bearer credential — treat as secret.
    bitrix24_webhook_url: Optional[str] = None
    # Default deal category (pipeline) id for new deals.
    bitrix24_default_category_id: int = 1

    class Config:
        env_file = ".env"
        extra = "ignore"

settings = Settings()

logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("deadline-bot")

# ---- Tenant + CRM initialisation (Phase 1, 2026-05-26) ----
# Loaded eagerly at import time so a bad config.yaml / missing system_prompt.md
# crashes the process at boot rather than on first request. Race: anything
# below this line can `from main import tenant, crm_adapter`.
#
# With CRM_ENABLED=False (the default during rollout), build_adapter returns
# a NoOpAdapter — every CRM call is a no-op log line, the bot behaves
# exactly like the pre-CRM version.
try:
    tenant: Tenant = load_tenant(settings.tenant_slug)
    log.info(
        f"Tenant loaded: slug={tenant.slug} display={tenant.display_name} "
        f"languages={tenant.languages}"
    )
except Exception as e:  # noqa: BLE001 — startup fail-loud is intentional
    log.error(f"Failed to load tenant '{settings.tenant_slug}': {e}")
    raise

crm_adapter: CRMAdapter = build_adapter(settings)
log.info(
    f"CRM adapter: provider={crm_adapter.provider_name} "
    f"crm_enabled={settings.crm_enabled} crm_provider={settings.crm_provider}"
)

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
# Trainer LLM — for /admin/training endpoints. Slightly higher temperature
# than handoff_llm because we want variety on iterative refine (an operator
# rejecting a proposal probably wants a meaningfully different next try, not
# a rephrasing of the same thing). Uses the primary model for quality —
# corrections will guide every future inference, so worth the extra cents.
trainer_llm = make_llm(_LLM_PRIMARY_MODEL, temperature=0.3, max_tokens=800)
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
    source: Optional[str] = Field(None, max_length=300,
                                  description="Traffic source (referrer host + UTM) from the widget — Phase C1.2")


class ChatResponse(BaseModel):
    answer: str
    handoff: bool = False
    session_id: str


# ---- Training / correction-loop request and response models ----

class TrainingDraftRequest(BaseModel):
    """Operator opens a new correction session. Provides the conversation
    snippet that was wrong + a free-text note about what should change."""
    dialog: str = Field(..., min_length=1, max_length=8000)
    correction_note: str = Field(..., min_length=1, max_length=2000)
    channel: Optional[str] = Field(None, max_length=32)
    source_conversation_id: Optional[str] = None  # UUID as str, optional


class TrainingRefineRequest(BaseModel):
    """Operator gives feedback on the previous proposal, asking for a
    different variant. session_id ties it to the live in-memory state."""
    session_id: str
    operator_feedback: str = Field(..., min_length=1, max_length=2000)


class TrainingApproveRequest(BaseModel):
    """Operator accepts the latest proposal — persist to DB.

    Phase 11 (2026-05-27): force_action lets the operator override a
    detected conflict with an existing active rule. Accepted values:
      - None / "" → run conflict check; if conflict, block with details
      - "supersede" → mark conflicting rules inactive + persist new
      - "coexist"   → save new alongside existing (operator overrode the judge)
    "merge" is NOT a server action — the operator handles it by calling
    /refine to write a combined guidance, then approving normally.
    """
    session_id: str
    created_by: str = Field("admin", max_length=100)
    force_action: Optional[str] = None


class TrainingDiscardRequest(BaseModel):
    """Operator abandons the correction without saving."""
    session_id: str


class TrainingProposalResponse(BaseModel):
    """Returned by /draft and /refine — what the trainer LLM came up with.

    Phase 11 (2026-05-27): similar_existing_rules lists active corrections
    whose trigger_context is semantically close to what the operator just
    pasted — surfaced so operators see what they might be conflicting with
    BEFORE they approve. Empty list when nothing close enough was found.
    """
    session_id: str
    proposed_rule: str
    proposed_response: Optional[str] = None
    confirmation_question: str
    similar_existing_rules: list[dict] = Field(default_factory=list)


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


# Email-backstop (надёжность handoff): иногда классификатор не выносит email в
# lead_email ИЛИ возвращает not-ready, хотя лид уже дал email + внятный бриф.
# Ловим email/имя/бриф прямо из сообщений лида на сервере — детерминированно.
_EMAIL_INLINE_RE = re.compile(r"[^\s@,;]+@[^\s@,;]+\.[^\s@,;]+")
_NAME_RE = re.compile(r"\b([А-ЯЁ][а-яё]{1,}|[A-Z][a-z]{1,})\b")


def _user_messages(history_dicts: list[dict]) -> list[str]:
    return [
        (m.get("content") or "")
        for m in history_dicts
        if m.get("role") == "user"
    ]


def _scan_lead_email(history_dicts: list[dict]) -> str:
    """Самый свежий валидный email из сообщений лида (regex по тексту)."""
    for c in reversed(_user_messages(history_dicts)):
        m = _EMAIL_INLINE_RE.search(c or "")
        if m and _is_valid_email(m.group(0)):
            return m.group(0)
    return ""


def _has_brief(history_dicts: list[dict]) -> bool:
    """Достаточно ли контекста, чтобы это был реальный лид (не спам с email)."""
    msgs = [c for c in _user_messages(history_dicts) if c and c.strip()]
    total = sum(len(c) for c in msgs)
    return len(msgs) >= 2 and total >= 40


def _guess_lead_name(history_dicts: list[dict], email: str) -> str:
    """Лучше-усилие имя: слово с заглавной перед email (исключая доменные части)."""
    for c in reversed(_user_messages(history_dicts)):
        if email and email in c:
            before = c.split(email)[0]
            m = _NAME_RE.search(before)
            if m:
                return m.group(1)
    return ""


# Скан телеграм-хэндла и телефона из сообщений лида (любой контакт → handoff).
_TG_RE = re.compile(r"@([A-Za-z][A-Za-z0-9_]{3,31})")
_PHONE_RE = re.compile(r"\+?\d[\d\-\s()]{7,}\d")


def _scan_lead_telegram(history_dicts: list[dict]) -> str:
    for c in reversed(_user_messages(history_dicts)):
        # не путать с email (@ в адресе) — берём @ только если не часть email
        for m in _TG_RE.finditer(c or ""):
            start = m.start()
            if start > 0 and (c[start - 1].isalnum() or c[start - 1] == "."):
                continue  # похоже на email-домен, пропускаем
            return "@" + m.group(1)
    return ""


def _scan_lead_phone(history_dicts: list[dict]) -> str:
    for c in reversed(_user_messages(history_dicts)):
        if "@" in (c or ""):
            c = re.sub(r"\S+@\S+", " ", c)  # вырезать email перед поиском телефона
        m = _PHONE_RE.search(c or "")
        if m:
            digits = re.sub(r"\D", "", m.group(0))
            if 9 <= len(digits) <= 15:
                return m.group(0).strip()
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
    customer, was_returning_match = resolve_or_create_customer_with_meta(
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
        topic_name = build_forum_topic_name(
            db, customer, conversation,
            lead_name=topic_label,
            channel=req.channel,
        )
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

    # ---- Phase 13: Returning Lead Memory state machine ----
    # All branches are guarded by the feature flag (default False) and
    # NOT comment_mode (recall UX makes no sense for public comments).
    # Flag off → zero behavior change, all variables default to neutral.
    recall_skip_normal_flow = False  # when True, skip LLM/RAG and return early
    recall_continue = False          # when True, swap context loader to with_recall variant
    _phase13_answer: str = ""        # recall-generated reply (bypass normal LLM flow)

    is_comment_mode_early = req.message_type == "comment"  # mirrors is_comment_mode below

    if settings.returning_lead_recall and not is_comment_mode_early:
        from datetime import datetime, timezone as _tz
        from sqlalchemy import select as _select, desc as _desc
        from services.returning_lead import (
            should_trigger_recall,
            generate_topic_summary,
            classify_topic_decision,
            archive_stale_conversations,
        )
        from langchain_core.messages import HumanMessage as _HumanMessage
        from prompts import render_recall_greeting
        from sqlalchemy import func as _sqlalchemy_func

        # Count existing assistant messages on this conversation BEFORE this turn.
        # is_fresh_conv means: bot hasn't spoken yet (user message we just appended
        # is the only message, or no assistant messages exist yet).
        _asst_count = db.execute(
            _select(_sqlalchemy_func.count()).select_from(MessageRow).where(
                MessageRow.conversation_id == conversation.id,
                MessageRow.role == "assistant",
            )
        ).scalar() or 0
        is_fresh_conv = (_asst_count == 0)

        # ---- Branch A: Emit recall greeting on fresh conversation ----
        if is_fresh_conv and was_returning_match and should_trigger_recall(db, customer.id):
            # IMPORTANT FIX (P13.T15 Bug 4): wrap entire Branch A in try/except so
            # that any LLM failure (OpenRouter throttle, timeout, network error) does
            # NOT crash the request. On exception we fall through to normal flow.
            try:
                # Find the most recent *completed* prior conversation for this customer.
                # Exclude OPEN conversations (concurrent active threads on another channel
                # are not "prior projects" — only closed/handed-off/resolved/abandoned/
                # archived convs represent a previous engagement we can recall).
                prior_conv = db.execute(
                    _select(ConvRow)
                    .where(
                        ConvRow.customer_id == customer.id,
                        ConvRow.id != conversation.id,
                        ConvRow.status.in_([
                            ConversationStatusEnum.HANDED_OFF.value,
                            ConversationStatusEnum.RESOLVED.value,
                            ConversationStatusEnum.ABANDONED.value,
                            ConversationStatusEnum.ARCHIVED.value,
                        ]),
                    )
                    .order_by(_desc(ConvRow.last_message_at))
                    .limit(1)
                ).scalar_one_or_none()

                if prior_conv is not None:
                    summary = generate_topic_summary(handoff_llm, db, prior_conv)
                    days_ago = 0
                    if prior_conv.last_message_at:
                        _prior_ts = prior_conv.last_message_at
                        if _prior_ts.tzinfo is None:
                            _prior_ts = _prior_ts.replace(tzinfo=_tz.utc)
                        days_ago = (datetime.now(_tz.utc) - _prior_ts).days

                    _recall_lang = (tenant.languages[0] if tenant.languages else "ru")
                    greeting_prompt = render_recall_greeting(
                        language=_recall_lang,
                        summary=summary,
                        days_ago=days_ago,
                        user_message=req.content,
                    )
                    recall_greeting_text = handoff_llm.invoke(
                        [_HumanMessage(content=greeting_prompt)]
                    ).content.strip()

                    append_message(
                        db, conversation.id, role="assistant",
                        content=recall_greeting_text,
                        extra_meta={"kind": "recall_greeting", "prior_conv_id": str(prior_conv.id)},
                    )
                    log.info(
                        f"[{str(conversation.id)[:8]}] Phase13 Branch A: recall greeting emitted "
                        f"(prior_conv={str(prior_conv.id)[:8]}, days_ago={days_ago})"
                    )
                    _phase13_answer = recall_greeting_text
                    recall_skip_normal_flow = True
            except Exception as _branch_a_exc:
                log.warning(
                    "[recall] Phase13 Branch A failed for customer=%s: %s — falling through to normal flow",
                    customer.id, _branch_a_exc,
                )
                # Reset Phase 13 flags so the normal flow proceeds unaffected.
                recall_skip_normal_flow = False
                _phase13_answer = ""

        # ---- Branch B: Lead replied to a recall greeting ----
        elif not is_fresh_conv:
            # Find last assistant message on this conversation.
            last_asst = db.execute(
                _select(MessageRow)
                .where(
                    MessageRow.conversation_id == conversation.id,
                    MessageRow.role == "assistant",
                )
                .order_by(_desc(MessageRow.created_at))
                .limit(1)
            ).scalar_one_or_none()

            _last_meta = (last_asst.extra_meta or {}) if last_asst else {}
            if _last_meta.get("kind") == "recall_greeting":
                prior_conv_id_str = _last_meta.get("prior_conv_id")
                prior_conv_for_b = None
                if prior_conv_id_str:
                    import uuid as _uuid
                    try:
                        prior_conv_for_b = db.get(ConvRow, _uuid.UUID(prior_conv_id_str))
                    except Exception:
                        pass

                prior_summary = (prior_conv_for_b.summary or "") if prior_conv_for_b else ""
                result = classify_topic_decision(
                    handoff_llm,
                    summary=prior_summary,
                    recall_greeting=last_asst.content,
                    user_reply=req.content,
                )
                decision = result.get("decision", "UNCLEAR")
                confidence = result.get("confidence", 0.0)

                log.info(
                    f"[{str(conversation.id)[:8]}] Phase13 Branch B: classifier → "
                    f"decision={decision} confidence={confidence:.2f}"
                )

                if decision == "NEW" and confidence >= 0.65:
                    # Spawn a child conversation; archive the current one.
                    # Keep the SAME channel_conversation_id so that subsequent
                    # messages from the lead (which arrive with the original
                    # chat_id / session_id) are routed to this new OPEN conv
                    # by get_or_create_conversation's status=OPEN filter.
                    # (The old conv is archived in the same transaction, so the
                    # lookup will find only this new one.)

                    # CRITICAL FIX (P13.T15 Bug 1): capture old conv id BEFORE
                    # reassignment so we can re-point the pivot user message below.
                    _old_conv_id = conversation.id

                    new_conv = ConvRow(
                        customer_id=customer.id,
                        channel=conversation.channel,
                        channel_conversation_id=conversation.channel_conversation_id,
                        status=ConversationStatusEnum.OPEN.value,
                        parent_conversation_id=conversation.id,
                    )
                    db.add(new_conv)
                    db.flush()  # populate new_conv.id

                    # Archive the conversation the lead was just on.
                    conversation.status = ConversationStatusEnum.ARCHIVED.value
                    conversation.archived_at = datetime.now(_tz.utc)

                    # Archive any other stale open conversations for this customer.
                    archive_stale_conversations(db, customer.id, except_conv_id=new_conv.id)

                    log.info(
                        f"[{str(conversation.id)[:8]}] Phase13: NEW branch — spawned child "
                        f"conv={str(new_conv.id)[:8]}, archived old conv"
                    )
                    # Reassign so all subsequent flow runs inside the new conv.
                    conversation = new_conv

                    # CRITICAL FIX (P13.T15 Bug 1): The user's pivoting message was
                    # already appended to the OLD conversation (line ~668, before the
                    # Phase 13 state machine ran). Move it onto new_conv so that:
                    #   - the LLM sees the pivoting question in its history
                    #   - apply_signals_on_turn on new_conv sees a real first-touch msg
                    #   - operator forum topic mirror shows the pivot message
                    _pivot_msg = db.execute(
                        _select(MessageRow)
                        .where(
                            MessageRow.conversation_id == _old_conv_id,
                            MessageRow.role == "user",
                        )
                        .order_by(_desc(MessageRow.created_at))
                        .limit(1)
                    ).scalar_one_or_none()
                    if _pivot_msg is not None:
                        _pivot_msg.conversation_id = new_conv.id
                        db.flush()
                        log.info(
                            "[recall] Phase13: moved pivot user msg %s from old conv %s to new conv %s",
                            _pivot_msg.id, _old_conv_id, new_conv.id,
                        )

                    # IMPORTANT FIX (P13.T15 Bug 5): forum topic was created BEFORE
                    # the Phase 13 state machine ran, so new_conv has forum_topic_id=None.
                    # Explicitly create a topic for new_conv so operators can see the
                    # pivot turn immediately without waiting for the next user message.
                    if (
                        settings.telegram_operator_group_id
                        and settings.telegram_bot_token
                        and not new_conv.forum_topic_id
                    ):
                        try:
                            _topic_label_new = req.username or req.email or req.external_id[:20]
                            _topic_name_new = build_forum_topic_name(
                                db, customer, new_conv,
                                lead_name=_topic_label_new,
                                channel=req.channel,
                            )
                            _new_topic_id = await create_forum_topic(
                                settings.telegram_bot_token,
                                settings.telegram_operator_group_id,
                                _topic_name_new,
                            )
                            if _new_topic_id is not None:
                                link_forum_topic(db, new_conv.id, _new_topic_id)
                                await close_forum_topic(
                                    settings.telegram_bot_token,
                                    settings.telegram_operator_group_id,
                                    _new_topic_id,
                                )
                                log.info(
                                    "[recall] Phase13: created forum topic %s for new_conv %s",
                                    _new_topic_id, str(new_conv.id)[:8],
                                )
                        except Exception as _e:
                            log.warning(
                                "[recall] Phase13: failed to create forum topic for new_conv %s: %s",
                                str(new_conv.id)[:8], _e,
                            )

                elif decision == "CONTINUE" and confidence >= 0.65:
                    # Signal the context loader to include prior conv tail.
                    recall_continue = True
                    log.info(
                        f"[{str(conversation.id)[:8]}] Phase13: CONTINUE — "
                        "will use get_recent_messages_with_recall for context"
                    )

                else:
                    # UNCLEAR or low confidence — ask for explicit clarification.
                    _recall_lang_b = (tenant.languages[0] if tenant.languages else "ru")
                    if _recall_lang_b == "en":
                        clarify_text = "Could you clarify — continue the prior project or start a new one?"
                    else:
                        clarify_text = "Уточни, пожалуйста: продолжаем тот проект или начинаем новый?"

                    append_message(
                        db, conversation.id, role="assistant",
                        content=clarify_text,
                        extra_meta={"kind": "recall_clarify"},
                    )
                    log.info(
                        f"[{str(conversation.id)[:8]}] Phase13 Branch B: UNCLEAR → clarification sent"
                    )
                    _phase13_answer = clarify_text
                    recall_skip_normal_flow = True

    # ---- Early return when Phase 13 handled the reply ----
    if recall_skip_normal_flow:
        # C1: Mirror the lead's triggering message to the operator forum-topic
        # BEFORE returning, so operators see the user's question alongside the
        # recall greeting that follows it.  Same format as the normal-flow mirror
        # below — no new dependencies, just the same send_to_topic call.
        if conversation.forum_topic_id and settings.telegram_operator_group_id and settings.telegram_bot_token:
            _is_voice_recall = isinstance(req.extra_meta, dict) and req.extra_meta.get("source") == "voice"
            _lead_label_recall = req.username or f"{req.channel.upper()}#{req.external_id}"
            _voice_hint_recall = ""
            if _is_voice_recall:
                _dur_recall = (req.extra_meta or {}).get("duration_sec", 0)
                _voice_hint_recall = f"  🎙️ {_dur_recall}s" if _dur_recall else "  🎙️"
            _mirror_in_recall = f"📥 LEAD · {_lead_label_recall}{_voice_hint_recall}\n{req.content[:3700]}"
            await send_to_topic(
                settings.telegram_bot_token,
                settings.telegram_operator_group_id,
                conversation.forum_topic_id,
                _mirror_in_recall,
            )
        db.commit()
        return MessageResponse(
            answer=_phase13_answer,
            handoff=False,
            customer_id=str(customer.id),
            conversation_id=str(conversation.id),
        )

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

    # 4b. RAG over training_corrections — fetch operator-curated lessons that
    # match the current lead message in bge-m3 embedding space. These take
    # priority over generic KB and few-shots (see TRAINER block in SYSTEM_PROMPT).
    # Fail-safe: if the table doesn't exist yet or the query errors, we proceed
    # without corrections — the bot still works, just no operator overrides.
    correction_rules: list[dict] = []
    try:
        from services.training import retrieve_corrections
        correction_rules = retrieve_corrections(
            db, query=req.content, k=3, channel=req.channel,
        )
        if correction_rules:
            log.info(
                f"[{str(conversation.id)[:8]}] applied {len(correction_rules)} training "
                f"corrections (distances: {[round(c['distance'], 3) for c in correction_rules]})"
            )
    except Exception as e:
        log.warning(f"retrieve_corrections failed (non-fatal): {e}")

    # 5. History from DB. get_recent_messages returns chronological order.
    # Pull 13 to include the just-appended user message; drop it for the
    # "history before this question" string.
    # Phase 13 CONTINUE branch: include tail of prior conversation for context.
    if recall_continue:
        from services.conversations import get_recent_messages_with_recall
        recent = get_recent_messages_with_recall(
            db, customer_id=customer.id, active_conv_id=conversation.id, limit=13
        )
    else:
        recent = get_recent_messages(db, conversation.id, limit=13)

    # 5b. Per-turn lead signals (Phase 9, 2026-05-27) — update interaction_type
    # (set once on first touch), lead_score (incremental + content keywords),
    # and lead_temperature (engagement triggers + decay). Mutates `customer`
    # in place; commit happens at the end of the turn alongside the message.
    signal_update = None
    if settings.crm_enabled and req.message_type != "comment":
        try:
            from services.lead_signals import apply_signals_on_turn
            signal_update = apply_signals_on_turn(
                customer=customer,
                recent_messages=recent,
                lead_message_text=req.content,
                channel=req.channel,
                message_type=req.message_type,
                tenant_config=tenant.raw_config,
            )
            if signal_update.is_first_touch:
                log.info(
                    f"[{str(conversation.id)[:8]}] first touch — interaction={signal_update.interaction_type} "
                    f"score={signal_update.new_score} temp={signal_update.new_temperature} "
                    f"keywords={list(signal_update.matched_keywords)}"
                )
            elif signal_update.new_score != signal_update.old_score or signal_update.new_temperature != signal_update.old_temperature:
                log.info(
                    f"[{str(conversation.id)[:8]}] signals: "
                    f"score {signal_update.old_score}→{signal_update.new_score} "
                    f"temp {signal_update.old_temperature}→{signal_update.new_temperature} "
                    f"keywords={list(signal_update.matched_keywords)}"
                )
        except Exception as exc:  # noqa: BLE001
            log.warning(f"[{str(conversation.id)[:8]}] lead_signals failed (non-fatal): {exc}")

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

    # ---- Созвон (call booking, 2026-06-01) ----
    # Сервер считает свободные слоты (будни 11–20, UTC+7) и кладёт их в промпт —
    # бот предлагает РОВНО эти времена. Когда лид выбирает слот, сервер бронирует:
    # стадия «📞 Созвон назначен» + next_meeting_at в CRM + напоминания лиду (в его
    # чат) и админу (опер-группа) за день / 3ч / 1ч. Логика — в services/scheduling.
    #
    # ТОЛЬКО в мессенджере (ТГ/WA/IG): созвон — конверсия именно там. На САЙТЕ цель
    # другая — ответить + взять email + увести в Telegram (созвон предложат уже в ТГ),
    # поэтому слоты на сайте НЕ предлагаем (см. # КАНАЛ в SYSTEM_PROMPT).
    _call_slots_human = None
    _just_booked_human = None
    _booking_channel_ok = (req.channel or "website").lower() != "website"
    if settings.crm_enabled and not is_comment_mode and _booking_channel_ok:
        try:
            from services import scheduling as _sched
            from datetime import datetime as _dtm, timezone as _tzu
            _profile = dict(customer.profile_data or {})
            _now = _dtm.now(_tzu.utc)
            _booked = _profile.get("booked_call_at")
            _offered = []
            for _x in (_profile.get("offered_call_slots") or []):
                try:
                    _offered.append(_dtm.fromisoformat(_x))
                except Exception:  # noqa: BLE001
                    pass
            _chosen = _sched.parse_slot_choice(req.content, _offered) if _offered else None
            _lead_msg_n = sum(
                1 for m in recent
                if (m.role.value if hasattr(m.role, "value") else str(m.role)) == "user"
            )
            if _chosen is not None and not _booked:
                # --- БРОНЬ ---
                _medium = _sched.detect_call_medium(req.content) or _profile.get("call_medium")
                _profile["booked_call_at"] = _chosen.isoformat()
                if _medium:
                    _profile["call_medium"] = _medium
                _profile.pop("offered_call_slots", None)
                customer.profile_data = _profile
                _just_booked_human = _sched.format_slot_human(_chosen, _now)
                conversation.lead_stage = "on_call"
                try:
                    from services.crm_dispatch import dispatch_stage_change
                    dispatch_stage_change(
                        customer_id=str(customer.id),
                        crm_deal_id=conversation.crm_deal_id,
                        new_stage="on_call",
                        conversation_id=str(conversation.id),
                        next_meeting_at=_chosen,
                    )
                except Exception as _ce:  # noqa: BLE001
                    log.warning(f"[{str(conversation.id)[:8]}] call stage dispatch failed: {_ce}")
                try:
                    from services.scheduled_actions import write_call_booking, write_call_reminder
                    _chat = getattr(conversation, "channel_conversation_id", None)
                    _is_msgr = (req.channel or "website").lower() != "website"
                    _lead_name = (customer.name or customer.email or "лид")
                    _contact = customer.email or (customer.identity_keys or {}).get("tg_handle") or ""
                    await asyncio.to_thread(
                        write_call_booking,
                        customer_id=str(customer.id),
                        conversation_id=str(conversation.id),
                        channel=req.channel,
                        chat_id=str(_chat) if _chat else None,
                        call_at=_chosen,
                        medium=_medium,
                    )
                    for _fire, _label in _sched.reminder_schedule(_chosen, _now):
                        # Лиду — только если есть канал, куда писать (мессенджер).
                        if _chat and _is_msgr:
                            await asyncio.to_thread(
                                write_call_reminder,
                                customer_id=str(customer.id),
                                conversation_id=str(conversation.id),
                                channel=req.channel,
                                chat_id=str(_chat),
                                due_at=_fire,
                                text=_sched.lead_reminder_text(_chosen, _label, _medium),
                                audience="lead",
                            )
                        # Админу — в опер-группу.
                        if settings.telegram_operator_group_id:
                            await asyncio.to_thread(
                                write_call_reminder,
                                customer_id=str(customer.id),
                                conversation_id=str(conversation.id),
                                channel=req.channel,
                                chat_id=str(settings.telegram_operator_group_id),
                                due_at=_fire,
                                text=_sched.admin_reminder_text(_chosen, _lead_name, _label, _medium, _contact),
                                audience="admin",
                            )
                except Exception as _re:  # noqa: BLE001
                    log.warning(f"[{str(conversation.id)[:8]}] call reminders write failed: {_re}")
                log.info(f"[{str(conversation.id)[:8]}] call booked at {_chosen.isoformat()} medium={_medium}")
            elif not _booked and _lead_msg_n >= 2:
                # --- ПРЕДЛОЖИТЬ СЛОТЫ --- (не на первом «привет»)
                # Если лид в ЭТОМ сообщении выразил пожелание («завтра утром»,
                # «в среду») — пересчитываем под него; иначе переиспользуем ранее
                # предложенные (стабильность между ходами), либо считаем ближайшие.
                _pref_nb, _pref_hmin, _pref_hmax = _sched.parse_time_preference(req.content, _now)
                _has_pref = _pref_nb is not None or _pref_hmin is not None
                _valid = [s for s in _offered if s > _now]
                if _valid and len(_valid) >= 2 and not _has_pref:
                    _slots = _valid[:2]
                else:
                    try:
                        from services.scheduled_actions import get_taken_call_slots
                        _taken = await asyncio.to_thread(get_taken_call_slots, _now)
                    except Exception:  # noqa: BLE001
                        _taken = []
                    _slots = _sched.compute_free_slots(
                        _now, taken=_taken, n=2,
                        not_before=_pref_nb, hour_min=_pref_hmin, hour_max=_pref_hmax,
                    )
                    if _slots:
                        _profile["offered_call_slots"] = [s.isoformat() for s in _slots]
                        customer.profile_data = _profile
                if _slots:
                    _call_slots_human = [_sched.format_slot_human(s, _now) for s in _slots]
        except Exception as _se:  # noqa: BLE001
            log.warning(f"[{str(conversation.id)[:8]}] call-booking flow skipped: {_se}")

    prompt = build_chat_prompt(
        context=context,
        history=history_str,
        question=req.content,
        is_first_turn=is_first_turn,
        is_comment_mode=is_comment_mode,
        corrections=correction_rules,
        channel=req.channel,
        call_slots=_call_slots_human,
        just_booked=_just_booked_human,
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

    # Anti-repeat guard (код-уровень): llama иногда дословно повторяет свой
    # прошлый ответ на коротких follow-up'ах («ааа», email, «успеете?»). Промпт-
    # правило это не добивает, поэтому: сравниваем с предыдущей репликой бота и,
    # если почти дубль, перегенерим ОДИН раз с явной подсказкой «скажи иначе».
    try:
        import difflib
        _prev_bot = ""
        for _m in reversed(recent or []):
            _role = _m.role.value if hasattr(_m.role, "value") else str(_m.role)
            if _role == "assistant":
                _prev_bot = _m.content or ""
                break
        if _prev_bot and raw_answer:
            _sim = difflib.SequenceMatcher(
                None, _prev_bot.lower().strip(), raw_answer.lower().strip()
            ).ratio()
            if _sim >= 0.72:
                log.info(
                    f"[{str(conversation.id)[:8]}] anti-repeat: sim={_sim:.2f} → regen"
                )
                _nudge = (
                    prompt
                    + "\n\n# СТОП-ПОВТОР (КРИТИЧНО)\nТвой ПРОШЛЫЙ ответ был дословно:\n«"
                    + _prev_bot.strip()
                    + "»\nЭто УЖЕ сказано — повторять НЕЛЬЗЯ. Дай ДРУГОЙ ответ: начни "
                    "с других слов (если начинал с «Так,» — начни иначе), добавь новую "
                    "мысль или вопрос, среагируй ИМЕННО на последнее сообщение лида. "
                    "Коротко, по-человечески, без повтора прошлой фразы."
                )
                _re_answer = await call_llm(_nudge)
                # Берём перегенерацию только если она реально отличается.
                if _re_answer and difflib.SequenceMatcher(
                    None, _prev_bot.lower().strip(), _re_answer.lower().strip()
                ).ratio() < 0.9:
                    raw_answer = _re_answer
                else:
                    # Всё ещё дубль — добавим живую вариацию, чтоб не звучать роботом.
                    raw_answer = (
                        _re_answer or raw_answer
                    ).rstrip() + " Что скажете?"
    except Exception as _are:  # noqa: BLE001
        log.debug(f"anti-repeat guard skipped: {_are}")

    # Channel guard: в мессенджере (лид УЖЕ в Telegram) бот НЕ должен звать
    # «перейти/продолжить в telegram @deadline_corp» и просить email «продублировать»
    # — вы уже здесь. Few-shot это перебивает инструкцию, поэтому ловим в коде:
    # 1 перегенерация с жёсткой подсказкой, иначе вырезаем нарушающие предложения.
    def _msgr_leaks(text: str) -> bool:
        low = (text or "").lower()
        if "@deadline_corp" in low:
            return True
        if "telegram" in low and any(w in low for w in ("продолж", "перейд", "перейти", "напишите нам", "пишите нам", "напишите в")):
            return True
        if "email" in low and any(w in low for w in ("продубл", "дублир")):
            return True
        return False
    try:
        if (req.channel or "website").lower() != "website" and _msgr_leaks(raw_answer):
            log.info(f"[{str(conversation.id)[:8]}] channel-guard: messenger reply leaked site-phrasing → regen")
            _ch_nudge = (
                prompt
                + "\n\n# СТОП: ТЫ УЖЕ В ЭТОМ ЧАТЕ (мессенджер).\n"
                "НЕ зови «перейти/продолжить в telegram @deadline_corp» — вы УЖЕ здесь, это "
                "выглядит глупо. НЕ проси email «продублировать». Контакт уже есть (этот чат), "
                "команда ответит ЗДЕСЬ. Ответь заново, по-человечески, общаясь прямо в этом чате."
            )
            raw_answer = await call_llm(_ch_nudge)
            if _msgr_leaks(raw_answer):
                # Всё ещё протекает — вырезаем нарушающие предложения.
                import re as _re_g
                parts = _re_g.split(r"(?<=[.!?])\s+", raw_answer)
                kept = [p for p in parts if not _msgr_leaks(p)]
                raw_answer = " ".join(kept).strip() or "Понял! Расскажите подробнее, что нужно — соберём с командой."
    except Exception as _cge:  # noqa: BLE001
        log.debug(f"channel-guard skipped: {_cge}")

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

    # 7b. Escalation triggers (Phase 9c+10ab, 2026-05-27) — run the 7 Notion §21
    #     checks and mirror any that fire into the operator topic. Rate-limited
    #     per (conversation, type) so the same alert doesn't spam every turn.
    #
    #     Phase 10a: bot-reply confidence comes from a heuristic over hedging
    #     phrases in the just-produced answer (no LLM call). Fires the
    #     low_confidence trigger when the bot is clearly unsure.
    #     Phase 10b: budget is regex-extracted from the lead's message and
    #     stashed on Customer.profile_data so operators see the parsed
    #     figure in HubSpot. Fires the large_deal_above_threshold trigger
    #     when above tenant.discount.auto_above_budget_threshold.
    if settings.crm_enabled and not is_comment_mode:
        try:
            from services.escalation import run_escalation_checks, format_alert_text
            from services.signal_extraction import (
                estimate_bot_confidence,
                extract_budget_rub,
            )
            lead_texts = [
                m.content for m in recent
                if (m.role.value if hasattr(m.role, "value") else str(m.role)) == "user"
            ]
            bot_texts = [
                m.content for m in recent
                if (m.role.value if hasattr(m.role, "value") else str(m.role)) == "assistant"
            ]
            confidence = estimate_bot_confidence(answer)
            budget_rub = extract_budget_rub(req.content)
            # Persist parsed budget so operators see it in HubSpot — keeps the
            # latest detected figure on the customer profile_data JSONB.
            if budget_rub is not None:
                profile = dict(customer.profile_data or {})
                profile["estimated_budget_rub"] = budget_rub
                customer.profile_data = profile
                log.info(
                    f"[{str(conversation.id)[:8]}] budget extracted: ~{budget_rub:,} RUB"
                )
            fired_triggers = run_escalation_checks(
                conversation_id=str(conversation.id),
                confidence=confidence,
                message_text=req.content,
                recent_lead_messages=lead_texts,
                recent_bot_replies=bot_texts,
                estimated_budget_rub=budget_rub,
                tenant_config=tenant.raw_config,
            )
            if fired_triggers:
                trigger_summary = ", ".join(t.type for t in fired_triggers)
                log.info(
                    f"[{str(conversation.id)[:8]}] escalation triggers fired: {trigger_summary}"
                )
                # Mirror each fired trigger into the operator topic
                if conversation.forum_topic_id and settings.telegram_operator_group_id and settings.telegram_bot_token:
                    for t in fired_triggers:
                        await send_to_topic(
                            settings.telegram_bot_token,
                            settings.telegram_operator_group_id,
                            conversation.forum_topic_id,
                            format_alert_text(t),
                        )
        except Exception as exc:  # noqa: BLE001
            log.warning(
                f"[{str(conversation.id)[:8]}] escalation checks failed (non-fatal): {exc}"
            )

    # 8. Handoff — gated on a real email (policy 2026-05-19).
    #    Email is the only mandatory contact: it's stable identity, while
    #    Telegram @username is mutable and would break our identity mapping
    #    if the user later renames it.
    #    SKIP entirely for public comments — contacts are not exchanged in
    #    public threads, and operator briefs there would be noise.
    handoff_triggered = False
    handoff_data = None  # Phase C1: kept in function scope so the CRM dispatch
                         # below can build a readable deal title + brief from it
    if not conversation.handoff_done and not is_comment_mode:
        all_recent = get_recent_messages(db, conversation.id, limit=20)
        history_dicts = _messages_to_dicts(all_recent)
        handoff_data = await check_handoff(history_dicts)
        # Email-backstop: если классификатор НЕ сказал ready, но лид уже дал
        # валидный email + внятный бриф — квалифицируем сами (T1-кейс).
        # Любой контакт годится: email / telegram @username / телефон.
        _scanned_email = _scan_lead_email(history_dicts)
        _scanned_tg = _scan_lead_telegram(history_dicts)
        _scanned_phone = _scan_lead_phone(history_dicts)
        # В мессенджере (telegram/whatsapp/...) сам КАНАЛ = контакт: лид уже
        # доступен здесь, отдельный email/telegram в тексте не нужен.
        _is_msgr = (req.channel or "website").lower() != "website"
        if (
            handoff_data is None
            and (_scanned_email or _scanned_tg or _scanned_phone or _is_msgr)
            and _has_brief(history_dicts)
        ):
            handoff_data = {
                "ready_for_handoff": True,
                "lead_email": _scanned_email,
                "lead_telegram_username": _scanned_tg,
                "lead_phone": _scanned_phone,
                "lead_name": _guess_lead_name(history_dicts, _scanned_email or _scanned_tg or ""),
                "task_summary": " | ".join(
                    c for c in _user_messages(history_dicts) if c.strip()
                )[:1000],
                "project_type": "Unknown",
            }
            log.info(
                f"[{str(conversation.id)[:8]}] contact-backstop handoff "
                f"(email={bool(_scanned_email)} tg={bool(_scanned_tg)} "
                f"phone={bool(_scanned_phone)} msgr={_is_msgr})"
            )
        if handoff_data:
            email = _extract_email_from_handoff(handoff_data) or (
                _scanned_email if _is_valid_email(_scanned_email) else ""
            )
            tg = (handoff_data.get("lead_telegram_username") or _scanned_tg or "").strip()
            phone = (handoff_data.get("lead_phone") or _scanned_phone or "").strip()
            if email or tg or phone or _is_msgr:
                # Прокидываем контакты в handoff_data — попадут в brief оператору
                # и в карточку сделки (чтобы менеджер знал, куда писать).
                if email:
                    handoff_data.setdefault("lead_email", email)
                if tg:
                    handoff_data.setdefault("lead_telegram_username", tg)
                if phone:
                    handoff_data.setdefault("lead_phone", phone)
                # Persist email (если есть) — стабильный якорь + кросс-канал мёрж.
                if email:
                    try:
                        from services.identity import update_email
                        update_email(db, customer.id, email)
                    except Exception as e:
                        log.warning(
                            f"[{str(conversation.id)[:8]}] update_email failed: {e}"
                        )
                # Persist телефон, если дан и ещё не записан.
                if phone and not (customer.phone or "").strip():
                    try:
                        customer.phone = phone[:50]
                    except Exception:  # noqa: BLE001
                        pass
                # Persist telegram @username в identity_keys — чтобы потом склеить
                # с этим же человеком, когда он напишет из своего Telegram
                # (кросс-канальный мёрж: сайт @username ↔ telegram from_user).
                if tg:
                    try:
                        _h = tg if tg.startswith("@") else "@" + tg
                        _ik = dict(customer.identity_keys or {})
                        if _ik.get("tg_handle") != _h:
                            _ik["tg_handle"] = _h
                            customer.identity_keys = _ik
                    except Exception:  # noqa: BLE001
                        pass
                # Имя в карточку/задачу.
                _ln = (handoff_data.get("lead_name") or "").strip()
                if _ln and not (customer.name or "").strip():
                    try:
                        customer.name = _ln[:200]
                    except Exception:  # noqa: BLE001
                        pass

                await send_telegram_brief(str(conversation.id), handoff_data, history_dicts)
                mark_handoff_done(db, conversation.id)
                handoff_triggered = True
            else:
                # Контакта нет вообще — ждём, пока лид даст email/telegram/телефон.
                log.info(
                    f"[{str(conversation.id)[:8]}] classifier ready but NO contact "
                    f"(email/tg/phone) — suppressed, бот попросит контакт"
                )

    # 8b. Funnel auto-transition (Phase 9b, 2026-05-27).
    #     Evaluates Notion §20 funnel state machine with this turn's signals
    #     and advances conversation.lead_stage if a valid forward transition
    #     fires. Pushes the change to CRM via dispatch_stage_change.
    #     Currently observable signals: lead_messages count → new_lead → in_dialog,
    #     handoff_classifier ready → qualified, interaction_type=HardStop → lost.
    #     Later stages (NDA, on_call, proposal, prepayment, ...) need operator
    #     action — they remain operator-set via HubSpot UI for now.
    if settings.crm_enabled and not is_comment_mode:
        try:
            from services.funnel import (
                decide_from_tenant_config as _funnel_decide,
                can_auto_transition,
            )
            lead_msg_count = sum(
                1 for m in recent
                if (m.role.value if hasattr(m.role, "value") else str(m.role)) == "user"
            )
            hard_stop_signal = (
                signal_update is not None
                and signal_update.interaction_type == "HardStop"
                and signal_update.is_first_touch
            )
            current_stage = conversation.lead_stage or "new_lead"
            decision = _funnel_decide(
                current_stage=current_stage,
                tenant_config=tenant.raw_config,
                lead_messages_so_far=lead_msg_count,
                classifier_says_ready=handoff_triggered,
                hard_stop_signal=hard_stop_signal,
            )
            if (
                decision.should_transition
                and decision.target_stage
                and can_auto_transition(current_stage, decision.target_stage)
            ):
                new_stage = decision.target_stage
                conversation.lead_stage = new_stage
                if new_stage == "lost":
                    conversation.lost_reason = decision.lost_reason
                log.info(
                    f"[{str(conversation.id)[:8]}] funnel: {current_stage} → {new_stage} "
                    f"({decision.reason})"
                )
                # Push to CRM. dispatch_stage_change enqueues 'pending' if
                # crm_deal_id isn't resolved yet — worker lazy-resolves from DB.
                from services.crm_dispatch import dispatch_stage_change
                dispatch_stage_change(
                    customer_id=str(customer.id),
                    crm_deal_id=conversation.crm_deal_id,
                    new_stage=new_stage,
                    lost_reason=decision.lost_reason,
                    conversation_id=str(conversation.id),
                )
        except Exception as exc:  # noqa: BLE001
            log.warning(
                f"[{str(conversation.id)[:8]}] funnel transition failed (non-fatal): {exc}"
            )

    # 9. Commit the whole turn atomically
    db.commit()

    # 10. CRM sync (Phase 8a, 2026-05-26) — enqueue events to the worker.
    #     This is a no-op when settings.crm_enabled=False (default during
    #     rollout). When enabled, the worker drains the queue async and
    #     handles upsert_contact / create_deal / log_message / stage updates.
    #     Wrapped in a broad try so a CRM hiccup never breaks the lead-facing
    #     flow — bot's own Postgres remains the source of truth.
    if settings.crm_enabled:
        try:
            # Re-fetch with identities relationship for tg_handle lookup —
            # the customer object above may be a fresh insert without
            # identities loaded yet.
            from services.crm_dispatch import dispatch_on_message_turn
            # Phase 12 (2026-05-28): pass lead_messages_count for lazy deal
            # creation threshold — deal only fires when handoff OR score>=50
            # OR this count >= 3, not on every first "hi" from a visitor.
            lead_msg_count_for_crm = sum(
                1 for m in recent
                if (m.role.value if hasattr(m.role, "value") else str(m.role)) == "user"
            )
            # Phase C1.2: прокидываем источник трафика (widget → extra_meta)
            # в handoff_data, чтобы он попал в карточку сделки.
            if handoff_data is not None and isinstance(req.extra_meta, dict):
                _ts = req.extra_meta.get("traffic_source")
                if _ts:
                    handoff_data["traffic_source"] = _ts
            dispatch_on_message_turn(
                customer=customer,
                conversation=conversation,
                last_lead_message=req.content,
                last_bot_reply=answer,
                handoff_just_fired=handoff_triggered,
                channel=req.channel,
                lead_messages_count=lead_msg_count_for_crm,
                project_type=None,  # not currently extracted from the conversation
                handoff_data=handoff_data,  # Phase C1: readable deal title + brief
            )
        except Exception as exc:  # noqa: BLE001
            log.warning(f"[{str(conversation.id)[:8]}] crm dispatch failed: {exc}")

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
    """Public website chat entry point.

    Only the `website` channel is accepted here. Social channels (telegram,
    instagram, messenger) MUST arrive through their signed webhook handlers,
    which call _handle_message directly — never via this anonymous HTTP route.
    Without this guard an unauthenticated caller could spoof channel=telegram
    with a victim's chat_id and trigger outbound Telegram actions to that
    victim, or merge messages into the victim's customer record.
    """
    if req.channel != "website":
        raise HTTPException(
            status_code=403,
            detail="This endpoint accepts the 'website' channel only; "
                   "social channels are handled via their webhook endpoints.",
        )
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
        extra_meta=({"traffic_source": req.source} if req.source else None),
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
    # --- Authenticate the caller (fail-closed) -----------------------------
    # Telegram echoes our registered secret in the X-Telegram-Bot-Api-Secret-
    # Token header on every update. Unlike the Meta handlers (which fail OPEN
    # when META_APP_SECRET is unset), this path fails CLOSED: an unset secret
    # refuses all traffic, because a forged update here can impersonate an
    # operator and push attacker-controlled text to real leads through the
    # bot's own outbound tokens.
    expected_secret = settings.telegram_webhook_secret
    if not expected_secret:
        log.error(
            "telegram_webhook: TELEGRAM_WEBHOOK_SECRET not set — refusing all "
            "updates (fail-closed). Set it in env AND re-register the webhook "
            "with secret_token (see set_telegram_webhook)."
        )
        # 503 (not 401): this is OUR misconfiguration. Telegram keeps the
        # update queued and retries, so leads' messages aren't lost once the
        # secret is configured.
        return Response(status_code=503)
    got_secret = request.headers.get("x-telegram-bot-api-secret-token", "")
    if not hmac.compare_digest(expected_secret.encode("utf-8"), got_secret.encode("utf-8")):
        log.warning(
            "telegram_webhook: secret-token mismatch — rejected an "
            "unauthenticated/forged update"
        )
        return Response(status_code=401)

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
    token = auth.split(None, 1)[1].strip()
    if not hmac.compare_digest(token.encode("utf-8"), expected.encode("utf-8")):
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
# /admin/training/* — operator correction loop (see services/training.py)
# ============================================================================
# Unlike /metrics, training endpoints REFUSE to run if the auth token isn't
# configured (return 503). /metrics defaults to open in dev for convenience;
# training endpoints write into the prompt-influencing table, so the default
# is fail-closed — accidental open exposure could let a curious caller poison
# every future bot response.

def _verify_training_token(request: Request) -> None:
    """FastAPI dependency enforcing bearer-token auth on every /admin/training
    endpoint. Fails closed: if TRAINING_AUTH_TOKEN isn't set in env, the whole
    feature is disabled (503), not silently open like /metrics."""
    expected = settings.training_auth_token
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="Training endpoints disabled — set TRAINING_AUTH_TOKEN in env.",
        )
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Bearer token required")
    token = auth.split(None, 1)[1].strip()
    if not hmac.compare_digest(token.encode("utf-8"), expected.encode("utf-8")):
        raise HTTPException(status_code=403, detail="Invalid token")


# ---- Task Engine B2 debug endpoints (под тем же admin-токеном) ----

@app.post("/admin/cron/sweep")
async def admin_cron_sweep(_: None = Depends(_verify_training_token)):
    """Force-run крон-свипа + само-исполнения отложенных действий (для теста,
    чтобы не ждать часовой интервал крона)."""
    from services.cron import sweep_once
    from services.scheduled_actions import run_due_followups, run_due_call_reminders
    try:
        sweep_stats = await sweep_once(tenant_config={})
    except Exception as e:  # noqa: BLE001
        sweep_stats = {"error": str(e)}
    fu_stats = await run_due_followups(tenant_config=None)
    call_stats = await run_due_call_reminders(tenant_config=None)
    return {"sweep": sweep_stats, "followups": fu_stats, "call_reminders": call_stats}


@app.get("/admin/scheduled-actions")
async def admin_scheduled_actions(
    status: str = "pending",
    db: Session = Depends(get_db),
    _: None = Depends(_verify_training_token),
):
    """Список отложенных действий бота по статусу (pending/done/failed)."""
    from db.models import ScheduledAction
    rows = (
        db.query(ScheduledAction)
        .filter(ScheduledAction.status == status)
        .order_by(ScheduledAction.due_at.asc())
        .limit(50)
        .all()
    )
    return {
        "count": len(rows),
        "items": [
            {
                "id": str(r.id), "action_type": r.action_type, "executor": r.executor,
                "status": r.status,
                "due_at": r.due_at.isoformat() if r.due_at else None,
                "channel": r.channel, "chat_id": r.chat_id, "attempts": r.attempts,
            }
            for r in rows
        ],
    }


@app.post("/admin/training/draft", response_model=TrainingProposalResponse)
async def training_draft(
    req: TrainingDraftRequest,
    db: Session = Depends(get_db),
    _: None = Depends(_verify_training_token),
):
    """Start a new correction session. Returns the trainer LLM's first
    proposal + a session_id to use in /refine and /approve.

    Phase 11: also surfaces similar_existing_rules so the operator sees
    nearby active rules BEFORE iterating/approving."""
    from services.training import draft_correction, find_similar_active_corrections
    try:
        source_conv_id = PyUUID(req.source_conversation_id) if req.source_conversation_id else None
    except (ValueError, TypeError):
        raise HTTPException(status_code=422, detail="source_conversation_id must be a valid UUID")

    try:
        session_id, proposal = await draft_correction(
            trainer_llm,
            dialog=req.dialog,
            correction_note=req.correction_note,
            channel=req.channel,
            source_conversation_id=source_conv_id,
        )
    except ValueError as e:
        # Trainer LLM returned invalid JSON — usually transient
        raise HTTPException(status_code=502, detail=f"trainer returned invalid JSON: {e}")

    # Phase 11: warn operator about semantically similar active rules
    # (cosine < 0.4 — strict, to keep false positives down). Non-fatal —
    # if pgvector fails we just return empty similar_existing_rules.
    similar: list[dict] = []
    try:
        similar = find_similar_active_corrections(
            db, trigger_context=req.dialog, k=3, max_distance=0.4, channel=req.channel,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning(f"find_similar_active_corrections failed (non-fatal): {exc}")

    return TrainingProposalResponse(
        session_id=str(session_id),
        proposed_rule=proposal.get("proposed_rule", ""),
        proposed_response=proposal.get("proposed_response"),
        confirmation_question=proposal.get("confirmation_question", "Подходит ли вариант?"),
        similar_existing_rules=similar,
    )


@app.post("/admin/training/refine", response_model=TrainingProposalResponse)
async def training_refine(
    req: TrainingRefineRequest,
    _: None = Depends(_verify_training_token),
):
    """Iterate on the previous proposal — operator says what they want
    changed; trainer LLM produces a new variant."""
    from services.training import refine_correction
    try:
        sid = PyUUID(req.session_id)
    except (ValueError, TypeError):
        raise HTTPException(status_code=422, detail="session_id must be a valid UUID")
    try:
        proposal = await refine_correction(trainer_llm, sid, req.operator_feedback)
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found or expired (TTL 15 min)")
    except ValueError as e:
        raise HTTPException(status_code=502, detail=f"trainer returned invalid JSON: {e}")
    return TrainingProposalResponse(
        session_id=req.session_id,
        proposed_rule=proposal.get("proposed_rule", ""),
        proposed_response=proposal.get("proposed_response"),
        confirmation_question=proposal.get("confirmation_question", "Подходит ли вариант?"),
    )


@app.post("/admin/training/approve")
async def training_approve(
    req: TrainingApproveRequest,
    db: Session = Depends(get_db),
    _: None = Depends(_verify_training_token),
):
    """Persist the latest proposal to training_corrections — future bot
    responses will retrieve this rule via similarity search when a
    semantically similar lead message comes in.

    Phase 11 (2026-05-27): unless the request carries force_action, we
    run a conflict check first — embed similarity over active rules,
    then LLM-as-judge per candidate. If ANY judge says is_conflict=true
    we return 409 with the conflicting rules so the operator can choose:
      - retry with force_action="supersede" → deactivate old rules + save new
      - retry with force_action="coexist"   → save new alongside existing
      - call /refine to write a merged guidance, then approve normally
    """
    from services.training import (
        approve_correction,
        find_similar_active_corrections,
        get_session,
        llm_judge_conflict,
        supersede_correction,
    )
    try:
        sid = PyUUID(req.session_id)
    except (ValueError, TypeError):
        raise HTTPException(status_code=422, detail="session_id must be a valid UUID")

    sess = get_session(sid)
    if sess is None:
        raise HTTPException(status_code=404, detail="Session not found or expired")
    if not sess.last_proposed_rule:
        raise HTTPException(status_code=400, detail="No proposal in session — call /draft first")

    # ---- Phase 11 conflict detection (skipped if operator overrode) ----
    if not req.force_action:
        try:
            similar = find_similar_active_corrections(
                db, trigger_context=sess.dialog, k=3, max_distance=0.4, channel=sess.channel,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning(f"find_similar_active failed (non-fatal, allowing approve): {exc}")
            similar = []

        conflicts_found: list[dict] = []
        for s in similar:
            try:
                verdict = await llm_judge_conflict(
                    trainer_llm, sess.last_proposed_rule, s["guidance"],
                )
            except Exception as exc:  # noqa: BLE001
                log.warning(f"llm_judge_conflict raised, treating as no conflict: {exc}")
                continue
            if verdict.get("is_conflict"):
                conflicts_found.append({
                    **s,
                    "judge_reason": verdict.get("reason"),
                    "suggested_action": verdict.get("suggested_action", "supersede"),
                })

        if conflicts_found:
            log.info(
                f"training: blocking approve of session {str(sid)[:8]} — "
                f"{len(conflicts_found)} conflict(s) detected"
            )
            raise HTTPException(
                status_code=409,
                detail={
                    "blocked_on": "conflict",
                    "message": (
                        f"Похожих активных правил: {len(conflicts_found)}. "
                        "Выберите действие: supersede (заменить старые), "
                        "coexist (оставить оба) или вернитесь к refine и объедините вручную."
                    ),
                    "conflicts": conflicts_found,
                },
            )

    # ---- Either no conflict, or operator overrode with force_action ----
    try:
        row = approve_correction(db, sid, created_by=req.created_by)
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found or expired")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Supersede applies AFTER persist so old.superseded_by_id can reference new.id
    superseded_ids: list[str] = []
    if req.force_action == "supersede":
        try:
            similar2 = find_similar_active_corrections(
                db, trigger_context=sess.dialog, k=3, max_distance=0.4, channel=sess.channel,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning(f"find_similar_active failed during supersede: {exc}")
            similar2 = []
        for s in similar2:
            if s["id"] == str(row.id):
                continue  # don't supersede ourselves
            try:
                supersede_correction(db, PyUUID(s["id"]), row.id)
                superseded_ids.append(s["id"])
            except Exception as exc:  # noqa: BLE001
                log.warning(f"supersede_correction failed for {s['id']}: {exc}")

    db.commit()
    msg = "Сохранил правило. Применю на следующих похожих ответах."
    if superseded_ids:
        msg += f" Деактивировал {len(superseded_ids)} старых правил."
    elif req.force_action == "coexist":
        msg += " Решено сосуществовать с похожими правилами."
    return {
        "ok": True,
        "correction_id": str(row.id),
        "superseded": superseded_ids,
        "force_action": req.force_action,
        "message": msg,
    }


@app.post("/admin/training/discard")
async def training_discard(
    req: TrainingDiscardRequest,
    _: None = Depends(_verify_training_token),
):
    """Operator abandons the correction without saving."""
    from services.training import discard_session
    try:
        sid = PyUUID(req.session_id)
    except (ValueError, TypeError):
        raise HTTPException(status_code=422, detail="session_id must be a valid UUID")
    removed = discard_session(sid)
    return {"ok": True, "removed": removed}


@app.get("/admin/training/list")
async def training_list(
    _: None = Depends(_verify_training_token),
    db: Session = Depends(get_db),
    limit: int = 50,
    include_inactive: bool = False,
):
    """List the most recent training_corrections — for the operator UI to
    show what rules currently shape bot behavior."""
    from sqlalchemy import select as sql_select
    from db.models import TrainingCorrection as TC
    stmt = sql_select(TC).order_by(TC.created_at.desc()).limit(min(limit, 200))
    if not include_inactive:
        stmt = stmt.where(TC.is_active == True)  # noqa: E712
    rows = db.execute(stmt).scalars().all()
    return {
        "rules": [
            {
                "id": str(r.id),
                "trigger_context": r.trigger_context[:300],
                "guidance": r.correct_guidance,
                "suggested_response": r.suggested_response,
                "channel": r.channel,
                "is_active": r.is_active,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "created_by": r.created_by,
            }
            for r in rows
        ],
        "count": len(rows),
    }


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
    log.info(f"Tenant:   {tenant.slug} ({tenant.display_name})")
    # CRM health-check is cheap on NoOp (always True) and a single API call
    # on real adapters. Failure here logs a warning but doesn't abort startup —
    # the bot must keep serving leads even if CRM is unreachable.
    try:
        crm_ok = await crm_adapter.health_check()
        log.info(f"CRM:      {crm_adapter.provider_name} (enabled={settings.crm_enabled}, healthy={crm_ok})")
    except Exception as exc:  # noqa: BLE001
        log.warning(f"CRM:      {crm_adapter.provider_name} health-check failed: {exc}")
    # Start the CRM event queue worker only when CRM is enabled — keeps the
    # bot's hot path zero-cost when CRM is off. Worker is a single asyncio
    # task that drains the queue forever; on shutdown we join it.
    if settings.crm_enabled:
        await crm_queue.start_worker(crm_adapter)
        log.info(f"CRM queue: worker running")
        # Phase 9d (2026-05-27) — periodic warming + temperature/score decay.
        # Cron sweeps silent customers every 1h by default. Started here so
        # both workers come up together; stopped in shutdown handler below.
        from services import cron as crm_cron
        await crm_cron.start_cron_worker(tenant_config=tenant.raw_config)
        log.info(f"CRM cron:  worker running")
    else:
        log.info(f"CRM queue: not started (crm_enabled=False)")
        log.info(f"CRM cron:  not started (crm_enabled=False)")
    log.info(f"Origins:  {settings.allowed_origins}")
    log.info("=" * 60)


@app.on_event("shutdown")
async def shutdown():
    """Drain the CRM queue + stop the cron worker before the container dies."""
    if crm_queue.is_running():
        log.info("Shutting down — draining CRM queue...")
        await crm_queue.stop_worker(timeout=5.0)
        log.info("CRM queue drained")
    from services import cron as crm_cron
    if crm_cron.is_running():
        log.info("Shutting down — stopping CRM cron worker...")
        await crm_cron.stop_cron_worker(timeout=5.0)
        log.info("CRM cron stopped")
