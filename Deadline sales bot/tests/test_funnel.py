"""Tests for services/funnel.py — lead funnel state machine.

These are pure-function tests; no DB, no CRM, no fixtures. Run with:
    pytest tests/test_funnel.py
"""

import pytest

from services.funnel import (
    ACTIVE_STAGES,
    ALL_STAGES,
    LOST_REASONS,
    TERMINAL_LOST,
    can_auto_transition,
    decide_from_tenant_config,
    decide_on_hard_stop,
    decide_on_handoff_classifier,
    decide_on_lead_message,
    decide_on_prepayment_received,
    decide_on_project_completed,
    decide_on_silence,
    decide_post_sale_window,
    determine_next_after_qualified,
    should_skip_nda,
    validate_transition,
)


# -------------------------------------------------------------------- taxonomy

class TestTaxonomy:
    def test_stages_count(self):
        assert len(ACTIVE_STAGES) == 11
        assert len(ALL_STAGES) == 12  # 11 active + lost
        assert TERMINAL_LOST in ALL_STAGES

    def test_lost_reasons_count(self):
        assert len(LOST_REASONS) == 6
        assert "price" in LOST_REASONS
        assert "hard_stop" in LOST_REASONS


# ------------------------------------------------------- transition validation

class TestCanAutoTransition:
    def test_forward_motion_allowed(self):
        assert can_auto_transition("new_lead", "in_dialog")
        assert can_auto_transition("in_dialog", "qualified")
        assert can_auto_transition("qualified", "nda")
        assert can_auto_transition("qualified", "on_call")  # NDA can be skipped
        assert can_auto_transition("in_work", "completed_won")
        assert can_auto_transition("completed_won", "post_sale")

    def test_backward_forbidden(self):
        assert not can_auto_transition("qualified", "new_lead")
        assert not can_auto_transition("proposal", "qualified")
        assert not can_auto_transition("in_work", "proposal")

    def test_lost_terminal(self):
        assert not can_auto_transition("lost", "new_lead")
        assert not can_auto_transition("lost", "in_dialog")

    def test_completed_won_never_auto_loses(self):
        assert not can_auto_transition("post_sale", "lost")
        assert not can_auto_transition("completed_won", "lost")

    def test_lost_reachable_from_active(self):
        for stage in ACTIVE_STAGES:
            if stage in ("completed_won", "post_sale"):
                continue
            assert can_auto_transition(stage, "lost"), f"{stage} should reach lost"

    def test_same_stage_is_no_op(self):
        assert not can_auto_transition("in_dialog", "in_dialog")


class TestValidateTransition:
    def test_to_lost_requires_reason(self):
        with pytest.raises(ValueError, match="lost_reason"):
            validate_transition("qualified", "lost")

    def test_to_lost_with_reason_ok(self):
        validate_transition("qualified", "lost", "price")  # no raise

    def test_invalid_lost_reason_rejected(self):
        with pytest.raises(ValueError, match="lost_reason"):
            validate_transition("qualified", "lost", "wrong_reason")

    def test_operator_override_allows_backward(self):
        validate_transition("post_sale", "new_lead", operator_override=True)

    def test_operator_override_still_rejects_bad_vocabulary(self):
        with pytest.raises(ValueError, match="Unknown"):
            validate_transition("garbage", "in_dialog", operator_override=True)


# ------------------------------------------------------------ NDA skip routing

class TestNdaSkip:
    def test_web_skips_nda(self):
        assert should_skip_nda("web", ["ai_agents"])

    def test_automation_skips_nda(self):
        assert should_skip_nda("automation", ["ai_agents"])

    def test_ai_agents_needs_nda(self):
        assert not should_skip_nda("ai_agents", ["ai_agents"])

    def test_case_insensitive(self):
        assert not should_skip_nda("AI_Agents", ["ai_agents"])
        assert not should_skip_nda("ai_agents", ["AI_AGENTS"])

    def test_unknown_project_type_defaults_skip(self):
        assert should_skip_nda(None, ["ai_agents"])
        assert should_skip_nda("", ["ai_agents"])

    def test_next_after_qualified_routes_correctly(self):
        assert determine_next_after_qualified("web", ["ai_agents"]) == "on_call"
        assert determine_next_after_qualified("ai_agents", ["ai_agents"]) == "nda"


# --------------------------------------------------------- per-rule decisions

class TestDecideOnLeadMessage:
    def test_below_threshold_no_change(self):
        assert not decide_on_lead_message("new_lead", 2, 3).should_transition

    def test_at_threshold_promotes(self):
        d = decide_on_lead_message("new_lead", 3, 3)
        assert d.should_transition
        assert d.target_stage == "in_dialog"

    def test_past_new_lead_is_no_op(self):
        assert not decide_on_lead_message("in_dialog", 10, 3).should_transition


class TestDecideOnHandoffClassifier:
    def test_promotes_in_dialog_to_qualified(self):
        d = decide_on_handoff_classifier("in_dialog", True)
        assert d.should_transition and d.target_stage == "qualified"

    def test_promotes_new_lead_to_qualified(self):
        # Notion §21 — handoff classifier can fast-track from new_lead too
        assert decide_on_handoff_classifier("new_lead", True).target_stage == "qualified"

    def test_already_qualified_no_op(self):
        assert not decide_on_handoff_classifier("qualified", True).should_transition

    def test_classifier_negative_no_op(self):
        assert not decide_on_handoff_classifier("in_dialog", False).should_transition


class TestDecideOnSilence:
    def test_in_dialog_silence_lost(self):
        d = decide_on_silence("in_dialog", 8, 7)
        assert d.target_stage == "lost"
        assert d.lost_reason == "delayed"

    def test_proposal_silence_no_auto(self):
        # operator decides for money-stage silence
        assert not decide_on_silence("proposal", 30, 7).should_transition

    def test_under_threshold_no_op(self):
        assert not decide_on_silence("in_dialog", 5, 7).should_transition


class TestDecideOnHardStop:
    def test_active_stage_loses(self):
        for stage in ACTIVE_STAGES:
            d = decide_on_hard_stop(stage)
            assert d.target_stage == "lost"
            assert d.lost_reason == "hard_stop"

    def test_already_lost_no_op(self):
        assert not decide_on_hard_stop("lost").should_transition


class TestPrepaymentAndCompletion:
    def test_prepayment_received_advances(self):
        assert decide_on_prepayment_received("prepayment").target_stage == "in_work"

    def test_prepayment_received_other_stage_no_op(self):
        assert not decide_on_prepayment_received("proposal").should_transition

    def test_project_completed_advances(self):
        assert decide_on_project_completed("in_work").target_stage == "completed_won"


class TestPostSaleWindow:
    def test_30_days_after_completed(self):
        d = decide_post_sale_window("completed_won", 30)
        assert d.target_stage == "post_sale"

    def test_under_window_no_op(self):
        assert not decide_post_sale_window("completed_won", 29).should_transition


# ----------------------------------------------------------- composite driver

class TestDecideFromTenantConfig:
    def test_engagement_promotes(self):
        cfg = {"funnel": {"auto_qualify_after_messages": 3}}
        d = decide_from_tenant_config("new_lead", cfg, lead_messages_so_far=3)
        assert d.target_stage == "in_dialog"

    def test_hard_stop_wins_over_engagement(self):
        cfg = {"funnel": {"auto_qualify_after_messages": 3}}
        d = decide_from_tenant_config(
            "new_lead", cfg, lead_messages_so_far=10, hard_stop_signal=True,
        )
        assert d.target_stage == "lost"
        assert d.lost_reason == "hard_stop"

    def test_handoff_beats_engagement(self):
        cfg = {"funnel": {"auto_qualify_after_messages": 3}}
        d = decide_from_tenant_config(
            "in_dialog", cfg, lead_messages_so_far=10, classifier_says_ready=True,
        )
        assert d.target_stage == "qualified"

    def test_no_signals_no_change(self):
        cfg = {"funnel": {"auto_qualify_after_messages": 3}}
        assert not decide_from_tenant_config("in_dialog", cfg).should_transition
