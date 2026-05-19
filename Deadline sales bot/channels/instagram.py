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
    """Channel-agnostic shape consumed by /message.

    `message_type`:
      - "dm"      — direct message (private, full sales-qualification flow)
      - "comment" — public comment under a post / media (short answer + redirect to DM)

    For comments, `extra_meta` carries {"comment_id": "..."} so the webhook
    handler can reply via the Graph API replies endpoint.
    """
    channel: str = "instagram"
    external_id: str               # IGSID for DM, commenter user id for comment
    content: str
    username: Optional[str] = None
    channel_conversation_id: str   # IGSID for DM, media/post id for comment
    message_type: str = "dm"       # "dm" | "comment"
    extra_meta: Optional[dict] = None


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
                message_type="dm",
            )

    return None


def parse_instagram_comment_webhook(payload: dict) -> Optional[NormalizedMessage]:
    """Extract first comment from an IG comments webhook payload.

    Payload shape (different from DM — uses `changes` not `messaging`):
    {
      "object": "instagram",
      "entry": [{
        "id": "<IG_BUSINESS_ID>",
        "changes": [{
          "field": "comments",
          "value": {
            "id": "<COMMENT_ID>",
            "from": {"id": "<USER_ID>", "username": "ivan"},
            "media": {"id": "<MEDIA_ID>"},
            "text": "Cool service!"
          }
        }]
      }]
    }

    Returns None for non-comment events, replies-to-replies, or empty text.
    """
    if not isinstance(payload, dict) or payload.get("object") != "instagram":
        return None

    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            if change.get("field") != "comments":
                continue

            value = change.get("value", {})
            text = (value.get("text") or "").strip()
            if not text:
                continue

            commenter = value.get("from", {})
            commenter_id = commenter.get("id")
            comment_id = value.get("id")
            media_id = value.get("media", {}).get("id", "")

            if not commenter_id or not comment_id:
                continue

            username = commenter.get("username")
            return NormalizedMessage(
                external_id=str(commenter_id),
                content=text,
                username=f"@{username}" if username else None,
                channel_conversation_id=str(media_id) or str(comment_id),
                message_type="comment",
                extra_meta={"comment_id": str(comment_id)},
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


async def send_instagram_comment_reply(page_access_token: str, comment_id: str, text: str) -> bool:
    """Reply to an Instagram comment publicly via POST /{comment_id}/replies.

    Requires permission `instagram_manage_comments` on the Meta App.
    Returns True on success.
    """
    if not page_access_token:
        log.error("instagram: META_PAGE_ACCESS_TOKEN not set — cannot reply to comment")
        return False
    if not comment_id or not text:
        return False

    text = text[:280]  # IG comment limit is 2200, but stay short for tone

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                f"{GRAPH_API_BASE}/{comment_id}/replies",
                params={"access_token": page_access_token},
                json={"message": text},
            )
        if r.status_code != 200:
            log.warning(f"instagram comment reply {r.status_code}: {r.text[:200]}")
            return False
        return True
    except Exception as e:
        log.error(f"instagram comment reply exception: {e}")
        return False
