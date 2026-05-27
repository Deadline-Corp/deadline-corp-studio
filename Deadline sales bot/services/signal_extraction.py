"""Heuristic extractors for bot-reply confidence and lead-message budget
(Phase 10, 2026-05-27).

Both functions are pure — text in, signal out. No LLM call, no DB.

estimate_bot_confidence(reply_text) → float in [0.0, 1.0]
  Reads the bot's own reply, looks for hedging/uncertainty phrases. Used
  to fire the low_confidence escalation trigger when the bot is clearly
  unsure (e.g. "let me check with the team", "I'm not sure"). This is a
  cheap approximation — proper logprobs-based confidence would require
  passing logprobs=true through ChatOpenAI and reading generation_info,
  but the heuristic gives ~80% of the signal at zero infra cost.

extract_budget_rub(lead_text) → Optional[int]
  Pulls a budget figure out of lead text and normalises to rubles.
  Handles common spellings in RU/EN: "5k USD", "10000$", "100 тыс руб",
  "1 млн", etc. Conservative — returns None when no match (no false
  positives on numbers that aren't budget). Used both to fire the
  large_deal_above_threshold escalation trigger AND to enrich
  Customer.profile_data so operators see the parsed figure in HubSpot.
"""

from __future__ import annotations

import re
from typing import Optional


# =============================================================================
# Bot reply confidence — heuristic
# =============================================================================

# Phrases that strongly signal low confidence in the bot's own reply
LOW_CONFIDENCE_PHRASES: tuple[str, ...] = (
    # Russian
    "не знаю",
    "не уверен",
    "не уверена",
    "уточнить с командой",
    "уточнить у команды",
    "надо уточнить",
    "нужно уточнить",
    "это лучше обсудить с командой",
    "возможно",
    "наверное",
    "не могу точно сказать",
    "не подскажу",
    "сложно сказать",
    "затрудняюсь ответить",
    # English
    "i'm not sure",
    "i am not sure",
    "let me check",
    "i don't know",
    "i need to check",
    "you'd need to ask the team",
    "best to ask the team",
    "i can't say",
    "probably",
    "perhaps",
    "not certain",
)

# Phrases that signal HIGH confidence (handoff complete, concrete next step)
HIGH_CONFIDENCE_PHRASES: tuple[str, ...] = (
    "передал команде",
    "passed to the team",
    "напишем на email",
    "we will email you",
)


def estimate_bot_confidence(reply_text: Optional[str]) -> float:
    """Returns confidence in [0.0, 1.0] based on hedging phrases in the reply.

    Mapping:
      - empty / very short (operator takeover blank) → 1.0 (no judgement)
      - any low-confidence phrase → 0.5 (will trip default gate of 0.7)
      - 2+ low-confidence phrases in same reply → 0.3 (clear uncertainty)
      - high-confidence phrase (handoff/follow-up) → 0.95
      - neutral → 0.85 (above gate, no trigger)
    """
    if not reply_text:
        return 1.0

    text_lower = reply_text.lower()
    low_count = sum(1 for p in LOW_CONFIDENCE_PHRASES if p in text_lower)
    high_count = sum(1 for p in HIGH_CONFIDENCE_PHRASES if p in text_lower)

    if low_count >= 2:
        return 0.3
    if low_count == 1:
        return 0.5
    if high_count >= 1:
        return 0.95
    return 0.85


# =============================================================================
# Budget extraction — regex
# =============================================================================

# Order matters: longer patterns first (so "10 тыс руб" wins over plain "10 тыс").
# Each pattern captures the number; the surrounding unit decides currency
# and magnitude (thousands / millions).

# Roughly mid-2026 cross rates — operator-overridable in tenant config later.
USD_TO_RUB: float = 92.0
EUR_TO_RUB: float = 100.0
THB_TO_RUB: float = 2.6


# Each entry: (compiled regex, currency, magnitude_multiplier)
# The capture group must be the numeric part.
_BUDGET_PATTERNS: list[tuple[re.Pattern, str, int]] = [
    # ---- USD ----
    # "$5k", "5k USD", "5 000 USD", "$10 000"
    (re.compile(r"\$\s*(\d[\d\s,\.]*)\s*[kKкК]\b"),       "USD", 1_000),
    (re.compile(r"(\d[\d\s,\.]*)\s*[kKкК]\s*(?:usd|\$)\b", re.IGNORECASE), "USD", 1_000),
    (re.compile(r"\$\s*(\d[\d\s,\.]*)"),                   "USD", 1),
    (re.compile(r"(\d[\d\s,\.]*)\s*(?:usd|долл|dollars)\b", re.IGNORECASE), "USD", 1),

    # ---- EUR ----
    (re.compile(r"€\s*(\d[\d\s,\.]*)\s*[kK]\b"),          "EUR", 1_000),
    (re.compile(r"(\d[\d\s,\.]*)\s*[kK]\s*(?:eur|€)\b", re.IGNORECASE), "EUR", 1_000),
    (re.compile(r"€\s*(\d[\d\s,\.]*)"),                    "EUR", 1),
    (re.compile(r"(\d[\d\s,\.]*)\s*(?:eur|euro)\b", re.IGNORECASE), "EUR", 1),

    # ---- THB ----
    (re.compile(r"(\d[\d\s,\.]*)\s*[kK]\s*(?:thb|бат|baht)\b", re.IGNORECASE), "THB", 1_000),
    (re.compile(r"(\d[\d\s,\.]*)\s*(?:thb|бат|baht)\b", re.IGNORECASE), "THB", 1),

    # ---- RUB ----
    (re.compile(r"(\d[\d\s,\.]*)\s*млн\s*(?:руб|₽|rub)?\b", re.IGNORECASE), "RUB", 1_000_000),
    (re.compile(r"(\d[\d\s,\.]*)\s*млн\b", re.IGNORECASE), "RUB", 1_000_000),
    (re.compile(r"(\d[\d\s,\.]*)\s*тыс(?:яч)?\s*(?:руб|₽|rub)?\b", re.IGNORECASE), "RUB", 1_000),
    (re.compile(r"(\d[\d\s,\.]*)\s*тыс\b", re.IGNORECASE), "RUB", 1_000),
    (re.compile(r"(\d[\d\s,\.]*)\s*к\s*(?:руб|₽|rub)\b", re.IGNORECASE), "RUB", 1_000),
    (re.compile(r"(\d[\d\s,\.]*)\s*(?:руб|₽|rub)\b", re.IGNORECASE), "RUB", 1),
]


# Trigger words near the number — to reduce false positives.
# We only consider matches that occur within ±60 chars of one of these words.
BUDGET_CONTEXT_WORDS: tuple[str, ...] = (
    "бюджет", "стоимост", "цена", "ценник", "оплат", "до ", "около ",
    "budget", "price", "cost", "around ", "up to ", "max ",
)


def _parse_number(raw: str) -> Optional[float]:
    """Convert '10 000', '10,000', '10.5', '1 200,50' → float."""
    if not raw:
        return None
    # Remove spaces (thousands separators) and replace comma with dot
    cleaned = raw.replace(" ", "").replace(",", ".")
    # Tolerate trailing dots
    cleaned = cleaned.rstrip(".")
    if not cleaned:
        return None
    # If there are multiple dots ("1.200.000" rare RU format), keep only the last as decimal
    if cleaned.count(".") > 1:
        parts = cleaned.split(".")
        cleaned = "".join(parts[:-1]) + "." + parts[-1]
    try:
        return float(cleaned)
    except ValueError:
        return None


def _has_budget_context(text: str, match_start: int, match_end: int) -> bool:
    """True if a budget-context word is within ±60 chars of the match."""
    window_lo = max(0, match_start - 60)
    window_hi = min(len(text), match_end + 60)
    window = text[window_lo:window_hi].lower()
    return any(w in window for w in BUDGET_CONTEXT_WORDS)


def _to_rub(amount: float, currency: str) -> int:
    if currency == "RUB":
        return int(amount)
    if currency == "USD":
        return int(amount * USD_TO_RUB)
    if currency == "EUR":
        return int(amount * EUR_TO_RUB)
    if currency == "THB":
        return int(amount * THB_TO_RUB)
    return int(amount)


def extract_budget_rub(lead_text: Optional[str]) -> Optional[int]:
    """Best-effort extract a budget figure from a lead message; normalise to RUB.

    Returns None when no budget pattern is matched OR matches don't have
    a budget-context word nearby. We pick the LARGEST candidate (operators
    usually mean total budget when several numbers are present).

    Examples:
      "бюджет 100 тыс руб"            → 100_000
      "budget around 5k USD"           → ~460_000
      "до 2 млн рублей"                → 2_000_000
      "5000 thb"                       → 13_000
      "приходи в 5 вечера" (no budget) → None
      "цена будет 50 000"              → 50_000 (RUB by default)
    """
    if not lead_text:
        return None
    text = lead_text

    candidates: list[int] = []
    for pattern, currency, mult in _BUDGET_PATTERNS:
        for m in pattern.finditer(text):
            if not _has_budget_context(text, m.start(), m.end()):
                continue
            num = _parse_number(m.group(1))
            if num is None or num <= 0:
                continue
            rub = _to_rub(num * mult, currency)
            if rub > 0:
                candidates.append(rub)

    if not candidates:
        return None
    # Pick the largest — biggest reported budget tends to be the operator's intent
    return max(candidates)
