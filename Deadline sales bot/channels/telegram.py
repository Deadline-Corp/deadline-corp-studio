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


async def set_telegram_webhook(token: str, url: str) -> dict:
    """Configure Telegram to POST every update to `url`. Idempotent. Returns
    the Telegram response dict. Call from a script after deploy, not on every
    container start (Bot API rate-limits setWebhook).
    """
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(
            f"{TELEGRAM_API_BASE}/bot{token}/setWebhook",
            json={"url": url, "allowed_updates": ["message"]},
        )
    return r.json()
