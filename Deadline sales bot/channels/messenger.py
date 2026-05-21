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
    """Channel-agnostic shape consumed by /message.

    `message_type`:
      - "dm"      — private message (full sales-qualification flow)
      - "comment" — public Page-feed comment (short answer + redirect to DM)

    For comments, `extra_meta` carries {"comment_id": "..."} so the webhook
    handler can reply via the Graph API comments endpoint.
    """
    channel: str = "messenger"
    external_id: str               # PSID for DM, commenter user id for comment
    content: str
    username: Optional[str] = None
    channel_conversation_id: str   # PSID for DM, post id for comment
    message_type: str = "dm"       # "dm" | "comment"
    extra_meta: Optional[dict] = None


def parse_messenger_webhook(payload: dict) -> Optional[NormalizedMessage]:
    """Extract first text message from a Messenger DM webhook payload.
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
                message_type="dm",
            )

    return None


def parse_messenger_comment_webhook(payload: dict) -> Optional[NormalizedMessage]:
    """Extract first new comment from a FB Page feed webhook payload.

    Payload shape (uses `changes` not `messaging`):
    {
      "object": "page",
      "entry": [{
        "id": "<PAGE_ID>",
        "changes": [{
          "field": "feed",
          "value": {
            "from": {"id": "<USER_ID>", "name": "Ivan"},
            "item": "comment",
            "verb": "add",
            "post_id": "<PAGE_ID>_<POST_ID>",
            "comment_id": "<PAGE_ID>_<COMMENT_ID>",
            "message": "When are you launching?"
          }
        }]
      }]
    }

    We accept only `item=comment` with `verb=add` (new comments, not edits/likes).
    Skip our own Page comments to avoid bot-replying-to-itself loops.
    """
    if not isinstance(payload, dict) or payload.get("object") != "page":
        return None

    for entry in payload.get("entry", []):
        page_id = str(entry.get("id", ""))

        for change in entry.get("changes", []):
            if change.get("field") != "feed":
                continue

            value = change.get("value", {})
            if value.get("item") != "comment" or value.get("verb") != "add":
                continue  # likes, edits, post updates — skip

            text = (value.get("message") or "").strip()
            if not text:
                continue

            commenter = value.get("from", {})
            commenter_id = str(commenter.get("id", ""))
            comment_id = str(value.get("comment_id", ""))
            post_id = str(value.get("post_id", ""))

            if not commenter_id or not comment_id:
                continue

            # Skip the Page's own comments (avoid replying to ourselves).
            # FB sets `from.id == page_id` when the Page authored the comment.
            if commenter_id == page_id:
                continue

            name = commenter.get("name")
            return NormalizedMessage(
                external_id=commenter_id,
                content=text,
                username=name,
                channel_conversation_id=post_id or comment_id,
                message_type="comment",
                extra_meta={"comment_id": comment_id},
            )

    return None


async def send_messenger_reply(
    page_access_token: str,
    recipient_psid: str,
    text: str,
    *,
    messaging_type: str = "RESPONSE",
    tag: Optional[str] = None,
) -> bool:
    """Send a DM reply through the Messenger Send API. Returns True on success.

    Default `messaging_type="RESPONSE"` works only within the 24-hour window
    since the user's last message. For operator replies that may land beyond
    24h (human-agent takeover scenarios), pass `messaging_type="MESSAGE_TAG"`
    + `tag="HUMAN_AGENT"` — extends the window to 7 days.

    HUMAN_AGENT tag requires the `human_agent` permission on the Meta App
    (Standard Access in App Review). Without it Meta returns 4xx.

    Truncates text to 1900 chars (Send API limit is 2000 — leave headroom).
    """
    if not page_access_token:
        log.error("messenger: META_PAGE_ACCESS_TOKEN not set — cannot reply")
        return False
    if not recipient_psid or not text:
        return False

    text = text[:1900]
    payload: dict = {
        "recipient": {"id": recipient_psid},
        "messaging_type": messaging_type,
        "message": {"text": text},
    }
    if messaging_type == "MESSAGE_TAG" and tag:
        payload["tag"] = tag

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                f"{GRAPH_API_BASE}/me/messages",
                params={"access_token": page_access_token},
                json=payload,
            )
        if r.status_code != 200:
            log.warning(f"messenger send {r.status_code} (type={messaging_type} tag={tag}): {r.text[:200]}")
            return False
        return True
    except Exception as e:
        log.error(f"messenger send exception: {e}")
        return False


async def send_messenger_comment_reply(page_access_token: str, comment_id: str, text: str) -> bool:
    """Reply to a FB Page comment publicly via POST /{comment_id}/comments.

    Requires permission `pages_manage_engagement` on the Meta App.
    Returns True on success.
    """
    if not page_access_token:
        log.error("messenger: META_PAGE_ACCESS_TOKEN not set — cannot reply to comment")
        return False
    if not comment_id or not text:
        return False

    text = text[:280]  # FB comment limit is 8000+ but stay short for tone

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                f"{GRAPH_API_BASE}/{comment_id}/comments",
                params={"access_token": page_access_token},
                json={"message": text},
            )
        if r.status_code != 200:
            log.warning(f"messenger comment reply {r.status_code}: {r.text[:200]}")
            return False
        return True
    except Exception as e:
        log.error(f"messenger comment reply exception: {e}")
        return False
