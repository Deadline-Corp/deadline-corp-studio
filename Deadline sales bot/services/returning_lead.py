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


def generate_topic_summary(llm, db: Session, conversation: Conversation) -> str:
    """Generate (or return cached) a 1-3 sentence summary of the project
    discussed in this conversation. Result is persisted to
    Conversation.summary so subsequent recalls don't re-spend LLM tokens.

    `llm` is a LangChain-compatible chat model (already configured with
    temperature etc. — caller's responsibility).

    Returns empty string if the conversation has no messages — caller
    should decide whether to skip the recall in that case.
    """
    if conversation.summary:
        return conversation.summary

    # Load all messages chronologically
    msgs = db.execute(
        select(Message)
        .where(Message.conversation_id == conversation.id)
        .order_by(Message.created_at.asc())
    ).scalars().all()

    if not msgs:
        return ""

    # Build a compact transcript for the prompt
    transcript_lines = []
    for m in msgs:
        role_label = {"user": "Клиент", "assistant": "Бот", "system": "Система"}.get(m.role, m.role)
        transcript_lines.append(f"{role_label}: {m.content}")
    transcript = "\n".join(transcript_lines)

    from prompts import TOPIC_SUMMARY_PROMPT
    prompt_text = TOPIC_SUMMARY_PROMPT.format(transcript=transcript)

    from langchain_core.messages import HumanMessage
    response = llm.invoke([HumanMessage(content=prompt_text)])
    summary = (response.content or "").strip()

    # Cache for next recall — caller commits/flushes
    conversation.summary = summary
    logger.info(
        "[recall] generated topic summary for conv=%s (%d msgs → %d chars)",
        conversation.id, len(msgs), len(summary),
    )
    return summary


def classify_topic_decision(llm, summary: str, recall_greeting: str, user_reply: str) -> dict:
    """Wrap the topic classifier LLM call. Returns
    {decision: CONTINUE|NEW|UNCLEAR, confidence: float, reason: str}.

    Fail-safe: parser returns UNCLEAR on any error, and any LLM exception
    (network, rate limit, timeout) is caught and also returns UNCLEAR so
    callers can route deterministically without try/except themselves.
    """
    from langchain_core.messages import HumanMessage
    from prompts import TOPIC_CLASSIFIER_PROMPT, parse_topic_decision

    prompt = TOPIC_CLASSIFIER_PROMPT.format(
        summary=summary, recall_greeting=recall_greeting, user_reply=user_reply
    )
    try:
        response = llm.invoke([HumanMessage(content=prompt)])
        result = parse_topic_decision(response.content or "")
    except Exception as exc:
        logger.warning("[recall] classifier failed: %s", exc)
        result = {"decision": "UNCLEAR", "confidence": 0.0, "reason": f"llm error: {exc}"}

    logger.info(
        "[recall] topic classifier → decision=%s confidence=%.2f reason=%s",
        result["decision"], result["confidence"], result["reason"][:80],
    )
    return result
