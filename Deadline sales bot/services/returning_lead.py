"""Phase 13 — Returning lead memory.

Provides:
  - should_trigger_recall(db, customer_id) -> bool          (Task 3 — this file)
  - generate_topic_summary(llm, db, conversation) -> str    (Task 4 — coming next)
  - classify_topic_decision(llm, summary, recall, user_msg) -> dict  (Task 7)
  - archive_stale_conversations(db, customer_id, ...)       (Task 8)

All Phase 13 logic lives here so main.py only has thin state-machine
branches that call these helpers.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import select, func
from sqlalchemy.orm import Session

# `Customer` and `ConversationStatusEnum` are used by other Phase 13 functions
# (generate_topic_summary, archive_stale_conversations) that will land in
# Tasks 4 and 8 — kept here to avoid churning the import block per-task.
from db.models import Conversation, ConversationStatusEnum, Customer, Message  # noqa: F401

logger = logging.getLogger(__name__)

# Trigger thresholds — Section 3.1 of ADR
RECALL_MIN_GAP_DAYS = 14
RECALL_MIN_LEAD_MESSAGES = 3


def should_trigger_recall(db: Session, customer_id: UUID) -> bool:
    """Return True iff this customer has at least one prior Conversation
    where last_message_at is at least RECALL_MIN_GAP_DAYS days in the past
    AND that Conversation has at least RECALL_MIN_LEAD_MESSAGES messages
    with role='user'.

    Does NOT check the identity-merge flag — caller is expected to gate
    this on was_returning_match first to avoid querying for new leads.
    """
    threshold = datetime.now(timezone.utc) - timedelta(days=RECALL_MIN_GAP_DAYS)

    # Subquery: for each Conversation belonging to this customer, count user msgs
    user_msg_count = (
        select(Message.conversation_id, func.count().label("n"))
        .where(Message.role == "user")
        .group_by(Message.conversation_id)
        .subquery()
    )

    stmt = (
        select(Conversation.id)
        .join(user_msg_count, user_msg_count.c.conversation_id == Conversation.id)
        .where(
            Conversation.customer_id == customer_id,
            Conversation.last_message_at <= threshold,
            user_msg_count.c.n >= RECALL_MIN_LEAD_MESSAGES,
        )
        .limit(1)
    )
    row = db.execute(stmt).first()
    triggered = row is not None
    logger.info(
        "[recall] should_trigger_recall customer=%s → %s (min_gap_days=%d, min_msgs=%d)",
        customer_id, triggered, RECALL_MIN_GAP_DAYS, RECALL_MIN_LEAD_MESSAGES,
    )
    return triggered
