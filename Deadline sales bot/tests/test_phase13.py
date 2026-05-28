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


def test_archive_stale_flips_status_and_stamps_archived_at(db):
    """Returns count of conversations archived. New conv (except_id)
    is left alone."""
    from services.returning_lead import archive_stale_conversations

    customer = _make_customer(db, email="stale@example.com")
    old_conv = _make_conv(db, customer, days_since_last=60, n_lead_msgs=4)
    new_conv = _make_conv(db, customer, days_since_last=0, n_lead_msgs=0)
    db.flush()

    n = archive_stale_conversations(db, customer.id, except_conv_id=new_conv.id)
    db.flush()

    db.refresh(old_conv)
    db.refresh(new_conv)
    assert n == 1
    assert old_conv.status == ConversationStatusEnum.ARCHIVED.value
    assert old_conv.archived_at is not None
    assert new_conv.status == ConversationStatusEnum.OPEN.value
    assert new_conv.archived_at is None


def test_archive_stale_skips_already_archived(db):
    """Already-archived conversations should not be touched, count not incremented."""
    from services.returning_lead import archive_stale_conversations

    customer = _make_customer(db, email="already_arch@example.com")
    already_arch = _make_conv(db, customer, status="archived", days_since_last=90, n_lead_msgs=2)
    open_conv = _make_conv(db, customer, days_since_last=10, n_lead_msgs=2)
    db.flush()

    n = archive_stale_conversations(db, customer.id)
    db.flush()

    db.refresh(already_arch)
    db.refresh(open_conv)
    assert n == 1  # only the open one
    assert open_conv.status == ConversationStatusEnum.ARCHIVED.value


def test_archive_stale_no_op_when_no_open_conversations(db):
    from services.returning_lead import archive_stale_conversations
    customer = _make_customer(db, email="nothing_to_archive@example.com")
    db.flush()
    n = archive_stale_conversations(db, customer.id)
    assert n == 0


def test_get_recent_messages_with_recall_merges_prior_and_current(db):
    """When customer has an archived prior conversation and a current
    active one, the helper returns last 5 prior + last 8 current,
    chronologically merged."""
    from services.conversations import get_recent_messages_with_recall

    customer = _make_customer(db, email="merge@example.com")

    # Prior: archived conv with 10 messages
    prior = _make_conv(db, customer, status="archived", days_since_last=30, n_lead_msgs=0)
    for i in range(10):
        m = Message(
            conversation_id=prior.id,
            role="user" if i % 2 == 0 else "assistant",
            content=f"prior msg {i}",
            created_at=datetime.now(timezone.utc) - timedelta(days=30, minutes=10 - i),
        )
        db.add(m)

    # Current: open conv with 3 messages
    current = _make_conv(db, customer, days_since_last=0, n_lead_msgs=0)
    for i in range(3):
        m = Message(
            conversation_id=current.id,
            role="user" if i % 2 == 0 else "assistant",
            content=f"current msg {i}",
            created_at=datetime.now(timezone.utc) - timedelta(minutes=3 - i),
        )
        db.add(m)
    db.flush()

    result = get_recent_messages_with_recall(
        db, customer_id=customer.id, active_conv_id=current.id, limit=13, prior_tail=5
    )

    # Up to 5 from prior tail + 3 from current = 8 total
    assert 7 <= len(result) <= 8
    # The current-conv messages are the LAST 3 (chronologically newest).
    last_three = result[-3:]
    contents_last = [
        (m["content"] if isinstance(m, dict) else m.content)
        for m in last_three
    ]
    assert all("current msg" in c for c in contents_last)


def test_get_recent_messages_with_recall_no_prior_returns_only_current(db):
    """Customer with only the active conversation — helper returns
    same result as the regular get_recent_messages."""
    from services.conversations import get_recent_messages_with_recall, get_recent_messages

    customer = _make_customer(db, email="lonely@example.com")
    current = _make_conv(db, customer, days_since_last=0, n_lead_msgs=0)
    for i in range(3):
        m = Message(
            conversation_id=current.id,
            role="user",
            content=f"msg {i}",
            created_at=datetime.now(timezone.utc) - timedelta(minutes=3 - i),
        )
        db.add(m)
    db.flush()

    via_recall = get_recent_messages_with_recall(
        db, customer_id=customer.id, active_conv_id=current.id, limit=13
    )
    via_plain = get_recent_messages(db, current.id, limit=13)
    # Both should have same length (and ideally same content, but at least same count)
    assert len(via_recall) == len(via_plain)


def test_forum_topic_prefix_returning_customer(db):
    """When customer has an archived prior conv → topic title gets [ПОВТОРНЫЙ]
    + summary tag."""
    from channels.telegram import build_forum_topic_name

    customer = _make_customer(db, email="ret-prefix@example.com")
    _make_conv(db, customer, status="archived", days_since_last=60, n_lead_msgs=4)
    active = _make_conv(db, customer, days_since_last=0, n_lead_msgs=1)
    active.summary = "сайт спа, 200к"
    db.flush()

    name = build_forum_topic_name(db, customer, active, lead_name="Иван С.", channel="website")
    assert name.startswith("[ПОВТОРНЫЙ]")
    assert "Иван С." in name
    assert "сайт спа" in name


def test_forum_topic_name_first_time_lead_no_prefix(db):
    """Fresh customer, no archived prior, no summary → plain title."""
    from channels.telegram import build_forum_topic_name

    customer = _make_customer(db, email="firsttime@example.com")
    active = _make_conv(db, customer, days_since_last=0, n_lead_msgs=1)
    db.flush()

    name = build_forum_topic_name(db, customer, active, lead_name="Пётр", channel="telegram")
    assert "[ПОВТОРНЫЙ]" not in name
    assert name == "Пётр · telegram"


def test_forum_topic_prefix_for_handed_off_prior(db):
    """Most common returning-lead case: prior conv is HANDED_OFF (handoff
    fired when email captured). Title should still get [ПОВТОРНЫЙ] prefix.

    Regression test for P13.T15 Bug 3: original filter was ARCHIVED-only,
    so HANDED_OFF priors were silently skipped and the prefix never appeared.
    """
    from channels.telegram import build_forum_topic_name

    customer = _make_customer(db, email="handed_off_returnee@example.com")
    _make_conv(db, customer, status="handed_off", days_since_last=45, n_lead_msgs=5)
    active = _make_conv(db, customer, days_since_last=0, n_lead_msgs=1)
    db.flush()

    name = build_forum_topic_name(db, customer, active, lead_name="Анна", channel="website")
    assert name.startswith("[ПОВТОРНЫЙ]")


def test_get_recent_messages_with_recall_handed_off_prior(db):
    """CONTINUE branch: prior conv is HANDED_OFF (most common case).
    get_recent_messages_with_recall must return messages from it.

    Regression test for P13.T15 Bug 2: original filter was ARCHIVED-only,
    so HANDED_OFF priors produced an empty recall and the CONTINUE branch
    delivered no memory despite promising it.
    """
    from services.conversations import get_recent_messages_with_recall

    customer = _make_customer(db, email="recall_handed_off@example.com")

    prior = _make_conv(db, customer, status="handed_off", days_since_last=30, n_lead_msgs=0)
    for i in range(6):
        m = Message(
            conversation_id=prior.id,
            role="user" if i % 2 == 0 else "assistant",
            content=f"prior msg {i}",
            created_at=datetime.now(timezone.utc) - timedelta(days=30, minutes=6 - i),
        )
        db.add(m)

    current = _make_conv(db, customer, days_since_last=0, n_lead_msgs=0)
    for i in range(2):
        m = Message(
            conversation_id=current.id,
            role="user",
            content=f"current msg {i}",
            created_at=datetime.now(timezone.utc) - timedelta(minutes=2 - i),
        )
        db.add(m)
    db.flush()

    result = get_recent_messages_with_recall(
        db, customer_id=customer.id, active_conv_id=current.id, limit=13, prior_tail=5
    )

    # Must include messages from the HANDED_OFF prior conv.
    contents = [
        (m["content"] if isinstance(m, dict) else m.content)
        for m in result
    ]
    assert any("prior msg" in c for c in contents), (
        "CONTINUE recall returned no messages from HANDED_OFF prior conv — filter bug not fixed"
    )
    assert any("current msg" in c for c in contents)


def test_phase13_new_branch_persists_pivot_msg_to_new_conv(db):
    """When classifier returns NEW, the user's pivoting message must end
    up on the new_conv (not stranded on the old conv) so the LLM sees it
    in subsequent turn history.

    Integration test deferred — full _handle_message wiring requires async
    test client setup beyond unit-test scope. Covered by Task 13 prod smoke.
    """
    import pytest
    pytest.skip(
        "Integration test deferred — covered by Task 13 prod smoke. "
        "Unit coverage: pivot msg re-point logic is in main.py NEW branch "
        "after `conversation = new_conv` reassignment (P13.T15 Bug 1 fix)."
    )
