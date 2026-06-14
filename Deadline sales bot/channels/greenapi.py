"""Green-API channel adapter (неофициальный WhatsApp через linked-device).

Green-API (green-api.com, Астана) подключает РЕАЛЬНЫЙ номер WhatsApp Business как
связанное устройство по QR — без Meta-верификации и без Cloud API. Номер остаётся
рабочим в телефоне; бот видит переписки через webhook и может отвечать через REST.

Webhook (Green-API → наш `/webhooks/greenapi`), входящее текстовое сообщение:
{
  "typeWebhook": "incomingMessageReceived",
  "instanceData": {"idInstance": 7107651997, "wid": "77058864715@c.us", ...},
  "idMessage": "BAE5...",
  "senderData": {"chatId": "77001234567@c.us", "sender": "77001234567@c.us",
                 "senderName": "Иван", "chatName": "Иван"},
  "messageData": {"typeMessage": "textMessage",
                  "textMessageData": {"textMessage": "Привет"}}
}
extendedTextMessage → messageData.extendedTextMessageData.text
audioMessage (голос) → messageData.fileMessageData.downloadUrl (+ mimeType)

Мы обрабатываем входящие text / extendedText / audio(voice). Исходящие (ручные
ответы команды с телефона) приходят как typeWebhook "outgoingMessageReceived" —
их парсер тоже распознаёт (role-hint в extra_meta), чтобы панель видела полную
переписку; вызывающий решает, что с ними делать.

chatId формат: "<цифры номера>@c.us" (индивидуальный) или "...@g.us" (группа —
пропускаем). Отправка: POST {apiUrl}/waInstance{id}/sendMessage/{token}.
"""

from __future__ import annotations

import logging
from typing import Optional

import httpx
from pydantic import BaseModel

# Транскрипция голоса — тот же Groq Whisper, что в Telegram/Cloud API.
from channels.telegram import transcribe_voice

log = logging.getLogger(__name__)


class NormalizedMessage(BaseModel):
    """Channel-agnostic shape (как у остальных каналов). channel='whatsapp',
    чтобы переиспользовать всю логику воронки/наблюдения/карточки."""
    channel: str = "whatsapp"
    external_id: str               # номер клиента, только цифры
    content: str                   # текст или транскрипт голоса
    username: Optional[str] = None
    channel_conversation_id: str   # тоже номер клиента (один диалог на номер)
    message_type: str = "dm"
    extra_meta: Optional[dict] = None


def _digits_from_chat_id(chat_id: str) -> str:
    """'77001234567@c.us' → '77001234567'."""
    return (chat_id or "").split("@", 1)[0].strip()


def _is_group(chat_id: str) -> bool:
    return (chat_id or "").endswith("@g.us")


async def download_greenapi_media(url: str) -> Optional[bytes]:
    """Скачать медиа (голос/файл) по downloadUrl из webhook. Green-API отдаёт
    прямую ссылку без авторизации (она временная/подписанная)."""
    if not url:
        return None
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(url)
            if r.status_code != 200:
                log.warning(f"greenapi media download {r.status_code}")
                return None
            return r.content
    except Exception as e:  # noqa: BLE001
        log.error(f"greenapi download_media exception: {e}")
        return None


async def parse_greenapi_webhook(
    payload: dict,
    groq_api_key: Optional[str] = None,
) -> Optional[NormalizedMessage]:
    """Green-API webhook → NormalizedMessage. Обрабатывает входящие text/voice
    (и помечает исходящие role='operator' для полноты переписки). None — для
    статусов, групп, неподдерживаемых типов."""
    if not isinstance(payload, dict):
        return None

    hook = payload.get("typeWebhook")
    incoming = hook == "incomingMessageReceived"
    outgoing = hook in ("outgoingMessageReceived", "outgoingAPIMessageReceived")
    if not (incoming or outgoing):
        return None  # statuses / state / device — skip

    sender = payload.get("senderData") or {}
    chat_id = str(sender.get("chatId", ""))
    if not chat_id or _is_group(chat_id):
        return None  # группы пока пропускаем (MVP — личные диалоги)

    peer = _digits_from_chat_id(chat_id)
    if not peer:
        return None
    uname = sender.get("senderName") or sender.get("chatName") or None
    role_hint = "operator" if outgoing else "user"

    md = payload.get("messageData") or {}
    mtype = md.get("typeMessage")

    # ---- text ----
    if mtype == "textMessage":
        text = ((md.get("textMessageData") or {}).get("textMessage") or "").strip()
    elif mtype == "extendedTextMessage":
        text = ((md.get("extendedTextMessageData") or {}).get("text") or "").strip()
    else:
        text = ""

    if text:
        return NormalizedMessage(
            external_id=peer, content=text, username=uname,
            channel_conversation_id=peer, message_type="dm",
            extra_meta={"role_hint": role_hint, "greenapi_msg_id": payload.get("idMessage")},
        )

    # ---- voice / audio ----
    if mtype in ("audioMessage", "voiceMessage"):
        file_md = md.get("fileMessageData") or {}
        url = file_md.get("downloadUrl")
        base = {"role_hint": role_hint, "source": "voice", "greenapi_msg_id": payload.get("idMessage")}
        if not url:
            return None
        if not groq_api_key:
            return NormalizedMessage(
                external_id=peer,
                content="[голосовое — транскрипция не настроена, напишите текстом]",
                username=uname, channel_conversation_id=peer, message_type="dm",
                extra_meta={**base, "transcription_failed": "stt_not_configured"},
            )
        audio = await download_greenapi_media(url)
        if audio is None:
            return NormalizedMessage(
                external_id=peer,
                content="[не получилось скачать голосовое, повторите текстом пожалуйста]",
                username=uname, channel_conversation_id=peer, message_type="dm",
                extra_meta={**base, "transcription_failed": "download"},
            )
        transcript = await transcribe_voice(audio, groq_api_key)
        if not transcript:
            return NormalizedMessage(
                external_id=peer,
                content="[не разобрал голосовое, повторите текстом пожалуйста]",
                username=uname, channel_conversation_id=peer, message_type="dm",
                extra_meta={**base, "transcription_failed": "stt_empty"},
            )
        log.info(f"greenapi voice transcribed ({len(transcript)} chars)")
        return NormalizedMessage(
            external_id=peer, content=transcript, username=uname,
            channel_conversation_id=peer, message_type="dm",
            extra_meta={**base, "transcribed_by": "groq-whisper-v3"},
        )

    # image / video / location / etc — MVP пропускает
    return None


async def send_greenapi_reply(
    api_url: str, id_instance: str, api_token: str, to_peer: str, text: str,
) -> bool:
    """Отправить текст клиенту через Green-API REST. `to_peer` — цифры номера
    (без @c.us, добавим). Возвращает True на успехе."""
    if not (api_url and id_instance and api_token):
        log.error("greenapi send: не заданы GREENAPI_* — отправка невозможна")
        return False
    if not to_peer or not text:
        return False
    chat_id = to_peer if "@" in to_peer else f"{to_peer}@c.us"
    url = f"{api_url.rstrip('/')}/waInstance{id_instance}/sendMessage/{api_token}"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(url, json={"chatId": chat_id, "message": text[:4000]})
        if r.status_code != 200:
            log.warning(f"greenapi sendMessage {r.status_code}: {r.text[:200]}")
            return False
        return True
    except Exception as e:  # noqa: BLE001
        log.error(f"greenapi send exception: {e}")
        return False
