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

We accept `type=text` and `type=audio` (voice). Voice is downloaded via the
media API and transcribed with Groq Whisper (same engine as Telegram voice).
Statuses (delivery/read), reactions, images, etc. are skipped.

Coexistence (one number used in BOTH the WhatsApp Business App and the Cloud
API): messages the team sends from the app arrive under the
`smb_message_echoes` field and as messages from our own number — both are
skipped so the bot never auto-replies to a human teammate's message.

Sending free-form text only works inside the 24-hour customer-service window
(i.e. when the user messaged us first — which is exactly the inbound-reply case
this handler covers). Proactive/template messages are a separate path (TODO).
"""

from __future__ import annotations

import logging
from typing import Optional

import httpx
from pydantic import BaseModel

# Voice transcription is channel-agnostic — reuse the Telegram path's Groq
# Whisper helper (telegram.py imports nothing from whatsapp.py → no cycle).
from channels.telegram import transcribe_voice


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


def _digits(s: str) -> str:
    """Keep only digits — for comparing phone numbers regardless of +/spaces."""
    return "".join(ch for ch in s if ch.isdigit())


async def download_whatsapp_media(access_token: str, media_id: str) -> Optional[bytes]:
    """Fetch media (voice/audio) binary from the WhatsApp Cloud API. Two-step:
      1. GET /<media_id> → {"url": "<lookaside url>", ...}  (url ~5 min TTL)
      2. GET <url> with Bearer → binary bytes

    Returns None on any failure. Incoming voice is audio/ogg;codecs=opus, which
    Groq Whisper accepts directly — no FFmpeg transcoding needed."""
    if not access_token or not media_id:
        return None
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(
                f"{GRAPH_API_BASE}/{media_id}",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if r.status_code != 200:
                log.warning(f"whatsapp getMedia {r.status_code}: {r.text[:200]}")
                return None
            url = (r.json() or {}).get("url")
            if not url:
                log.warning("whatsapp getMedia: no url in response")
                return None
            r2 = await client.get(url, headers={"Authorization": f"Bearer {access_token}"})
            if r2.status_code != 200:
                log.warning(f"whatsapp media download {r2.status_code}")
                return None
            return r2.content
    except Exception as e:
        log.error(f"whatsapp download_whatsapp_media exception: {e}")
        return None


async def parse_whatsapp_webhook(
    payload: dict,
    access_token: Optional[str] = None,
    groq_api_key: Optional[str] = None,
) -> Optional[NormalizedMessage]:
    """Extract the first inbound text OR voice message from a WhatsApp Cloud
    API webhook payload. Voice is downloaded + transcribed (Groq Whisper) when
    `access_token` and `groq_api_key` are provided. Returns None for statuses,
    coexistence echoes, self-messages, unsupported media, or malformed events.

    Now async because the voice path performs network I/O (media download +
    transcription)."""
    if not isinstance(payload, dict) or payload.get("object") != "whatsapp_business_account":
        return None

    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            field = change.get("field")
            # Coexistence: messages the team sends from the app come as echoes.
            if field == "smb_message_echoes":
                continue
            if field != "messages":
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

            metadata = value.get("metadata") or {}
            phone_number_id = str(metadata.get("phone_number_id", ""))
            # Our own business number (digits) — drop self/echo messages.
            own_number = _digits(str(metadata.get("display_phone_number", "")))

            for msg in messages:
                wa_from = str(msg.get("from", ""))
                if not wa_from:
                    continue
                # Coexistence self-echo guard: never reply to our own number.
                if own_number and _digits(wa_from) == own_number:
                    continue

                mtype = msg.get("type")
                extra = {"phone_number_id": phone_number_id} if phone_number_id else {}
                uname = name_by_waid.get(wa_from)

                # ---- text ----
                if mtype == "text":
                    text = ((msg.get("text") or {}).get("body") or "").strip()
                    if not text:
                        continue
                    return NormalizedMessage(
                        external_id=wa_from, content=text, username=uname,
                        channel_conversation_id=wa_from, message_type="dm",
                        extra_meta=extra or None,
                    )

                # ---- voice / audio ----
                if mtype == "audio":
                    media_id = (msg.get("audio") or {}).get("id")
                    if not media_id:
                        continue
                    base = {**extra, "source": "voice"}
                    if not access_token or not groq_api_key:
                        return NormalizedMessage(
                            external_id=wa_from,
                            content="[голосовое — транскрипция не настроена, напишите текстом]",
                            username=uname, channel_conversation_id=wa_from, message_type="dm",
                            extra_meta={**base, "transcription_failed": "stt_not_configured"},
                        )
                    audio = await download_whatsapp_media(access_token, media_id)
                    if audio is None:
                        return NormalizedMessage(
                            external_id=wa_from,
                            content="[не получилось скачать голосовое, повторите текстом пожалуйста]",
                            username=uname, channel_conversation_id=wa_from, message_type="dm",
                            extra_meta={**base, "transcription_failed": "download"},
                        )
                    transcript = await transcribe_voice(audio, groq_api_key)
                    if not transcript:
                        return NormalizedMessage(
                            external_id=wa_from,
                            content="[не разобрал голосовое, повторите текстом пожалуйста]",
                            username=uname, channel_conversation_id=wa_from, message_type="dm",
                            extra_meta={**base, "transcription_failed": "stt_empty"},
                        )
                    log.info(f"whatsapp voice transcribed ({len(transcript)} chars)")
                    return NormalizedMessage(
                        external_id=wa_from, content=transcript, username=uname,
                        channel_conversation_id=wa_from, message_type="dm",
                        extra_meta={**base, "transcribed_by": "groq-whisper-v3"},
                    )

                # other types (image/location/reaction/...) — skip for MVP
                continue

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
