"""Instagram Direct Messages channel adapter.

Wire-protocol almost identical to Messenger (same Graph API surface, IGSID
instead of PSID), but webhook subscriptions are configured separately in the
Meta App dashboard under "Instagram Graph API".

Webhook payload reference:
  https://developers.facebook.com/docs/messenger-platform/instagram/get-started

Typical incoming event (object="instagram"):
{
  "object": "instagram",
  "entry": [{
    "id": "<IG_BUSINESS_ID>",
    "time": 1700000000,
    "messaging": [{
      "sender":    {"id": "<IGSID>"},
      "recipient": {"id": "<IG_BUSINESS_ID>"},
      "timestamp": 1700000000,
      "message": {"mid": "...", "text": "Hi"}
    }]
  }]
}

For MVP we accept only `messaging.message.text`. Skip stories replies,
reactions, attachments — they need different handling.
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
    channel: str = "instagram"
    external_id: str               # IGSID (Instagram-Scoped ID)
    content: str
    username: Optional[str] = None  # IG handle not in webhook by default
    channel_conversation_id: str   # thread = IGSID for 1-on-1 DM


def parse_instagram_webhook(payload: dict) -> Optional[NormalizedMessage]:
    """Extract first text message from an IG DM webhook payload."""
    if not isinstance(payload, dict) or payload.get("object") != "instagram":
        return None

    for entry in payload.get("entry", []):
        for event in entry.get("messaging", []):
            if event.get("message", {}).get("is_echo"):
                continue

            sender = event.get("sender", {})
            igsid = sender.get("id")
            if not igsid:
                continue

            msg = event.get("message", {})
            text = (msg.get("text") or "").strip()
            if not text:
                continue

            return NormalizedMessage(
                external_id=str(igsid),
                content=text,
                channel_conversation_id=str(igsid),
            )

    return None


async def send_instagram_reply(page_access_token: str, recipient_igsid: str, text: str) -> bool:
    """Send a DM reply through the Send API (IG uses the same /me/messages
    endpoint as Messenger, with the same Page Access Token of the linked Page).
    """
    if not page_access_token:
        log.error("instagram: META_PAGE_ACCESS_TOKEN not set — cannot reply")
        return False
    if not recipient_igsid or not text:
        return False

    text = text[:1900]

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                f"{GRAPH_API_BASE}/me/messages",
                params={"access_token": page_access_token},
                json={
                    "recipient": {"id": recipient_igsid},
                    "messaging_type": "RESPONSE",
                    "message": {"text": text},
                },
            )
        if r.status_code != 200:
            log.warning(f"instagram send {r.status_code}: {r.text[:200]}")
            return False
        return True
    except Exception as e:
        log.error(f"instagram send exception: {e}")
        return False
