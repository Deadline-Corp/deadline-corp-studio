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


def test_generate_topic_summary_calls_llm_and_caches(db):
    """generate_topic_summary should:
      - read messages from the given conversation
      - call the LLM with TOPIC_SUMMARY_PROMPT
      - persist result to Conversation.summary
      - return the same string on subsequent calls without re-calling LLM
    """
    from unittest.mock import MagicMock
    from services.returning_lead import generate_topic_summary

    customer = _make_customer(db, email="topic_summary@example.com")
    conv = _make_conv(db, customer, days_since_last=20, n_lead_msgs=4)
    db.flush()

    fake_llm = MagicMock()
    fake_llm.invoke.return_value = MagicMock(content="спа-сайт, бюджет 200к, остановились на ТЗ")

    summary1 = generate_topic_summary(fake_llm, db, conv)
    db.flush()
    assert summary1 == "спа-сайт, бюджет 200к, остановились на ТЗ"
    assert fake_llm.invoke.call_count == 1

    # Second call should not hit LLM — read from cache
    summary2 = generate_topic_summary(fake_llm, db, conv)
    assert summary2 == summary1
    assert fake_llm.invoke.call_count == 1  # unchanged


def test_generate_topic_summary_empty_conversation_returns_empty_string(db):
    """Edge case: conversation row exists but has no messages.
    Should return empty string (don't waste LLM call, caller decides skip)."""
    from unittest.mock import MagicMock
    from services.returning_lead import generate_topic_summary

    customer = _make_customer(db, email="empty_summary@example.com")
    conv = _make_conv(db, customer, days_since_last=20, n_lead_msgs=0)
    db.flush()

    fake_llm = MagicMock()
    summary = generate_topic_summary(fake_llm, db, conv)
    assert summary == ""
    assert fake_llm.invoke.call_count == 0


def test_parse_topic_decision_continue():
    from prompts import parse_topic_decision
    raw = '{"decision": "CONTINUE", "confidence": 0.82, "reason": "те же темы"}'
    result = parse_topic_decision(raw)
    assert result == {"decision": "CONTINUE", "confidence": 0.82, "reason": "те же темы"}


def test_parse_topic_decision_handles_code_fences():
    from prompts import parse_topic_decision
    raw = '```json\n{"decision": "NEW", "confidence": 0.9, "reason": "другой бюджет"}\n```'
    result = parse_topic_decision(raw)
    assert result["decision"] == "NEW"
    assert result["confidence"] == 0.9


def test_parse_topic_decision_garbage_returns_unclear():
    from prompts import parse_topic_decision
    result = parse_topic_decision("я не уверен что это json")
    assert result["decision"] == "UNCLEAR"
    assert result["confidence"] == 0.0


def test_parse_topic_decision_empty_returns_unclear():
    from prompts import parse_topic_decision
    result = parse_topic_decision("")
    assert result["decision"] == "UNCLEAR"
    assert result["confidence"] == 0.0


def test_parse_topic_decision_invalid_decision_normalizes_to_unclear():
    """Even if JSON parses, if decision string is not in
    {CONTINUE, NEW, UNCLEAR}, normalize to UNCLEAR."""
    from prompts import parse_topic_decision
    raw = '{"decision": "MAYBE", "confidence": 0.5, "reason": "x"}'
    result = parse_topic_decision(raw)
    assert result["decision"] == "UNCLEAR"


def test_parse_topic_decision_clamps_confidence_to_unit_range():
    from prompts import parse_topic_decision
    raw = '{"decision": "CONTINUE", "confidence": 1.5, "reason": "x"}'
    result = parse_topic_decision(raw)
    assert result["confidence"] == 1.0

    raw2 = '{"decision": "NEW", "confidence": -0.3, "reason": "x"}'
    result2 = parse_topic_decision(raw2)
    assert result2["confidence"] == 0.0


def test_classify_topic_decision_routes_via_llm():
    """classify_topic_decision is a thin wrapper — it builds the prompt
    via TOPIC_CLASSIFIER_PROMPT, calls the LLM, and parses the response."""
    from unittest.mock import MagicMock
    from services.returning_lead import classify_topic_decision

    fake_llm = MagicMock()
    fake_llm.invoke.return_value = MagicMock(
        content='{"decision": "NEW", "confidence": 0.88, "reason": "другой проект"}'
    )

    result = classify_topic_decision(
        fake_llm,
        summary="сайт спа, 200к",
        recall_greeting="Помню, спа-сайт. Продолжим?",
        user_reply="хочу telegram бот",
    )
    assert result["decision"] == "NEW"
    assert result["confidence"] == 0.88
    assert fake_llm.invoke.call_count == 1


def test_classify_topic_decision_falls_back_on_garbage():
    from unittest.mock import MagicMock
    from services.returning_lead import classify_topic_decision

    fake_llm = MagicMock()
    fake_llm.invoke.return_value = MagicMock(content="мнэ, я не понял")
    result = classify_topic_decision(fake_llm, "x", "y", "z")
    assert result["decision"] == "UNCLEAR"
    assert result["confidence"] == 0.0


def test_classify_topic_decision_handles_llm_exception():
    """If the LLM raises (network error, rate limit) — return UNCLEAR
    with confidence=0 so main.py can route to explicit clarification."""
    from unittest.mock import MagicMock
    from services.returning_lead import classify_topic_decision

    fake_llm = MagicMock()
    fake_llm.invoke.side_effect = RuntimeError("network down")
    result = classify_topic_decision(fake_llm, "x", "y", "z")
    assert result["decision"] == "UNCLEAR"
    assert result["confidence"] == 0.0
    assert "network down" in result["reason"] or "llm error" in result["reason"]
