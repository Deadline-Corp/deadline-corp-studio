"""Telegram channel adapter.

Two halves:
  * parse_telegram_webhook(payload)   — Bot API Update → NormalizedMessage
  * send_telegram_reply(token, ...)    — sendMessage HTTP call back to user

Webhook expected shape (Bot API ≥ 7.x):
{
  "update_id": 123,
  "message": {
    "message_id": 42,
    "from":  {"id": 555, "username": "ivan", ...},
    "chat":  {"id": 555, "type": "private", ...},
    "date":  1700000000,
    "text":  "Привет"
  }
}

We accept only `message.text` for now. Voice / photo / sticker / inline
queries / edited messages / channel posts — ignored (return None).
"""

from __future__ import annotations

import logging
from typing import Optional

import httpx
from pydantic import BaseModel


log = logging.getLogger(__name__)

TELEGRAM_API_BASE = "https://api.telegram.org"


class NormalizedMessage(BaseModel):
    """Channel-agnostic shape consumed by the /message endpoint."""
    channel: str = "telegram"
    external_id: str               # Telegram user.id (string for safety)
    content: str                   # raw text message
    username: Optional[str] = None  # "@ivan" if user has one
    channel_conversation_id: str   # Telegram chat.id (= user.id for private chats)


def parse_telegram_webhook(payload: dict) -> Optional[NormalizedMessage]:
    """Convert a Telegram Update payload into NormalizedMessage. Return None
    if the update is not a text message we can handle."""
    if not isinstance(payload, dict):
        return None

    msg = payload.get("message")
    if not isinstance(msg, dict):
        return None  # could be edited_message / channel_post / callback_query — skip for MVP

    text = (msg.get("text") or "").strip()
    if not text:
        return None  # voice, photo, sticker, etc.

    from_user = msg.get("from") or {}
    chat = msg.get("chat") or {}

    user_id = from_user.get("id")
    chat_id = chat.get("id")
    if user_id is None or chat_id is None:
        log.warning(f"telegram: payload missing from.id or chat.id — {payload!r}")
        return None

    username_raw = from_user.get("username")
    username = f"@{username_raw}" if username_raw else None

    return NormalizedMessage(
        external_id=str(user_id),
        content=text,
        username=username,
        channel_conversation_id=str(chat_id),
    )


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
