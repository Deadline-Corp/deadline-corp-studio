"""Tests for Phase 6 modules: warming.py + pause_strategy.py + triggers.py."""

from datetime import datetime, timedelta, timezone

import pytest

from services.warming import WarmingAction, plan_warming, should_warm_now
from services.pause_strategy import (
    DEFAULT_TIMINGS,
    NextAttempt,
    classify_pause,
    compute_next_attempt,
    get_timings,
)
from services.triggers import (
    EscalationTrigger,
    check_all_triggers,
    check_dialog_loop,
    check_explicit_handoff,
    check_large_deal,
    check_legal_keywords,
    check_low_confidence,
    check_silence_after_24h,
    check_two_negatives_in_row,
)


# Realistic warming config from tenants/deadline-corp/config.yaml
WARMING_CFG = {
    "hot":    {"silence_max_d": 14,   "cadence_d": 1, "formats": ["direct_followup"]},
    "warm":   {"silence_max_d": 60,   "cadence_d": 7, "formats": ["case_study", "content"]},
    "cold":   {"silence_max_d": 180,  "cadence_d": 21, "formats": ["content", "offer"]},
    "frozen": {"silence_max_d": 9999, "cadence_d": 90, "formats": ["special_offer", "archive"]},
}


# ============================================================================
# warming.py
# ============================================================================

class TestShouldWarmNow:
    def test_hot_lead_quick_cadence(self):
        ok, fmt, _ = should_warm_now(
            current_temperature="hot",
            silent_days=2,
            last_warmed_days_ago=None,
            config_warming=WARMING_CFG,
        )
        assert ok
        assert fmt == "direct_followup"

    def test_warm_cadence_respected(self):
        # warmed 3 days ago, cadence is 7d → too early
        ok, _, _ = should_warm_now(
            current_temperature="warm",
            silent_days=10,
            last_warmed_days_ago=3,
            config_warming=WARMING_CFG,
        )
        assert not ok

    def test_warm_cadence_passed(self):
        ok, fmt, _ = should_warm_now(
            current_temperature="warm",
            silent_days=15,
            last_warmed_days_ago=8,
            config_warming=WARMING_CFG,
        )
        assert ok
        assert fmt == "case_study"

    def test_past_bucket_max_skipped(self):
        # 100 days silent in 'warm' bucket (max 60d) — should have decayed
        ok, _, reason = should_warm_now(
            current_temperature="warm",
            silent_days=100,
            last_warmed_days_ago=None,
            config_warming=WARMING_CFG,
        )
        assert not ok
        assert "decayed" in reason

    def test_unknown_temperature_skipped(self):
        ok, _, _ = should_warm_now(
            current_temperature="lukewarm",
            silent_days=10,
            last_warmed_days_ago=None,
            config_warming=WARMING_CFG,
        )
        assert not ok


class TestPlanWarming:
    def test_returns_action_when_warm_due(self):
        a = plan_warming(
            customer_id="cust-1",
            current_temperature="hot",
            silent_days=2,
            last_warmed_days_ago=None,
            config_warming=WARMING_CFG,
        )
        assert isinstance(a, WarmingAction)
        assert a.customer_id == "cust-1"
        assert a.format == "direct_followup"
        assert a.temperature == "hot"

    def test_none_when_not_due(self):
        a = plan_warming(
            customer_id="cust-1",
            current_temperature="warm",
            silent_days=5,
            last_warmed_days_ago=1,  # too soon
            config_warming=WARMING_CFG,
        )
        assert a is None


# ============================================================================
# pause_strategy.py
# ============================================================================

class TestClassifyPause:
    def test_operator_pause_wins(self):
        assert classify_pause(
            last_lead_message="busy later",
            operator_paused=True,
            has_named_date=True,
        ) == "operator_pause"

    def test_named_date(self):
        assert classify_pause(
            has_named_date=True,
        ) == "named_date"

    def test_busy_later_ru(self):
        assert classify_pause(last_lead_message="занят, напишу позже") == "busy_later"

    def test_busy_later_en(self):
        assert classify_pause(last_lead_message="too busy now") == "busy_later"

    def test_awaiting_our_info(self):
        assert classify_pause(we_promised_info=True) == "awaiting_our_info"

    def test_unanswered_question(self):
        assert classify_pause(we_asked_question_last=True) == "unanswered_question"

    def test_default_unexplained(self):
        assert classify_pause() == "unexplained"
        assert classify_pause(last_lead_message="ок") == "unexplained"


class TestComputeNextAttempt:
    base_time = datetime(2026, 5, 26, 12, 0, tzinfo=timezone.utc)

    def test_unexplained_first_attempt_4h(self):
        result = compute_next_attempt(
            pause_type="unexplained",
            attempt_number=1,
            last_attempt_at=self.base_time,
        )
        assert result is not None
        assert result.due_at == self.base_time + timedelta(hours=4)
        assert not result.angle_change
        assert not result.channel_switch

    def test_unexplained_second_angle_change(self):
        result = compute_next_attempt(
            pause_type="unexplained",
            attempt_number=2,
            last_attempt_at=self.base_time,
        )
        # second has default 24h (1d) from DEFAULT_TIMINGS
        assert result is not None
        assert result.angle_change
        assert not result.channel_switch

    def test_unexplained_third_channel_switch(self):
        result = compute_next_attempt(
            pause_type="unexplained",
            attempt_number=3,
            last_attempt_at=self.base_time,
        )
        assert result is not None
        assert result.angle_change
        assert result.channel_switch

    def test_busy_later_angle_change_immediate(self):
        result = compute_next_attempt(
            pause_type="busy_later",
            attempt_number=1,
            last_attempt_at=self.base_time,
        )
        assert result is not None
        assert result.angle_change

    def test_operator_pause_waits(self):
        result = compute_next_attempt(
            pause_type="operator_pause",
            attempt_number=1,
            last_attempt_at=self.base_time,
        )
        assert result is not None
        assert result.due_at is None

    def test_named_date_uses_provided(self):
        when = self.base_time + timedelta(days=7)
        result = compute_next_attempt(
            pause_type="named_date",
            attempt_number=1,
            last_attempt_at=self.base_time,
            named_date=when,
        )
        assert result is not None
        assert result.due_at == when

    def test_named_date_without_provided_is_none(self):
        assert compute_next_attempt(
            pause_type="named_date",
            attempt_number=1,
            last_attempt_at=self.base_time,
        ) is None

    def test_attempts_exhausted(self):
        # unanswered_question has only 1 attempt configured
        result = compute_next_attempt(
            pause_type="unanswered_question",
            attempt_number=2,
            last_attempt_at=self.base_time,
        )
        assert result is None


# ============================================================================
# triggers.py
# ============================================================================

class TestLowConfidence:
    def test_below_gate(self):
        t = check_low_confidence(0.5, gate=0.7)
        assert t is not None and t.severity == "warning"

    def test_above_gate(self):
        assert check_low_confidence(0.9, gate=0.7) is None

    def test_none_no_trigger(self):
        assert check_low_confidence(None) is None


class TestLegalKeywords:
    def test_ru_match(self):
        t = check_legal_keywords("давайте обсудим договор")
        assert t is not None and t.type == "legal_keywords" and t.severity == "alert"

    def test_en_match(self):
        t = check_legal_keywords("send me the contract")
        assert t is not None

    def test_no_match(self):
        assert check_legal_keywords("привет") is None
        assert check_legal_keywords("") is None
        assert check_legal_keywords(None) is None


class TestExplicitHandoff:
    def test_ru_request(self):
        t = check_explicit_handoff("позови менеджера пожалуйста")
        assert t is not None and t.severity == "alert"

    def test_en_request(self):
        t = check_explicit_handoff("can I talk to a human?")
        assert t is not None

    def test_no_request(self):
        assert check_explicit_handoff("у меня вопрос") is None


class TestTwoNegativesInRow:
    def test_both_negative(self):
        t = check_two_negatives_in_row(["слишком дорого", "не подходит"])
        assert t is not None

    def test_only_last_negative(self):
        assert check_two_negatives_in_row(["норм", "не подходит"]) is None

    def test_one_message(self):
        assert check_two_negatives_in_row(["дорого"]) is None


class TestLargeDeal:
    def test_above_threshold(self):
        t = check_large_deal(2_000_000, threshold_rub=1_000_000)
        assert t is not None and t.severity == "alert"

    def test_below_threshold(self):
        assert check_large_deal(500_000, threshold_rub=1_000_000) is None

    def test_none(self):
        assert check_large_deal(None) is None


class TestDialogLoop:
    def test_three_same_prefix(self):
        # ASCII-only and >30 shared chars to match the detector's 30-char prefix rule
        # "This is the exact same prefix " = 30 chars
        replies = [
            "This is the exact same prefix hello",
            "This is the exact same prefix world",
            "This is the exact same prefix yo",
        ]
        t = check_dialog_loop(replies, similarity_threshold=3)
        assert t is not None

    def test_different_prefixes(self):
        replies = ["Привет, как дела?", "Понятно, опишите задачу", "Скиньте email"]
        assert check_dialog_loop(replies, similarity_threshold=3) is None

    def test_too_few(self):
        assert check_dialog_loop(["a", "b"], similarity_threshold=3) is None


class TestSilenceAfter24h:
    def test_24h(self):
        assert check_silence_after_24h(24.0) is not None

    def test_under_24h(self):
        assert check_silence_after_24h(23.9) is None


class TestCheckAllTriggers:
    def test_multiple_fire(self):
        triggers = check_all_triggers(
            confidence=0.4,                     # low_confidence
            message_text="договор пожалуйста",  # legal_keywords
            silent_hours=30,                    # silence_after_24h
        )
        types = {t.type for t in triggers}
        assert "low_confidence" in types
        assert "legal_keywords" in types
        assert "silence_after_24h" in types
        assert len(triggers) == 3

    def test_none_fire(self):
        assert check_all_triggers(
            confidence=0.9,
            message_text="привет",
            silent_hours=2,
        ) == []

    def test_handoff_request_alert(self):
        triggers = check_all_triggers(message_text="can I speak to manager please?")
        assert any(t.type == "explicit_handoff_request" for t in triggers)
