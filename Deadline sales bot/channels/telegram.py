"""Telegram channel adapter.

Now supports:
  * parse_telegram_webhook(payload)        — Update → NormalizedMessage
                                              (text + voice messages)
  * send_telegram_reply(token, ...)         — sendMessage HTTP back to user
  * download_voice(token, file_id)          — Bot API getFile + binary fetch
  * transcribe_voice(audio, groq_key, ...)  — Groq Whisper-large-v3
  * Forum-supergroup helpers (Phase B):
      create_forum_topic, send_to_topic, answer_callback_query

Webhook expected shape (Bot API ≥ 7.x):
{
  "update_id": 123,
  "message": {
    "message_id": 42,
    "from":  {"id": 555, "username": "ivan", ...},
    "chat":  {"id": 555, "type": "private", ...},
    "date":  1700000000,
    "text":  "Привет",
    "voice": {"file_id": "AwAC...", "duration": 5, "mime_type": "audio/ogg", ...}
  }
}

We accept message.text directly. For message.voice we download + transcribe
synchronously inside the parser — the caller (`/webhooks/telegram`) sees
a NormalizedMessage with the transcript as `content`. Edited messages,
channel posts, stickers, photos — still ignored.
"""

from __future__ import annotations

import logging
from typing import Optional

import httpx
from pydantic import BaseModel


log = logging.getLogger(__name__)

TELEGRAM_API_BASE = "https://api.telegram.org"
GROQ_TRANSCRIBE_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
GROQ_WHISPER_MODEL = "whisper-large-v3"


class NormalizedMessage(BaseModel):
    """Channel-agnostic shape consumed by the /message endpoint.

    For voice messages, `content` is the transcript and `extra_meta` carries
    {"source": "voice", "duration_sec": N, "transcribed_by": "groq-whisper-v3"}
    so downstream code (LLM prompt, ops log) can know it wasn't typed text.
    """
    channel: str = "telegram"
    external_id: str               # Telegram user.id (string for safety)
    content: str                   # raw text or voice transcript
    username: Optional[str] = None  # "@ivan" if user has one
    channel_conversation_id: str   # Telegram chat.id (= user.id for private chats)
    message_type: str = "dm"       # "dm" | "comment" (Telegram is always dm)
    extra_meta: Optional[dict] = None


# ============================================================================
# Voice — download + transcribe (Groq Whisper)
# ============================================================================

async def download_voice(token: str, file_id: str) -> Optional[bytes]:
    """Fetch the voice file binary from Telegram.

    Two-step:
      1. POST getFile?file_id=... → {"result": {"file_path": "voice/file_X.oga"}}
      2. GET https://api.telegram.org/file/bot<TOKEN>/<file_path> → binary
    """
    if not token or not file_id:
        return None

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(
                f"{TELEGRAM_API_BASE}/bot{token}/getFile",
                json={"file_id": file_id},
            )
            if r.status_code != 200:
                log.warning(f"telegram getFile {r.status_code}: {r.text[:200]}")
                return None
            data = r.json()
            file_path = data.get("result", {}).get("file_path")
            if not file_path:
                log.warning(f"telegram getFile: no file_path in response — {data!r}")
                return None

            file_url = f"{TELEGRAM_API_BASE}/file/bot{token}/{file_path}"
            r2 = await client.get(file_url)
            if r2.status_code != 200:
                log.warning(f"telegram file download {r2.status_code}: {r2.text[:200]}")
                return None
            return r2.content
    except Exception as e:
        log.error(f"telegram download_voice exception: {e}")
        return None


async def transcribe_voice(
    audio: bytes,
    groq_api_key: str,
    language: Optional[str] = None,
) -> Optional[str]:
    """Send `.ogg` audio bytes to Groq Whisper, get text transcript.

    `language` is optional ISO-639-1 ("ru", "en"). When unset, Whisper auto-
    detects — works fine for RU/EN mixed input. Pass a hint only if you know.

    Returns the transcript string, or None on any failure (caller logs).
    """
    if not groq_api_key or not audio:
        return None

    files = {
        "file": ("voice.ogg", audio, "audio/ogg"),
    }
    data = {
        "model": GROQ_WHISPER_MODEL,
        "response_format": "text",
        # temperature=0 deterministic, fewer wild guesses
        "temperature": "0",
    }
    if language:
        data["language"] = language

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                GROQ_TRANSCRIBE_URL,
                headers={"Authorization": f"Bearer {groq_api_key}"},
                files=files,
                data=data,
            )
        # Memory hygiene: drop refs to the multipart payload immediately —
        # for long voice (200s+) the audio buffer can be 3-5 MB. We don't
        # want it lingering until the next natural GC cycle while the
        # subsequent RAG/LLM pipeline allocates more memory.
        del files, data
        if r.status_code != 200:
            log.warning(f"groq transcribe {r.status_code}: {r.text[:200]}")
            return None
        # response_format=text → body is plain text, not JSON
        transcript = r.text.strip()
        if not transcript:
            return None
        return transcript
    except Exception as e:
        log.error(f"groq transcribe exception: {e}")
        return None


# ============================================================================
# Webhook parser
# ============================================================================

async def parse_telegram_webhook(
    payload: dict,
    bot_token: Optional[str] = None,
    groq_api_key: Optional[str] = None,
) -> Optional[NormalizedMessage]:
    """Convert a Telegram Update payload into NormalizedMessage. Return None
    if the update is not a message we can handle.

    Text messages — return immediately.
    Voice messages — download + transcribe via Groq Whisper, return transcript
    as `content` with `extra_meta.source = "voice"`. If transcription fails,
    return a placeholder so the bot can apologise rather than ghost the user.
    """
    if not isinstance(payload, dict):
        return None

    msg = payload.get("message")
    if not isinstance(msg, dict):
        return None  # edited_message / channel_post / callback_query — skip

    from_user = msg.get("from") or {}
    chat = msg.get("chat") or {}
    user_id = from_user.get("id")
    chat_id = chat.get("id")
    if user_id is None or chat_id is None:
        log.warning(f"telegram: payload missing from.id or chat.id — {payload!r}")
        return None

    username_raw = from_user.get("username")
    username = f"@{username_raw}" if username_raw else None

    # ---- text path ----
    text = (msg.get("text") or "").strip()
    if text:
        return NormalizedMessage(
            external_id=str(user_id),
            content=text,
            username=username,
            channel_conversation_id=str(chat_id),
        )

    # ---- voice path ----
    voice = msg.get("voice")
    if isinstance(voice, dict) and voice.get("file_id"):
        file_id = voice["file_id"]
        duration = voice.get("duration", 0)

        if not bot_token or not groq_api_key:
            log.info(
                f"telegram voice ignored: bot_token={bool(bot_token)} "
                f"groq_api_key={bool(groq_api_key)} (set GROQ_API_KEY to enable)"
            )
            return NormalizedMessage(
                external_id=str(user_id),
                content="[голосовое — транскрипция не настроена, напишите текстом]",
                username=username,
                channel_conversation_id=str(chat_id),
                extra_meta={"source": "voice", "duration_sec": duration,
                            "transcription_failed": "stt_not_configured"},
            )

        audio = await download_voice(bot_token, file_id)
        if audio is None:
            return NormalizedMessage(
                external_id=str(user_id),
                content="[не получилось скачать голосовое, повторите текстом пожалуйста]",
                username=username,
                channel_conversation_id=str(chat_id),
                extra_meta={"source": "voice", "duration_sec": duration,
                            "transcription_failed": "download"},
            )

        transcript = await transcribe_voice(audio, groq_api_key)
        if not transcript:
            return NormalizedMessage(
                external_id=str(user_id),
                content="[не разобрал голосовое, повторите текстом пожалуйста]",
                username=username,
                channel_conversation_id=str(chat_id),
                extra_meta={"source": "voice", "duration_sec": duration,
                            "transcription_failed": "stt_empty"},
            )

        log.info(f"telegram voice transcribed ({duration}s → {len(transcript)} chars)")
        return NormalizedMessage(
            external_id=str(user_id),
            content=transcript,
            username=username,
            channel_conversation_id=str(chat_id),
            extra_meta={"source": "voice", "duration_sec": duration,
                        "transcribed_by": "groq-whisper-v3"},
        )

    # No text, no voice — sticker / photo / video / etc. Skip.
    return None


async def send_telegram_reply(token: str, chat_id: str, text: str) -> bool:
    """Send `text` to `chat_id` via Telegram Bot API. Returns True on success.

    Truncates to 4000 chars (Telegram hard limit is 4096, leave headroom).
    Silently no-ops on failure (logs warning) — we don't want webhook to
    return non-200 because Telegram would retry the same update repeatedly.
    """
    if not token:
        log.error("telegram: TELEGRAM_BOT_TOKEN not set — cannot reply")
        return False
    if not chat_id or not text:
        return False

    text = text[:4000]

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                f"{TELEGRAM_API_BASE}/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
            )
        if r.status_code != 200:
            log.warning(f"telegram sendMessage {r.status_code}: {r.text[:200]}")
            return False
        return True
    except Exception as e:
        log.error(f"telegram sendMessage exception: {e}")
        return False


# Map of attachment-type → Bot API method. Used by forward_attachment to
# re-send a file via its file_id (Telegram caches uploads — no re-upload needed
# when forwarding between chats handled by the same bot).
_ATTACHMENT_METHODS = {
    "voice": "sendVoice",
    "photo": "sendPhoto",
    "document": "sendDocument",
    "video": "sendVideo",
    "audio": "sendAudio",
    "animation": "sendAnimation",
    "sticker": "sendSticker",
    "video_note": "sendVideoNote",
}

# Map of attachment-type → name of the file-id field in the API request body.
# Most endpoints use the attachment type as the field name (sendPhoto wants
# `photo`, sendVoice wants `voice`). All match except none right now —
# kept as a separate dict in case Telegram adds a quirky endpoint later.
_ATTACHMENT_FIELDS = {
    "voice": "voice",
    "photo": "photo",
    "document": "document",
    "video": "video",
    "audio": "audio",
    "animation": "animation",
    "sticker": "sticker",
    "video_note": "video_note",
}


async def forward_attachment(
    token: str,
    chat_id: str,
    attachment_type: str,
    file_id: str,
    caption: Optional[str] = None,
) -> bool:
    """Forward a media attachment to a Telegram chat by its file_id.

    Telegram caches every uploaded file under a permanent file_id. As long as
    the same bot owns the file (received it via webhook or sent it), it can
    re-send it to any chat by reusing the file_id — no download/upload cycle
    needed. We use this for operator → lead forwarding: when an operator
    sends voice/photo/document in the operator topic, we grab the file_id
    from the webhook payload and call sendVoice/sendPhoto/sendDocument
    against the lead's chat.

    Returns True on success.

    `attachment_type` must be one of the keys in _ATTACHMENT_METHODS.
    `caption` is optional text that displays under the media (≤1024 chars).
    Stickers and video_notes do not accept captions — caption is ignored.
    """
    method = _ATTACHMENT_METHODS.get(attachment_type)
    field = _ATTACHMENT_FIELDS.get(attachment_type)
    if not method or not field:
        log.warning(f"forward_attachment: unsupported type '{attachment_type}'")
        return False
    if not token or not chat_id or not file_id:
        return False

    payload: dict = {"chat_id": chat_id, field: file_id}
    # Stickers and video_notes can't have captions per Bot API docs
    if caption and attachment_type not in ("sticker", "video_note"):
        payload["caption"] = caption[:1024]

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(
                f"{TELEGRAM_API_BASE}/bot{token}/{method}",
                json=payload,
            )
        if r.status_code != 200:
            log.warning(f"forward_attachment {method} {r.status_code}: {r.text[:200]}")
            return False
        return True
    except Exception as e:
        log.error(f"forward_attachment {method} exception: {e}")
        return False


def extract_attachment(msg: dict) -> Optional[tuple[str, str, Optional[str]]]:
    """Inspect a Telegram message payload for a media attachment.

    Returns a tuple (attachment_type, file_id, caption) if found, else None.
    Photos arrive as an array of sizes — we pick the LAST entry which is
    always the highest-resolution variant.
    """
    if not isinstance(msg, dict):
        return None
    caption = (msg.get("caption") or "").strip() or None

    # Photo arrives as array — pick the largest (last) variant
    if photos := msg.get("photo"):
        if isinstance(photos, list) and photos:
            largest = photos[-1]
            file_id = largest.get("file_id") if isinstance(largest, dict) else None
            if file_id:
                return ("photo", file_id, caption)

    # All other types: single object with file_id
    for key in ("voice", "document", "video", "audio", "animation", "sticker", "video_note"):
        obj = msg.get(key)
        if isinstance(obj, dict) and obj.get("file_id"):
            return (key, obj["file_id"], caption)

    return None


async def send_typing_action(token: str, chat_id: str) -> None:
    """Show "печатает..." indicator in the lead's chat for ~5 seconds.

    Cheap fire-and-forget — we don't await the result strictly and we don't
    fail the request if it errors. Telegram's typing indicator vanishes after
    5s on its own, so if the LLM takes longer we'd need to re-trigger; for
    MVP one call is enough because most replies fit under 5s.
    """
    if not token or not chat_id:
        return
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            await client.post(
                f"{TELEGRAM_API_BASE}/bot{token}/sendChatAction",
                json={"chat_id": chat_id, "action": "typing"},
            )
    except Exception as e:
        # Non-fatal — typing is purely cosmetic
        log.debug(f"sendChatAction failed (non-fatal): {e}")


async def set_telegram_webhook(token: str, url: str) -> dict:
    """Configure Telegram to POST every update to `url`. Idempotent. Returns
    the Telegram response dict. Call from a script after deploy, not on every
    container start (Bot API rate-limits setWebhook).

    `allowed_updates` includes `message` and `callback_query` because operators
    interact with the bot via inline buttons in the forum-supergroup.
    """
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(
            f"{TELEGRAM_API_BASE}/bot{token}/setWebhook",
            json={
                "url": url,
                "allowed_updates": ["message", "callback_query"],
            },
        )
    return r.json()


# ============================================================================
# Forum-supergroup helpers (Phase B — operator visibility)
#
# Telegram forum mode lets a single supergroup host hundreds of "topics" —
# sub-channels each with their own thread. We use one topic per lead so
# operators see every conversation as a separate chat inside one TG group.
#
# Bot must be added to the supergroup as ADMIN with `manage_topics` right,
# otherwise createForumTopic returns 400 BAD_REQUEST.
# ============================================================================


def build_forum_topic_name(db, customer, conversation, *, lead_name: str, channel: str) -> str:
    """Compose forum-topic title with optional returning-lead prefix +
    topic summary tag.

    - [ПОВТОРНЫЙ] prefix if customer has ANY archived prior conversation
    - ' · <first 40 chars of conversation.summary>' tail if summary is set

    Both are optional — for first-time leads with no archived priors and no
    summary yet, the name is just '{lead_name} · {channel}'.
    """
    from sqlalchemy import select, func
    from db.models import Conversation, ConversationStatusEnum

    prefix = ""
    # CRITICAL FIX (P13.T15): original filter was ARCHIVED-only, but the most
    # common returning-lead case is HANDED_OFF (handoff fired when email was
    # captured). Widened to all four "completed" statuses so [ПОВТОРНЫЙ] prefix
    # fires for any lead that has previously engaged and closed a conversation.
    has_prior = db.execute(
        select(func.count(Conversation.id)).where(
            Conversation.customer_id == customer.id,
            Conversation.status.in_([
                ConversationStatusEnum.HANDED_OFF.value,
                ConversationStatusEnum.RESOLVED.value,
                ConversationStatusEnum.ABANDONED.value,
                ConversationStatusEnum.ARCHIVED.value,
            ]),
        )
    ).scalar_one() > 0
    if has_prior:
        prefix = "[ПОВТОРНЫЙ] "

    summary_tag = ""
    if conversation.summary:
        summary_tag = " · " + conversation.summary[:40]

    return f"{prefix}{lead_name} · {channel}{summary_tag}"


async def create_forum_topic(
    token: str,
    supergroup_id: str,
    name: str,
    icon_color: Optional[int] = None,
) -> Optional[int]:
    """Create a new forum topic in `supergroup_id`. Returns message_thread_id.

    `icon_color` is one of the seven Telegram-allowed colors:
    7322096 (red), 16766590 (orange), 13338331 (yellow), 9367192 (green),
    16749490 (cyan), 16478047 (blue), 14638336 (purple). We rotate by
    channel for visual scanability.
    """
    if not token or not supergroup_id or not name:
        return None
    payload = {"chat_id": supergroup_id, "name": name[:128]}
    if icon_color is not None:
        payload["icon_color"] = icon_color
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                f"{TELEGRAM_API_BASE}/bot{token}/createForumTopic",
                json=payload,
            )
        if r.status_code != 200:
            log.warning(f"createForumTopic {r.status_code}: {r.text[:200]}")
            return None
        data = r.json()
        if not data.get("ok"):
            log.warning(f"createForumTopic non-ok: {data}")
            return None
        return data.get("result", {}).get("message_thread_id")
    except Exception as e:
        log.error(f"createForumTopic exception: {e}")
        return None


async def send_to_topic(
    token: str,
    supergroup_id: str,
    message_thread_id: int,
    text: str,
    reply_markup: Optional[dict] = None,
) -> bool:
    """sendMessage into a specific forum topic. Returns True on success.

    `reply_markup` accepts standard Bot API inline keyboard format:
        {"inline_keyboard": [[{"text":"...", "callback_data":"..."}]]}
    """
    if not token or not supergroup_id or message_thread_id is None or not text:
        return False
    payload = {
        "chat_id": supergroup_id,
        "message_thread_id": message_thread_id,
        "text": text[:4000],
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                f"{TELEGRAM_API_BASE}/bot{token}/sendMessage",
                json=payload,
            )
        if r.status_code != 200:
            log.warning(f"send_to_topic {r.status_code}: {r.text[:200]}")
            return False
        return True
    except Exception as e:
        log.error(f"send_to_topic exception: {e}")
        return False


async def close_forum_topic(
    token: str,
    supergroup_id: str,
    message_thread_id: int,
) -> bool:
    """Close a forum topic so members cannot send messages into it.

    Telegram UI hides the message-input field for non-admin members of a
    closed topic, which is exactly the UX we want for the operator inbox:
    while operator_takeover is OFF, the bot is the only active speaker —
    operators read but can't accidentally type into the lead's conversation.

    Bot itself (and supergroup admins with can_manage_topics) can still post
    into a closed topic via the API, so mirroring of LEAD/BOT messages keeps
    working. Idempotent: calling close on an already-closed topic returns OK.
    """
    if not token or not supergroup_id or message_thread_id is None:
        return False
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                f"{TELEGRAM_API_BASE}/bot{token}/closeForumTopic",
                json={"chat_id": supergroup_id, "message_thread_id": message_thread_id},
            )
        if r.status_code != 200:
            log.warning(f"close_forum_topic {r.status_code}: {r.text[:200]}")
            return False
        return True
    except Exception as e:
        log.error(f"close_forum_topic exception: {e}")
        return False


async def reopen_forum_topic(
    token: str,
    supergroup_id: str,
    message_thread_id: int,
) -> bool:
    """Re-open a previously closed forum topic. Restores the message-input
    field for all members. Used when an operator presses "Возьму на себя"
    so they can start typing replies that will be forwarded to the lead.
    Idempotent: calling reopen on an already-open topic returns OK.
    """
    if not token or not supergroup_id or message_thread_id is None:
        return False
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                f"{TELEGRAM_API_BASE}/bot{token}/reopenForumTopic",
                json={"chat_id": supergroup_id, "message_thread_id": message_thread_id},
            )
        if r.status_code != 200:
            log.warning(f"reopen_forum_topic {r.status_code}: {r.text[:200]}")
            return False
        return True
    except Exception as e:
        log.error(f"reopen_forum_topic exception: {e}")
        return False


async def answer_callback_query(
    token: str,
    callback_query_id: str,
    text: Optional[str] = None,
) -> bool:
    """Acknowledge a callback_query (inline button tap). MUST be called for
    every callback_query within 30s or Telegram retries. `text` shows as a
    transient toast notification at the top of the operator's screen.
    """
    if not token or not callback_query_id:
        return False
    payload = {"callback_query_id": callback_query_id}
    if text:
        payload["text"] = text[:200]
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                f"{TELEGRAM_API_BASE}/bot{token}/answerCallbackQuery",
                json=payload,
            )
        return r.status_code == 200
    except Exception as e:
        log.error(f"answer_callback_query exception: {e}")
        return False
