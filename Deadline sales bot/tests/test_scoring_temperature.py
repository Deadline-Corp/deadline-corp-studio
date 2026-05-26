"""Tests for services/scoring.py + services/temperature.py.

Pure-function tests, no DB. Run with:
    pytest tests/test_scoring_temperature.py
"""

import pytest

from services.scoring import (
    ScoreComponents,
    add_message_score,
    apply_decay as score_decay,
    base_score,
    compute_initial_score,
    content_score,
    detect_interaction_type,
    is_qualified,
    source_factor,
)
from services.temperature import (
    ACTIVE_TEMPERATURES,
    ALL_TEMPERATURES,
    apply_decay as temp_decay,
    decide_from_tenant_config,
    detect_from_message,
    is_higher,
    step_down,
    upgrade_to,
)


# Realistic tenant config slice
TENANT_SCORING = {
    "base_by_interaction_type": {
        "P1": 100, "P2": 60, "P3": 50, "P4": 30, "P5": 20, "P6": 10, "HardStop": 0,
    },
    "content_keywords": {
        "бюджет": 20, "дедлайн": 20, "тз": 15, "проект": 10,
        "budget": 20, "deadline": 20, "project": 10,
    },
    "source_weight": {
        "telegram": 1.0, "website": 1.0, "instagram": 0.8,
        "messenger": 0.8, "email": 0.6,
    },
    "decay_per_48h": -1,
    "qualification_threshold": 80,
}

TENANT_TEMPERATURE = {
    "decay_days": 14,
    "frozen_after_days": 21,
    "triggers": {
        "warm": {"content_replies_count": 2},
        "hot": {"keywords": ["цена", "сроки", "портфолио", "price", "timeline"]},
        "ready": {"phrases": ["что нужно чтобы начать", "ready to begin"]},
    },
}


# =============================================================================
# scoring.py — building blocks
# =============================================================================

class TestBaseScore:
    def test_known_type(self):
        assert base_score("P1", TENANT_SCORING["base_by_interaction_type"]) == 100
        assert base_score("P6", TENANT_SCORING["base_by_interaction_type"]) == 10
        assert base_score("HardStop", TENANT_SCORING["base_by_interaction_type"]) == 0

    def test_unknown_type_zero(self):
        assert base_score("WHATEVER", TENANT_SCORING["base_by_interaction_type"]) == 0


class TestContentScore:
    def test_single_keyword(self):
        total, matched = content_score("у меня бюджет 100к", TENANT_SCORING["content_keywords"])
        assert total == 20
        assert matched == ("бюджет",)

    def test_multiple_keywords_summed(self):
        text = "Бюджет 5к, дедлайн через неделю"
        total, matched = content_score(text, TENANT_SCORING["content_keywords"])
        assert total == 40  # 20 + 20
        assert set(matched) == {"бюджет", "дедлайн"}

    def test_case_insensitive(self):
        total, matched = content_score("BUDGET для project", TENANT_SCORING["content_keywords"])
        assert total == 30  # 20 + 10
        assert set(matched) == {"budget", "project"}

    def test_no_match(self):
        assert content_score("привет", TENANT_SCORING["content_keywords"]) == (0, ())

    def test_empty_input(self):
        assert content_score("", TENANT_SCORING["content_keywords"]) == (0, ())
        assert content_score(None, TENANT_SCORING["content_keywords"]) == (0, ())


class TestSourceFactor:
    def test_known_channel(self):
        assert source_factor("telegram", TENANT_SCORING["source_weight"]) == 1.0
        assert source_factor("instagram", TENANT_SCORING["source_weight"]) == 0.8

    def test_unknown_channel_default(self):
        assert source_factor("smoke_signal", TENANT_SCORING["source_weight"]) == 1.0


# =============================================================================
# scoring.py — composite
# =============================================================================

class TestComputeInitialScore:
    def test_p1_telegram_with_budget_keyword(self):
        sc = compute_initial_score(
            interaction_type="P1",
            channel="telegram",
            first_message_text="нужен сайт, бюджет 5к",
            config_scoring=TENANT_SCORING,
        )
        # base 100 + content 20 (бюджет) = 120, * 1.0 telegram = 120
        assert sc.total == 120
        assert sc.base == 100
        assert sc.content == 20

    def test_p2_instagram_no_keywords(self):
        sc = compute_initial_score(
            interaction_type="P2",
            channel="instagram",
            first_message_text="привет",
            config_scoring=TENANT_SCORING,
        )
        # base 60 + content 0 = 60, * 0.8 instagram = 48
        assert sc.total == 48
        assert sc.content == 0

    def test_hardstop_is_zero(self):
        sc = compute_initial_score(
            interaction_type="HardStop",
            channel="telegram",
            first_message_text="не пишите больше",
            config_scoring=TENANT_SCORING,
        )
        assert sc.total == 0


class TestAddMessageScore:
    def test_keyword_adds_points(self):
        new_score, matched = add_message_score(
            current_score=50,
            channel="telegram",
            message_text="ок, бюджет 10к",
            config_scoring=TENANT_SCORING,
        )
        assert new_score == 70  # 50 + (20 * 1.0)
        assert "бюджет" in matched

    def test_no_keyword_no_change(self):
        new_score, matched = add_message_score(
            current_score=50,
            channel="telegram",
            message_text="ок, до завтра",
            config_scoring=TENANT_SCORING,
        )
        assert new_score == 50
        assert matched == ()

    def test_source_factor_applied(self):
        new_score, _ = add_message_score(
            current_score=50,
            channel="email",
            message_text="наш бюджет на проект",
            config_scoring=TENANT_SCORING,
        )
        # +20 бюджет + 10 проект = 30 * 0.6 email = 18 → +18
        assert new_score == 68


class TestScoreDecay:
    def test_below_48h_no_decay(self):
        assert score_decay(100, hours_silent=12) == 100
        assert score_decay(100, hours_silent=47.9) == 100

    def test_48h_one_step(self):
        assert score_decay(100, hours_silent=48) == 99

    def test_multiple_intervals(self):
        # 100 hours = 2 full 48h intervals → -2
        assert score_decay(100, hours_silent=100) == 98

    def test_floor_at_zero(self):
        assert score_decay(2, hours_silent=480) == 0  # 10 intervals would be -10

    def test_already_at_min(self):
        assert score_decay(0, hours_silent=480) == 0


class TestIsQualified:
    def test_at_threshold(self):
        assert is_qualified(80, 80)

    def test_above(self):
        assert is_qualified(100, 80)

    def test_below(self):
        assert not is_qualified(79, 80)


# =============================================================================
# scoring.py — InteractionType detection
# =============================================================================

class TestDetectInteractionType:
    def test_hard_stop_wins(self):
        assert detect_interaction_type(
            channel="telegram",
            is_hard_stop=True,
            is_explicit_request=True,  # ignored, hard_stop wins
        ) == "HardStop"

    def test_outbound_is_p6(self):
        assert detect_interaction_type(channel="email", is_outbound=True) == "P6"

    def test_ad_click_is_p1(self):
        assert detect_interaction_type(channel="website", is_ad_click=True) == "P1"

    def test_explicit_request_is_p1(self):
        assert detect_interaction_type(channel="telegram", is_explicit_request=True) == "P1"

    def test_form_is_p2(self):
        assert detect_interaction_type(channel="website", is_form_submission=True) == "P2"

    def test_cold_return_is_p3(self):
        assert detect_interaction_type(channel="telegram", is_cold_return=True) == "P3"

    def test_comment_is_p4(self):
        assert detect_interaction_type(channel="instagram", is_public_comment=True) == "P4"

    def test_reaction_is_p5(self):
        assert detect_interaction_type(channel="instagram", is_reaction_or_story=True) == "P5"

    def test_default_dm_is_p2(self):
        assert detect_interaction_type(channel="telegram") == "P2"


# =============================================================================
# temperature.py
# =============================================================================

class TestTemperatureVocabulary:
    def test_active_count(self):
        assert len(ACTIVE_TEMPERATURES) == 5
        assert ACTIVE_TEMPERATURES == ("cold", "warm", "hot", "ready", "client")

    def test_all_count(self):
        assert len(ALL_TEMPERATURES) == 6
        assert "frozen" in ALL_TEMPERATURES


class TestIsHigher:
    def test_basic_ordering(self):
        assert is_higher("hot", "warm")
        assert is_higher("client", "cold")
        assert is_higher("ready", "hot")

    def test_same_not_higher(self):
        assert not is_higher("warm", "warm")

    def test_frozen_incomparable(self):
        assert not is_higher("frozen", "cold")
        assert not is_higher("hot", "frozen")


class TestUpgradeTo:
    def test_higher_wins(self):
        assert upgrade_to("warm", "hot") == "hot"
        assert upgrade_to("cold", "ready") == "ready"

    def test_no_downgrade(self):
        assert upgrade_to("hot", "cold") == "hot"

    def test_frozen_exits_on_any_candidate(self):
        assert upgrade_to("frozen", "warm") == "warm"
        assert upgrade_to("frozen", "cold") == "cold"

    def test_frozen_candidate_ignored(self):
        # Frozen is set by decay only, not by upgrade
        assert upgrade_to("hot", "frozen") == "hot"


class TestDetectFromMessage:
    def test_ready_phrase_wins(self):
        result = detect_from_message(
            current_temperature="cold",
            content_replies_so_far=0,
            message_text="что нужно чтобы начать с вами работу",
            config_temperature=TENANT_TEMPERATURE,
        )
        assert result == "ready"

    def test_hot_keyword(self):
        result = detect_from_message(
            current_temperature="warm",
            content_replies_so_far=2,
            message_text="какая цена за такой проект?",
            config_temperature=TENANT_TEMPERATURE,
        )
        assert result == "hot"

    def test_warm_engagement(self):
        result = detect_from_message(
            current_temperature="cold",
            content_replies_so_far=2,
            message_text="ок норм",
            config_temperature=TENANT_TEMPERATURE,
        )
        assert result == "warm"

    def test_below_engagement_no_change(self):
        result = detect_from_message(
            current_temperature="cold",
            content_replies_so_far=1,
            message_text="ок",
            config_temperature=TENANT_TEMPERATURE,
        )
        assert result == "cold"

    def test_no_downgrade(self):
        # Already hot, neutral message → stays hot
        result = detect_from_message(
            current_temperature="hot",
            content_replies_so_far=5,
            message_text="спасибо",
            config_temperature=TENANT_TEMPERATURE,
        )
        assert result == "hot"


class TestStepDown:
    def test_basic(self):
        assert step_down("hot") == "warm"
        assert step_down("warm") == "cold"
        assert step_down("ready") == "hot"

    def test_cold_floor(self):
        assert step_down("cold") == "cold"

    def test_client_does_not_decay(self):
        assert step_down("client") == "client"

    def test_frozen_stays(self):
        assert step_down("frozen") == "frozen"


class TestTempDecay:
    def test_under_decay_threshold(self):
        assert temp_decay("hot", silent_days=10) == "hot"

    def test_14d_step_down(self):
        assert temp_decay("hot", silent_days=14) == "warm"
        assert temp_decay("warm", silent_days=14) == "cold"

    def test_21d_frozen(self):
        assert temp_decay("hot", silent_days=21) == "frozen"
        assert temp_decay("ready", silent_days=30) == "frozen"

    def test_client_never_decays(self):
        assert temp_decay("client", silent_days=999) == "client"

    def test_frozen_stays_frozen(self):
        assert temp_decay("frozen", silent_days=999) == "frozen"


class TestDecideFromTenantConfig:
    cfg = {"temperature": TENANT_TEMPERATURE}

    def test_engagement_beats_decay(self):
        # Lead silent 30 days, then sends "цена" → exit frozen, go hot
        result = decide_from_tenant_config(
            current_temperature="frozen",
            content_replies_so_far=0,
            message_text="а цена какая?",
            silent_days=30,
            tenant_config=self.cfg,
        )
        assert result == "hot"  # frozen exits via candidate

    def test_pure_decay_no_engagement(self):
        result = decide_from_tenant_config(
            current_temperature="hot",
            content_replies_so_far=0,
            message_text=None,
            silent_days=14,
            tenant_config=self.cfg,
        )
        assert result == "warm"

    def test_no_signal_no_change(self):
        result = decide_from_tenant_config(
            current_temperature="warm",
            content_replies_so_far=1,
            message_text="привет",
            silent_days=3,
            tenant_config=self.cfg,
        )
        assert result == "warm"
