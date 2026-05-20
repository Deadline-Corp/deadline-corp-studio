"""Conversation + message persistence — DB-backed replacement for the
in-memory `SESSIONS` dict in main.py.

A `Conversation` is a single thread between a customer and the bot on a
specific channel. The same customer can have multiple conversations
(e.g. closed one from last month + currently open one). For channels that
have native thread IDs (Telegram chat_id, IG/FB thread_id), we store that
in `channel_conversation_id` so we can pick the right one even if multiple
exist.

For the website, `channel_conversation_id` is the widget's session_id —
matches the in-memory key the old /chat endpoint used.

This module is dormant until Day 4 — main.py still uses `SESSIONS` dict.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import select, desc
from sqlalchemy.orm import Session

from db.models import Conversation, Message, ConversationStatusEnum


log = logging.getLogger(__name__)


def get_or_create_conversation(
    db: Session,
    customer_id: UUID,
    channel: str,
    channel_conversation_id: Optional[str] = None,
) -> Conversation:
    """Return the right open conversation for (customer, channel, [thread]).

    Lookup priority:
      1. If channel_conversation_id is given → find OPEN conversation matching
         that (customer, channel, thread). This is the IG/FB/Telegram case
         where the channel itself provides a thread id.
      2. Else → find the most recently created OPEN conversation for this
         (customer, channel). This is the website case where the widget may
         generate a new session_id every page load but we want to keep one
         logical conversation per customer-channel.
      3. If nothing found → create a new OPEN conversation.
    """
    if channel_conversation_id:
        existing = db.execute(
            select(Conversation).where(
                Conversation.customer_id == customer_id,
                Conversation.channel == channel,
                Conversation.channel_conversation_id == channel_conversation_id,
                Conversation.status == ConversationStatusEnum.OPEN.value,
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing
    else:
        existing = db.execute(
            select(Conversation)
            .where(
                Conversation.customer_id == customer_id,
                Conversation.channel == channel,
                Conversation.status == ConversationStatusEnum.OPEN.value,
            )
            .order_by(desc(Conversation.created_at))
            .limit(1)
        ).scalar_one_or_none()
        if existing is not None:
            return existing

    conversation = Conversation(
        customer_id=customer_id,
        channel=channel,
        channel_conversation_id=channel_conversation_id,
        status=ConversationStatusEnum.OPEN.value,
    )
    db.add(conversation)
    db.flush()
    return conversation


def append_message(
    db: Session,
    conversation_id: UUID,
    role: str,
    content: str,
    extra_meta: Optional[dict] = None,
    embedding: Optional[list[float]] = None,
) -> Message:
    """Append a message and bump conversation.last_message_at.

    `role` is one of RoleEnum values (user / assistant / system / operator).
    `extra_meta` is freeform JSONB — useful for channel_msg_id, attachments,
    voice_duration_sec, latency_ms, etc.
    `embedding` is optional — store it later when we add semantic search
    over conversation history.
    """
    msg = Message(
        conversation_id=conversation_id,
        role=role,
        content=content,
        extra_meta=extra_meta,
        embedding=embedding,
    )
    db.add(msg)
    db.flush()  # populate msg.id and msg.created_at

    # Bump last_message_at on the conversation. We use Python-side `now()`
    # rather than `func.now()` so the value is set immediately and visible
    # in the current transaction without a SELECT roundtrip.
    conversation = db.get(Conversation, conversation_id)
    if conversation is not None:
        conversation.last_message_at = datetime.now(timezone.utc)
        db.flush()

    return msg


def get_recent_messages(
    db: Session,
    conversation_id: UUID,
    limit: int = 10,
) -> list[Message]:
    """Return the last `limit` messages in chronological order (oldest first).

    Default of 10 matches the LLM history window used in main.py (last 6 turns
    of user+assistant pairs = 12 entries, but a 10-window is fine because the
    LLM also re-sees the system prompt + RAG context every turn).
    """
    rows = db.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(desc(Message.created_at))
        .limit(limit)
    ).scalars().all()
    return list(reversed(rows))


def mark_handoff_done(db: Session, conversation_id: UUID) -> Conversation:
    """Flip conversation.handoff_done = True and status -> HANDED_OFF.

    Used by main.py /chat after send_telegram_brief() succeeds, so that
    subsequent messages on the same conversation don't re-fire the brief.
    """
    conversation = db.get(Conversation, conversation_id)
    if conversation is None:
        raise ValueError(f"Conversation {conversation_id} not found")
    conversation.handoff_done = True
    conversation.status = ConversationStatusEnum.HANDED_OFF.value
    db.flush()
    return conversation


# ============================================================================
# Operator takeover (Phase B) — Telegram forum-supergroup integration
# ============================================================================


def link_forum_topic(
    db: Session,
    conversation_id: UUID,
    topic_id: int,
) -> Conversation:
    """Record the Telegram forum topic id we created for this conversation.

    The topic_id is the `message_thread_id` from createForumTopic Bot API
    response. Once set, every subsequent user message + bot reply gets
    mirrored into that topic for the operator team to read.
    """
    conversation = db.get(Conversation, conversation_id)
    if conversation is None:
        raise ValueError(f"Conversation {conversation_id} not found")
    conversation.forum_topic_id = topic_id
    db.flush()
    return conversation


def find_conversation_by_topic(
    db: Session,
    topic_id: int,
) -> Optional[Conversation]:
    """Reverse lookup: operator wrote in a topic — which conversation is it?

    Used by /webhooks/telegram when chat.type == 'supergroup' and there's
    a message_thread_id: we need to find which lead this thread belongs to
    so we can forward the operator's message back to that lead.

    Returns None if no conversation is linked to that topic (e.g. operator
    wrote in a stale archived topic — bot should ignore).
    """
    return db.execute(
        select(Conversation).where(Conversation.forum_topic_id == topic_id)
    ).scalar_one_or_none()


def set_operator_takeover(
    db: Session,
    conversation_id: UUID,
    enabled: bool,
) -> Conversation:
    """Toggle the takeover flag.

    When True: the next /message call will skip the LLM entirely. The
    operator must reply manually in the forum topic — the bot only forwards
    those replies to the lead. When False: bot resumes autonomous replies.
    """
    conversation = db.get(Conversation, conversation_id)
    if conversation is None:
        raise ValueError(f"Conversation {conversation_id} not found")
    conversation.operator_takeover = enabled
    db.flush()
    return conversation
