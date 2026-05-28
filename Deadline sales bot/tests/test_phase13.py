"""Phase 13 tests — returning lead memory + topic-driven branching.

Naming convention: each test starts with the scenario, then the expectation.
"""
import uuid
from datetime import datetime, timedelta, timezone

from db.models import Customer, Conversation, Message, ConversationStatusEnum
from services.returning_lead import (
    should_trigger_recall,
    RECALL_MIN_GAP_DAYS,
    RECALL_MIN_LEAD_MESSAGES,
)


def _make_customer(db, email="ret@example.com"):
    c = Customer(email=email)
    db.add(c)
    db.flush()
    return c


def _make_conv(db, customer, *, status="open", days_since_last=0, n_lead_msgs=0):
    conv = Conversation(
        customer_id=customer.id,
        channel="website",
        channel_conversation_id=f"sess_{uuid.uuid4().hex[:8]}",
        status=status,
        last_message_at=datetime.now(timezone.utc) - timedelta(days=days_since_last),
    )
    db.add(conv)
    db.flush()
    for i in range(n_lead_msgs):
        m = Message(
            conversation_id=conv.id,
            role="user",
            content=f"lead msg {i}",
            created_at=datetime.now(timezone.utc) - timedelta(days=days_since_last, minutes=i),
        )
        db.add(m)
    db.flush()
    return conv


def test_should_trigger_recall_returning_with_long_gap_and_enough_msgs(db):
    customer = _make_customer(db)
    _make_conv(db, customer, days_since_last=30, n_lead_msgs=5)
    db.flush()
    assert should_trigger_recall(db, customer.id) is True


def test_should_trigger_recall_returning_but_short_gap(db):
    customer = _make_customer(db)
    _make_conv(db, customer, days_since_last=3, n_lead_msgs=10)
    db.flush()
    assert should_trigger_recall(db, customer.id) is False


def test_should_trigger_recall_returning_long_gap_but_thin_history(db):
    customer = _make_customer(db)
    _make_conv(db, customer, days_since_last=30, n_lead_msgs=1)
    db.flush()
    assert should_trigger_recall(db, customer.id) is False


def test_should_trigger_recall_no_prior_conversations(db):
    customer = _make_customer(db)
    db.flush()
    assert should_trigger_recall(db, customer.id) is False


def test_should_trigger_recall_boundary_exactly_14_days(db):
    """Exactly at the 14-day threshold should still trigger (filter is <=)."""
    customer = _make_customer(db, email="boundary14@example.com")
    _make_conv(db, customer, days_since_last=14, n_lead_msgs=3)
    db.flush()
    assert should_trigger_recall(db, customer.id) is True


def test_should_trigger_recall_just_inside_13_days(db):
    """One day shy of the threshold — should NOT trigger."""
    customer = _make_customer(db, email="boundary13@example.com")
    _make_conv(db, customer, days_since_last=13, n_lead_msgs=3)
    db.flush()
    assert should_trigger_recall(db, customer.id) is False
