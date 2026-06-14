"""Operator actions shared between the Telegram forum flow and the Admin UI.

Раньше «ответ оператора лиду» и «takeover вкл/выкл» жили только внутри
main._handle_operator_message / _handle_operator_callback (Telegram-форум).
С появлением Admin UI (admin_api.py) те же действия нужны из веба. Чтобы обе
точки входа вели себя РОВНО одинаково (один код = нет рассинхрона), доставка
и переключение takeover вынесены сюда.

Контракт:
- deliver_operator_reply() — отправить текст/вложение лиду в его канал.
  Возвращает delivered: bool. НЕ пишет в БД (это делает вызывающий — у
  форум-пути и UI-пути разные extra_meta).
- set_takeover_with_mirror() — флаг в БД + зеркало в форум-топик (открыть/
  закрыть топик + статус-сообщение), как делала кнопка «Возьму на себя».
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from sqlalchemy.orm import Session

from services.conversations import set_operator_takeover
from channels.telegram import (
    send_telegram_reply,
    forward_attachment,
    send_to_topic,
    close_forum_topic,
    reopen_forum_topic,
)
from channels.instagram import send_instagram_reply
from channels.messenger import send_messenger_reply

log = logging.getLogger(__name__)


async def deliver_operator_reply(
    conv: Any,
    text: str,
    settings: Any,
    attachment: Optional[tuple] = None,
) -> bool:
    """Forward an operator-authored text (and optional Telegram attachment)
    to the lead's original channel. Mirrors the behavior previously inlined
    in main._handle_operator_message — bit-for-bit:

    - telegram  → sendMessage / forward_attachment
    - instagram → Graph API with HUMAN_AGENT tag (7-day window)
    - messenger → Graph API with HUMAN_AGENT tag
    - website   → no push channel; message just lives in DB (delivered=True)

    Meta channels can't receive attachments yet (needs /me/message_attachments
    upload) — we log and return False so the caller can warn the operator.
    """
    token = settings.telegram_bot_token
    delivered = False

    if conv.channel == "telegram" and conv.channel_conversation_id:
        if attachment:
            attachment_type, file_id, _ = attachment
            delivered = await forward_attachment(
                token, conv.channel_conversation_id,
                attachment_type, file_id,
                caption=text or None,
            )
            log.info(
                f"[{str(conv.id)[:8]}] operator forwarded {attachment_type} "
                f"to telegram lead (delivered={delivered})"
            )
        else:
            delivered = await send_telegram_reply(token, conv.channel_conversation_id, text)
    elif conv.channel == "instagram" and conv.channel_conversation_id:
        if attachment:
            log.warning(
                f"[{str(conv.id)[:8]}] operator sent {attachment[0]} to IG lead — "
                f"attachment forwarding not implemented for Meta channels yet "
                f"(would need /me/message_attachments upload). Operator should send text only."
            )
            delivered = False
        else:
            delivered = await send_instagram_reply(
                settings.meta_page_access_token,
                conv.channel_conversation_id,
                text,
                messaging_type="MESSAGE_TAG",
                tag="HUMAN_AGENT",
            )
    elif conv.channel == "messenger" and conv.channel_conversation_id:
        if attachment:
            log.warning(
                f"[{str(conv.id)[:8]}] operator sent {attachment[0]} to Messenger lead — "
                f"attachment forwarding not implemented for Meta channels yet "
                f"(would need /me/message_attachments upload). Operator should send text only."
            )
            delivered = False
        else:
            delivered = await send_messenger_reply(
                settings.meta_page_access_token,
                conv.channel_conversation_id,
                text,
                messaging_type="MESSAGE_TAG",
                tag="HUMAN_AGENT",
            )
    elif conv.channel == "whatsapp" and conv.channel_conversation_id:
        if attachment:
            log.warning(
                f"[{str(conv.id)[:8]}] operator sent {attachment[0]} to WhatsApp lead — "
                f"attachment forwarding not implemented for WhatsApp yet. Send text only."
            )
            delivered = False
        elif getattr(settings, "waha_base_url", None):
            from channels.waha import send_waha_reply
            delivered = await send_waha_reply(
                settings.waha_base_url, getattr(settings, "waha_api_key", None) or "",
                getattr(settings, "waha_session", None) or "default",
                conv.channel_conversation_id, text,
            )
        elif getattr(settings, "greenapi_id_instance", None) and getattr(settings, "greenapi_api_token", None):
            from channels.greenapi import send_greenapi_reply
            delivered = await send_greenapi_reply(
                getattr(settings, "greenapi_api_url", None) or "https://api.green-api.com",
                settings.greenapi_id_instance, settings.greenapi_api_token,
                conv.channel_conversation_id, text,
            )
        else:
            from channels.whatsapp import send_whatsapp_reply
            delivered = await send_whatsapp_reply(
                settings.whatsapp_token,
                settings.whatsapp_phone_number_id or "",
                conv.channel_conversation_id,
                text,
            )
    else:
        # Website — no push. Message just lives in DB; the widget will see
        # it on the next /chat poll (Phase 2: switch widget to long-poll or SSE).
        log.info(f"[{str(conv.id)[:8]}] operator wrote on channel={conv.channel} (no push, stored only)")
        delivered = True

    return delivered


async def set_takeover_with_mirror(
    db: Session,
    conv: Any,
    enabled: bool,
    settings: Any,
    source: str = "admin-ui",
) -> None:
    """Toggle operator_takeover + mirror the state into the Telegram forum
    topic exactly like the inline button does (_handle_operator_callback):
      ON  → reopen topic (operator can type) + state message
      OFF → close topic (bot speaks) + state message

    Форум-зеркало best-effort: если Telegram недоступен — флаг в БД всё равно
    переключён (источник правды), а зеркалирование просто залогируется.
    """
    set_operator_takeover(db, conv.id, enabled)
    db.commit()

    token = settings.telegram_bot_token
    if not (conv.forum_topic_id and settings.telegram_operator_group_id and token):
        return
    try:
        if enabled:
            await reopen_forum_topic(
                token, settings.telegram_operator_group_id, conv.forum_topic_id,
            )
        else:
            await close_forum_topic(
                token, settings.telegram_operator_group_id, conv.forum_topic_id,
            )
        state_msg = (
            f"🔔 OPERATOR TAKEOVER ON (via {source}) — каждое сообщение в теме идёт лиду напрямую. "
            "Команды: /release — снять takeover · /close — закрыть · /note <текст> — внутренняя пометка."
            if enabled
            else f"🔔 OPERATOR RELEASED (via {source}) — бот снова отвечает автономно."
        )
        await send_to_topic(
            token, settings.telegram_operator_group_id, conv.forum_topic_id, state_msg,
        )
    except Exception as e:  # noqa: BLE001 — mirror is best-effort
        log.warning(f"[{str(conv.id)[:8]}] takeover forum mirror failed: {e}")


async def mirror_to_forum(
    conv: Any,
    text: str,
    settings: Any,
) -> None:
    """Best-effort: показать в форум-топике лида сообщение, отправленное из
    другого интерфейса (Admin UI), чтобы операторы в Telegram видели полную
    картину и не ответили дважды."""
    token = settings.telegram_bot_token
    if not (conv.forum_topic_id and settings.telegram_operator_group_id and token):
        return
    try:
        await send_to_topic(
            token, settings.telegram_operator_group_id, conv.forum_topic_id, text,
        )
    except Exception as e:  # noqa: BLE001
        log.warning(f"[{str(conv.id)[:8]}] forum mirror failed: {e}")
