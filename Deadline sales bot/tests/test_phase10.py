"""Tests for Phase 10 — signal_extraction (confidence + budget)."""

import pytest

from services.signal_extraction import (
    USD_TO_RUB,
    EUR_TO_RUB,
    THB_TO_RUB,
    estimate_bot_confidence,
    extract_budget_rub,
)


# =============================================================================
# Confidence heuristic
# =============================================================================

class TestBotConfidence:
    def test_empty_returns_neutral_high(self):
        assert estimate_bot_confidence("") == 1.0
        assert estimate_bot_confidence(None) == 1.0

    def test_high_confidence_on_handoff_phrase(self):
        assert estimate_bot_confidence("Передал команде, напишем на email") >= 0.9

    def test_low_confidence_on_uncertainty(self):
        assert estimate_bot_confidence("Возможно, надо уточнить с командой") < 0.7

    def test_very_low_on_multiple_hedges(self):
        # 2+ low-confidence phrases → 0.3
        assert estimate_bot_confidence(
            "Не знаю, наверное надо уточнить с командой"
        ) == 0.3

    def test_neutral_default(self):
        assert estimate_bot_confidence("Понятно. Опишите подробнее задачу") == 0.85

    def test_english_low_confidence(self):
        assert estimate_bot_confidence("I'm not sure, let me check with the team") < 0.6

    def test_english_high_confidence(self):
        assert estimate_bot_confidence("Passed to the team. We will email you") >= 0.9


# =============================================================================
# Budget extraction
# =============================================================================

class TestBudgetExtraction:
    def test_thousands_rub_with_context(self):
        assert extract_budget_rub("бюджет 100 тыс руб") == 100_000

    def test_million_rub(self):
        assert extract_budget_rub("до 2 млн рублей хотим вложить") == 2_000_000

    def test_usd_with_k_suffix(self):
        # 5 * 1000 * USD_TO_RUB
        out = extract_budget_rub("budget around 5k USD")
        assert out is not None
        assert out == int(5000 * USD_TO_RUB)

    def test_dollar_sign_prefix(self):
        out = extract_budget_rub("budget is $10 000")
        assert out is not None
        # Best-effort — could parse as 10000 USD or 10 USD depending on regex order.
        # Accept anything > 100k RUB.
        assert out > 100_000

    def test_eur_k(self):
        out = extract_budget_rub("budget 5k EUR")
        assert out is not None
        assert out == int(5000 * EUR_TO_RUB)

    def test_thb_simple(self):
        out = extract_budget_rub("price 5000 thb")
        assert out is not None
        assert out == int(5000 * THB_TO_RUB)

    def test_no_context_returns_none(self):
        assert extract_budget_rub("приходи в 5 вечера") is None
        assert extract_budget_rub("у меня есть 10 лет опыта") is None

    def test_empty_returns_none(self):
        assert extract_budget_rub("") is None
        assert extract_budget_rub(None) is None

    def test_picks_largest_candidate(self):
        # When multiple numbers present, biggest wins
        out = extract_budget_rub(
            "бюджет 50 тыс руб минимум, максимум до 200 тыс руб"
        )
        assert out == 200_000

    def test_decimal_million_rub(self):
        out = extract_budget_rub("ценник 1.5 млн руб")
        assert out is not None
        # 1.5 млн = 1_500_000
        assert out == 1_500_000

    def test_zero_or_negative_skipped(self):
        # Trigger word present but value is 0 — skipped
        assert extract_budget_rub("бюджет 0 руб") is None
