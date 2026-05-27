"""Tests for Phase 9 modules: lead_signals + escalation."""

from dataclasses import dataclass
from typing import Optional

import pytest

from services.lead_signals import SignalUpdate, apply_signals_on_turn
from services.escalation import (
    RATE_LIMIT_WINDOW_SEC,
    format_alert_text,
    reset_rate_limit,
    run_escalation_checks,
)


# Realistic tenant config slice
TENANT_CFG = {
    "scoring": {
        "base_by_interaction_type": {
            "P1": 100, "P2": 60, "P3": 50, "P4": 30, "P5": 20, "P6": 10, "HardStop": 0,
        },
        "content_keywords": {
            "бюджет": 20, "дедлайн": 20, "тз": 15, "проект": 10,
            "budget": 20, "deadline": 20, "project": 10,
        },
        "source_weight": {
            "telegram": 1.0, "website": 1.0, "instagram": 0.8,
        },
        "decay_per_48h": -1,
    },
    "temperature": {
        "decay_days": 14,
        "frozen_after_days": 21,
        "triggers": {
            "warm": {"content_replies_count": 2},
            "hot": {"keywords": ["price", "timeline", "portfolio"]},
            "ready": {"phrases": ["ready to begin", "let's start"]},
        },
    },
    "operator_mode": {"confidence_gate": 0.7},
    "discount": {"auto_above_budget_threshold": 1_000_000},
}


# Lightweight stand-ins for ORM rows (only fields we read)
@dataclass
class FakeRole:
    value: str


@dataclass
class FakeMessage:
    role: FakeRole
    content: str = ""

    def __init__(self, role_str: str, content: str = ""):
        self.role = FakeRole(role_str)
        self.content = content


@dataclass
class FakeCustomer:
    email: Optional[str] = None
    interaction_type: Optional[str] = None
    lead_score: int = 0
    lead_temperature: Optional[str] = None


# ============================================================================
# lead_signals.py
# ============================================================================

class TestApplySignalsOnTurnFirstTouch:
    def test_first_touch_detects_p2_default(self):
        c = FakeCustomer()
        recent = [FakeMessage("user", "hi")]
        update = apply_signals_on_turn(
            customer=c,
            recent_messages=recent,
            lead_message_text="hi",
            channel="telegram",
            message_type="dm",
            tenant_config=TENANT_CFG,
        )
        assert update.is_first_touch
        assert update.interaction_type == "P2"
        assert c.interaction_type == "P2"
        assert c.lead_score == 60  # base P2 * 1.0 telegram
        assert update.new_score == 60

    def test_first_touch_p1_with_explicit_request(self):
        c = FakeCustomer()
        recent = [FakeMessage("user", "I want to develop an AI agent")]
        update = apply_signals_on_turn(
            customer=c, recent_messages=recent,
            lead_message_text="I want to develop an AI agent",
            channel="telegram", message_type="dm",
            tenant_config=TENANT_CFG,
        )
        assert update.interaction_type == "P1"
        assert c.lead_score == 100  # P1 base, no keywords matched

    def test_first_touch_with_budget_keyword_adds_score(self):
        c = FakeCustomer()
        recent = [FakeMessage("user", "looking for a project, budget 5k")]
        update = apply_signals_on_turn(
            customer=c, recent_messages=recent,
            lead_message_text="looking for a project, budget 5k",
            channel="website", message_type="dm",
            tenant_config=TENANT_CFG,
        )
        # P1 (explicit "looking for") + budget 20 + project 10 = 130 * 1.0
        assert c.lead_score >= 130
        assert "budget" in update.matched_keywords
        assert "project" in update.matched_keywords

    def test_first_touch_hard_stop_detected(self):
        c = FakeCustomer()
        recent = [FakeMessage("user", "please unsubscribe me")]
        update = apply_signals_on_turn(
            customer=c, recent_messages=recent,
            lead_message_text="please unsubscribe me",
            channel="telegram", message_type="dm",
            tenant_config=TENANT_CFG,
        )
        assert update.interaction_type == "HardStop"
        assert c.lead_score == 0  # HardStop base is 0

    def test_first_touch_comment_mode_p4(self):
        c = FakeCustomer()
        recent = [FakeMessage("user", "nice post!")]
        update = apply_signals_on_turn(
            customer=c, recent_messages=recent,
            lead_message_text="nice post!",
            channel="instagram", message_type="comment",
            tenant_config=TENANT_CFG,
        )
        assert update.interaction_type == "P4"


class TestApplySignalsOnTurnIncremental:
    def test_returning_lead_adds_score(self):
        c = FakeCustomer(interaction_type="P2", lead_score=60, lead_temperature="cold")
        recent = [
            FakeMessage("user", "hi"),
            FakeMessage("assistant", "reply 1"),
            FakeMessage("user", "interested in your project work, budget 10k"),
        ]
        update = apply_signals_on_turn(
            customer=c, recent_messages=recent,
            lead_message_text="interested in your project work, budget 10k",
            channel="telegram", message_type="dm",
            tenant_config=TENANT_CFG,
        )
        assert not update.is_first_touch
        # +budget 20, +project 10 = +30 * 1.0 = +30 → 90
        assert c.lead_score == 90
        # interaction_type unchanged (set once at first touch)
        assert c.interaction_type == "P2"

    def test_returning_lead_temperature_warm_after_2_messages(self):
        c = FakeCustomer(interaction_type="P2", lead_score=60, lead_temperature="cold")
        recent = [FakeMessage("user", "hi"), FakeMessage("user", "hello")]
        update = apply_signals_on_turn(
            customer=c, recent_messages=recent,
            lead_message_text="hello",
            channel="telegram", message_type="dm",
            tenant_config=TENANT_CFG,
        )
        assert c.lead_temperature == "warm"
        assert update.new_temperature == "warm"

    def test_hot_keyword_jumps_temperature(self):
        c = FakeCustomer(interaction_type="P2", lead_score=60, lead_temperature="cold")
        recent = [FakeMessage("user", "hi"), FakeMessage("user", "what's the price?")]
        update = apply_signals_on_turn(
            customer=c, recent_messages=recent,
            lead_message_text="what's the price?",
            channel="telegram", message_type="dm",
            tenant_config=TENANT_CFG,
        )
        assert c.lead_temperature == "hot"

    def test_ready_phrase_top_temperature(self):
        c = FakeCustomer(interaction_type="P2", lead_score=80, lead_temperature="hot")
        recent = [
            FakeMessage("user", "hi"),
            FakeMessage("user", "ok let's start I am ready to begin work"),
        ]
        update = apply_signals_on_turn(
            customer=c, recent_messages=recent,
            lead_message_text="ok let's start I am ready to begin work",
            channel="telegram", message_type="dm",
            tenant_config=TENANT_CFG,
        )
        assert c.lead_temperature == "ready"


# ============================================================================
# escalation.py
# ============================================================================

class TestEscalation:
    def setup_method(self):
        reset_rate_limit()

    def test_legal_keyword_alert(self):
        out = run_escalation_checks(
            conversation_id="conv-1",
            message_text="please send me the contract",
            tenant_config=TENANT_CFG,
        )
        assert any(t.type == "legal_keywords" for t in out)
        assert any(t.severity == "alert" for t in out)

    def test_explicit_handoff_alert(self):
        out = run_escalation_checks(
            conversation_id="conv-2",
            message_text="can I talk to a human?",
            tenant_config=TENANT_CFG,
        )
        assert any(t.type == "explicit_handoff_request" for t in out)

    def test_rate_limit_suppresses_repeat(self):
        run_escalation_checks(
            conversation_id="conv-3",
            message_text="send the contract",
            tenant_config=TENANT_CFG,
            now=1000.0,
        )
        # Same conv, same trigger, 5 seconds later → rate-limited
        out = run_escalation_checks(
            conversation_id="conv-3",
            message_text="send the contract again",
            tenant_config=TENANT_CFG,
            now=1005.0,
        )
        assert not any(t.type == "legal_keywords" for t in out)

    def test_rate_limit_passes_after_window(self):
        run_escalation_checks(
            conversation_id="conv-4",
            message_text="send the contract",
            tenant_config=TENANT_CFG,
            now=1000.0,
        )
        out = run_escalation_checks(
            conversation_id="conv-4",
            message_text="contract please",
            tenant_config=TENANT_CFG,
            now=1000.0 + RATE_LIMIT_WINDOW_SEC + 1,
        )
        assert any(t.type == "legal_keywords" for t in out)

    def test_different_conv_independent(self):
        run_escalation_checks(
            conversation_id="conv-a",
            message_text="contract",
            tenant_config=TENANT_CFG,
            now=1000.0,
        )
        out = run_escalation_checks(
            conversation_id="conv-b",
            message_text="contract",
            tenant_config=TENANT_CFG,
            now=1001.0,
        )
        assert any(t.type == "legal_keywords" for t in out)

    def test_format_alert_text(self):
        from services.triggers import EscalationTrigger
        t = EscalationTrigger(type="legal_keywords", severity="alert", reason="x")
        s = format_alert_text(t)
        assert "ESCALATION" in s
        assert "legal_keywords" in s

    def test_no_triggers_returns_empty(self):
        out = run_escalation_checks(
            conversation_id="conv-clean",
            message_text="just chatting normally",
            tenant_config=TENANT_CFG,
        )
        assert out == []
