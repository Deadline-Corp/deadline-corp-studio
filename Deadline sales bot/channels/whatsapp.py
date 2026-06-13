"""WhatsApp Cloud API channel adapter (Meta).

Uses the same Meta webhook + Graph API infrastructure as Messenger/Instagram,
so the shape mirrors channels/messenger.py.

Webhook payload reference:
  https://developers.facebook.com/docs/whatsapp/cloud-api/webhooks/payload-examples

Typical incoming text message:
{
  "object": "whatsapp_business_account",
  "entry": [{
    "id": "<WABA_ID>",
    "changes": [{
      "field": "messages",
      "value": {
        "messaging_product": "whatsapp",
        "metadata": {"display_phone_number": "...", "phone_number_id": "<PHONE_NUMBER_ID>"},
        "contacts": [{"profile": {"name": "Ivan"}, "wa_id": "<USER_PHONE>"}],
        "messages": [{
          "from": "<USER_PHONE>",
          "id": "wamid...",
          "timestamp": "...",
          "type": "text",
          "text": {"body": "Hi"}
        }]
      }
    }]
  }]
}

We accept only `type=text`. Skip statuses (delivery/read), media, reactions.
Sending free-form text only works inside the 24-hour customer-service window
(i.e. when the user messaged us first — which is exactly the inbound-reply case
this handler covers). Proactive/template messages are a separate path (TODO).
"""

from __future__ import annotations

import logging
from typing import Optional

import httpx
from pydantic import BaseModel


log = logging.getLogger(__name__)

GRAPH_API_BASE = "https://graph.facebook.com/v21.0"


class NormalizedMessage(BaseModel):
    """Channel-agnostic shape consumed by /message (mirror of messenger)."""
    channel: str = "whatsapp"
    external_id: str               # user's wa_id (phone number, digits only)
    content: str
    username: Optional[str] = None
    channel_conversation_id: str   # wa_id (one thread per phone number)
    message_type: str = "dm"
    # phone_number_id from metadata — needed to send the reply back via the
    # correct business number. Carried so the webhook handler can route sends
    # even when multiple numbers share one app.
    extra_meta: Optional[dict] = None


def parse_whatsapp_webhook(payload: dict) -> Optional[NormalizedMessage]:
    """Extract the first inbound text message from a WhatsApp Cloud API
    webhook payload. Returns None for statuses / non-text / malformed events."""
    if not isinstance(payload, dict) or payload.get("object") != "whatsapp_business_account":
        return None

    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            if change.get("field") != "messages":
                continue
            value = change.get("value", {})

            # statuses (delivered/read/sent) carry no "messages" — skip.
            messages = value.get("messages") or []
            if not messages:
                continue

            # Map wa_id -> profile name from the contacts array (if present).
            name_by_waid: dict[str, str] = {}
            for c in value.get("contacts", []) or []:
                wid = str(c.get("wa_id", ""))
                nm = (c.get("profile") or {}).get("name")
                if wid and nm:
                    name_by_waid[wid] = nm

            phone_number_id = str((value.get("metadata") or {}).get("phone_number_id", ""))

            for msg in messages:
                if msg.get("type") != "text":
                    continue  # media/reaction/location/etc — skip for MVP
                text = ((msg.get("text") or {}).get("body") or "").strip()
                if not text:
                    continue
                wa_from = str(msg.get("from", ""))
                if not wa_from:
                    continue
                return NormalizedMessage(
                    external_id=wa_from,
                    content=text,
                    username=name_by_waid.get(wa_from),
                    channel_conversation_id=wa_from,
                    message_type="dm",
                    extra_meta={"phone_number_id": phone_number_id} if phone_number_id else None,
                )

    return None


async def send_whatsapp_reply(
    access_token: str,
    phone_number_id: str,
    to_wa_id: str,
    text: str,
) -> bool:
    """Send a free-form text reply via the WhatsApp Cloud API.

    Works only inside the 24-hour customer-service window (user messaged us
    within the last 24h). Outside it, Meta requires a pre-approved template —
    not handled here (proactive/template path is separate, see roadmap).

    `phone_number_id` is the business number id (from webhook metadata or the
    configured WHATSAPP_PHONE_NUMBER_ID). Returns True on HTTP 200.
    Truncates text to 4000 chars (WA body limit is 4096 — leave headroom).
    """
    if not access_token:
        log.error("whatsapp: WHATSAPP_TOKEN not set — cannot reply")
        return False
    if not phone_number_id:
        log.error("whatsapp: phone_number_id missing — cannot reply")
        return False
    if not to_wa_id or not text:
        return False

    text = text[:4000]
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to_wa_id,
        "type": "text",
        "text": {"body": text},
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                f"{GRAPH_API_BASE}/{phone_number_id}/messages",
                headers={"Authorization": f"Bearer {access_token}"},
                json=payload,
            )
        if r.status_code != 200:
            log.warning(f"whatsapp send {r.status_code}: {r.text[:200]}")
            return False
        return True
    except Exception as e:
        log.error(f"whatsapp send exception: {e}")
        return False
