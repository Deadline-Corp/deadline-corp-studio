"""Facebook Messenger channel adapter.

Webhook payload reference:
  https://developers.facebook.com/docs/messenger-platform/reference/webhook-events/messages

Typical incoming event:
{
  "object": "page",
  "entry": [{
    "id": "<PAGE_ID>",
    "time": 1700000000,
    "messaging": [{
      "sender":    {"id": "<PSID>"},
      "recipient": {"id": "<PAGE_ID>"},
      "timestamp": 1700000000,
      "message": {
        "mid": "...",
        "text": "Hi"
      }
    }]
  }]
}

We accept only `messaging.message.text`. Skip postbacks, attachments,
delivery receipts, read receipts, echoes.
"""

from __future__ import annotations

import logging
from typing import Optional

import httpx
from pydantic import BaseModel


log = logging.getLogger(__name__)

GRAPH_API_BASE = "https://graph.facebook.com/v21.0"


class NormalizedMessage(BaseModel):
    """Channel-agnostic shape consumed by /message."""
    channel: str = "messenger"
    external_id: str               # sender PSID
    content: str
    username: Optional[str] = None
    channel_conversation_id: str   # thread id = sender PSID for Messenger (1-on-1)


def parse_messenger_webhook(payload: dict) -> Optional[NormalizedMessage]:
    """Extract first text message from a Messenger webhook payload.
    Returns None if no actionable text message found."""
    if not isinstance(payload, dict) or payload.get("object") != "page":
        return None

    for entry in payload.get("entry", []):
        for event in entry.get("messaging", []):
            # Skip echoes (page's own outbound messages)
            if event.get("message", {}).get("is_echo"):
                continue

            sender = event.get("sender", {})
            psid = sender.get("id")
            if not psid:
                continue

            msg = event.get("message", {})
            text = (msg.get("text") or "").strip()
            if not text:
                continue  # attachment / postback / sticker — skip for MVP

            return NormalizedMessage(
                external_id=str(psid),
                content=text,
                channel_conversation_id=str(psid),
            )

    return None


async def send_messenger_reply(page_access_token: str, recipient_psid: str, text: str) -> bool:
    """Send a reply through the Messenger Send API. Returns True on success.

    Uses RESPONSE messaging_type (must be ≤24h since user's last message).
    Truncates text to 1900 chars (Send API limit is 2000 — leave headroom).
    """
    if not page_access_token:
        log.error("messenger: META_PAGE_ACCESS_TOKEN not set — cannot reply")
        return False
    if not recipient_psid or not text:
        return False

    text = text[:1900]

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                f"{GRAPH_API_BASE}/me/messages",
                params={"access_token": page_access_token},
                json={
                    "recipient": {"id": recipient_psid},
                    "messaging_type": "RESPONSE",
                    "message": {"text": text},
                },
            )
        if r.status_code != 200:
            log.warning(f"messenger send {r.status_code}: {r.text[:200]}")
            return False
        return True
    except Exception as e:
        log.error(f"messenger send exception: {e}")
        return False
