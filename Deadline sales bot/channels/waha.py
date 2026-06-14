"""WAHA channel adapter (self-hosted unofficial WhatsApp, devlikeapro/waha).

WAHA = open-source обёртка над Baileys/whatsapp-web.js, поднята на нашем VPS
(Docker). Подключает реальный номер по QR как связанное устройство — без
Meta-верификации, без лимитов на чаты, $0 за софт. Номер остаётся в телефоне.

Webhook (WAHA → наш `/webhooks/waha`), событие message / message.any:
{
  "event": "message",
  "session": "default",
  "payload": {
    "id": "false_77001234567@c.us_ABC",
    "from": "77001234567@c.us",
    "fromMe": false,
    "body": "Привет",
    "type": "chat",            # "ptt"/"audio" для голоса
    "hasMedia": false,
    "notifyName": "Иван",
    "media": {"url": "...", "mimetype": "audio/ogg"}   # если hasMedia
  }
}
Отправка: POST {base}/api/sendText  {session, chatId:"<digits>@c.us", text}
с заголовком X-Api-Key.
"""

from __future__ import annotations

import logging
from typing import Optional

import httpx
from pydantic import BaseModel

from channels.telegram import transcribe_voice

log = logging.getLogger(__name__)


class NormalizedMessage(BaseModel):
    channel: str = "whatsapp"
    external_id: str
    content: str
    username: Optional[str] = None
    channel_conversation_id: str
    message_type: str = "dm"
    extra_meta: Optional[dict] = None


def _digits(chat_id: str) -> str:
    return (chat_id or "").split("@", 1)[0].strip()


def _is_group(chat_id: str) -> bool:
    return (chat_id or "").endswith("@g.us")


async def _download_waha_media(url: str, api_key: str) -> Optional[bytes]:
    """Скачать медиа (голос) из WAHA. URL может быть на нашем WAHA (нужен
    X-Api-Key) или внешний lookaside — заголовок не помешает."""
    if not url:
        return None
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(url, headers={"X-Api-Key": api_key} if api_key else {})
            if r.status_code != 200:
                log.warning(f"waha media download {r.status_code}")
                return None
            return r.content
    except Exception as e:  # noqa: BLE001
        log.error(f"waha download_media exception: {e}")
        return None


async def parse_waha_webhook(
    payload: dict,
    groq_api_key: Optional[str] = None,
    api_key: Optional[str] = None,
) -> Optional[NormalizedMessage]:
    """WAHA webhook → NormalizedMessage. Текст + голос (через Groq Whisper).
    fromMe (ручной ответ команды) помечается role_hint='operator'. Группы,
    статусы, не-сообщения → None."""
    if not isinstance(payload, dict):
        return None
    if payload.get("event") not in ("message", "message.any"):
        return None

    p = payload.get("payload") or {}
    frm = str(p.get("from", ""))
    if not frm or _is_group(frm):
        return None
    peer = _digits(frm)
    if not peer:
        return None

    role_hint = "operator" if p.get("fromMe") else "user"
    uname = p.get("notifyName") or None
    mtype = (p.get("type") or "").lower()

    # ---- voice / audio ----
    if mtype in ("ptt", "audio"):
        media = p.get("media") or {}
        url = media.get("url")
        base = {"role_hint": role_hint, "source": "voice", "waha_id": p.get("id")}
        if not url:
            return None
        if not groq_api_key:
            return NormalizedMessage(
                external_id=peer,
                content="[голосовое — транскрипция не настроена, напишите текстом]",
                username=uname, channel_conversation_id=peer,
                extra_meta={**base, "transcription_failed": "stt_not_configured"},
            )
        audio = await _download_waha_media(url, api_key or "")
        if audio is None:
            return NormalizedMessage(
                external_id=peer,
                content="[не получилось скачать голосовое, повторите текстом пожалуйста]",
                username=uname, channel_conversation_id=peer,
                extra_meta={**base, "transcription_failed": "download"},
            )
        transcript = await transcribe_voice(audio, groq_api_key)
        if not transcript:
            return NormalizedMessage(
                external_id=peer,
                content="[не разобрал голосовое, повторите текстом пожалуйста]",
                username=uname, channel_conversation_id=peer,
                extra_meta={**base, "transcription_failed": "stt_empty"},
            )
        log.info(f"waha voice transcribed ({len(transcript)} chars)")
        return NormalizedMessage(
            external_id=peer, content=transcript, username=uname,
            channel_conversation_id=peer,
            extra_meta={**base, "transcribed_by": "groq-whisper-v3"},
        )

    # ---- text (chat) ----
    text = (p.get("body") or "").strip()
    if text and mtype not in ("image", "video", "document", "sticker", "location"):
        return NormalizedMessage(
            external_id=peer, content=text, username=uname,
            channel_conversation_id=peer,
            extra_meta={"role_hint": role_hint, "waha_id": p.get("id")},
        )

    return None


async def send_waha_reply(
    base_url: str, api_key: str, session: str, to_peer: str, text: str,
) -> bool:
    """Отправить текст клиенту через WAHA REST (POST /api/sendText)."""
    if not base_url or not to_peer or not text:
        return False
    chat_id = to_peer if "@" in to_peer else f"{to_peer}@c.us"
    url = f"{base_url.rstrip('/')}/api/sendText"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["X-Api-Key"] = api_key
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(url, headers=headers, json={
                "session": session or "default", "chatId": chat_id, "text": text[:4000],
            })
        if r.status_code not in (200, 201):
            log.warning(f"waha sendText {r.status_code}: {r.text[:200]}")
            return False
        return True
    except Exception as e:  # noqa: BLE001
        log.error(f"waha send exception: {e}")
        return False
