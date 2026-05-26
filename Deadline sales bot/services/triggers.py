"""7 escalation triggers (Notion §21, Phase 6, 2026-05-26).

Each trigger is a pure-function predicate over runtime signals. Hot path
calls check_all_triggers(...) on every bot turn; positive results are
queued as operator notifications (Telegram alert + CRM task).

Notion §21 list (severity in parentheses):
    1. low_confidence              (warning)   — bot answer confidence < gate
    2. two_negatives_in_row        (warning)   — lead frustration pattern
    3. large_deal_above_threshold  (alert)     — high-value lead needs human
    4. legal_keywords              (alert)     — contract / lawsuit / etc
    5. dialog_loop                 (warning)   — bot stuck in similar replies
    6. explicit_handoff_request    (alert)     — lead asked for a human
    7. silence_after_24h           (warning)   — lead ghosted >24h
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional


TriggerType = Literal[
    "low_confidence",
    "two_negatives_in_row",
    "large_deal_above_threshold",
    "legal_keywords",
    "dialog_loop",
    "explicit_handoff_request",
    "silence_after_24h",
]

Severity = Literal["warning", "alert"]


@dataclass(frozen=True)
class EscalationTrigger:
    """One fired trigger. Caller decides whether to notify operator,
    create CRM task, write to log, etc."""
    type: TriggerType
    severity: Severity
    reason: str


# -----------------------------------------------------------------------------
# Phrase / keyword inventories
# -----------------------------------------------------------------------------

LEGAL_KEYWORDS_RU: tuple[str, ...] = (
    "договор", "суд", "жалоба", "юрист", "адвокат", "ндс", "акт сверки",
    "штраф", "иск", "претензия", "арбитраж",
)
LEGAL_KEYWORDS_EN: tuple[str, ...] = (
    "contract", "lawsuit", "lawyer", "attorney", "vat", "tax", "complaint",
    "litigation", "claim", "subpoena",
)
LEGAL_KEYWORDS: tuple[str, ...] = LEGAL_KEYWORDS_RU + LEGAL_KEYWORDS_EN


HANDOFF_PHRASES_RU: tuple[str, ...] = (
    "соедини", "позови менеджера", "хочу с человеком", "переключи на",
    "позовите менеджера", "хочу к человеку", "дайте человека",
    "поговорить с менеджером", "связь с менеджером",
)
HANDOFF_PHRASES_EN: tuple[str, ...] = (
    "manager please", "talk to a human", "real person", "human agent",
    "speak to manager", "talk to manager", "speak with someone",
)
HANDOFF_PHRASES: tuple[str, ...] = HANDOFF_PHRASES_RU + HANDOFF_PHRASES_EN


NEGATIVE_PHRASES_RU: tuple[str, ...] = (
    "не подходит", "дорого", "не интересно", "не надо", "не нравится",
    "плохо", "не вариант", "не наш", "слишком дорого", "не устраивает",
)
NEGATIVE_PHRASES_EN: tuple[str, ...] = (
    "not interested", "too expensive", "no thanks", "doesn't fit",
    "no thank you", "won't work", "not for us",
)
NEGATIVE_PHRASES: tuple[str, ...] = NEGATIVE_PHRASES_RU + NEGATIVE_PHRASES_EN


# -----------------------------------------------------------------------------
# Individual checks
# -----------------------------------------------------------------------------

def check_low_confidence(
    confidence: Optional[float], gate: float = 0.7,
) -> Optional[EscalationTrigger]:
    if confidence is not None and confidence < gate:
        return EscalationTrigger(
            type="low_confidence",
            severity="warning",
            reason=f"answer confidence {confidence:.2f} < gate {gate}",
        )
    return None


def check_legal_keywords(message_text: Optional[str]) -> Optional[EscalationTrigger]:
    if not message_text:
        return None
    text_lower = message_text.lower()
    for kw in LEGAL_KEYWORDS:
        if kw in text_lower:
            return EscalationTrigger(
                type="legal_keywords",
                severity="alert",
                reason=f"legal keyword detected: {kw!r}",
            )
    return None


def check_explicit_handoff(message_text: Optional[str]) -> Optional[EscalationTrigger]:
    if not message_text:
        return None
    text_lower = message_text.lower()
    for phrase in HANDOFF_PHRASES:
        if phrase in text_lower:
            return EscalationTrigger(
                type="explicit_handoff_request",
                severity="alert",
                reason=f"lead requested human: {phrase!r}",
            )
    return None


def check_two_negatives_in_row(
    recent_lead_messages: list[str],
) -> Optional[EscalationTrigger]:
    """Last 2 lead messages both contain a negative phrase."""
    if len(recent_lead_messages) < 2:
        return None

    def is_negative(text: Optional[str]) -> bool:
        if not text:
            return False
        text_lower = text.lower()
        return any(phrase in text_lower for phrase in NEGATIVE_PHRASES)

    if is_negative(recent_lead_messages[-1]) and is_negative(recent_lead_messages[-2]):
        return EscalationTrigger(
            type="two_negatives_in_row",
            severity="warning",
            reason="last 2 lead messages contain negative phrases",
        )
    return None


def check_large_deal(
    estimated_budget_rub: Optional[int],
    threshold_rub: int = 1_000_000,
) -> Optional[EscalationTrigger]:
    if estimated_budget_rub is not None and estimated_budget_rub >= threshold_rub:
        return EscalationTrigger(
            type="large_deal_above_threshold",
            severity="alert",
            reason=f"estimated budget {estimated_budget_rub} >= threshold {threshold_rub}",
        )
    return None


def check_dialog_loop(
    recent_bot_replies: list[str], similarity_threshold: int = 3,
) -> Optional[EscalationTrigger]:
    """N+ consecutive bot replies start with the same 30-char prefix.

    Crude heuristic — catches the obvious case where the bot is stuck
    repeating a template. Sentence-level semantic loops slip through
    this check and need LLM-as-judge in a future iteration.
    """
    if len(recent_bot_replies) < similarity_threshold:
        return None
    tail = recent_bot_replies[-similarity_threshold:]
    if not tail[-1]:
        return None
    prefix = tail[-1][:30]
    if all(r and r[:30] == prefix for r in tail):
        return EscalationTrigger(
            type="dialog_loop",
            severity="warning",
            reason=f"bot replied with same 30-char prefix {similarity_threshold} times in a row",
        )
    return None


def check_silence_after_24h(silent_hours: float) -> Optional[EscalationTrigger]:
    if silent_hours >= 24:
        return EscalationTrigger(
            type="silence_after_24h",
            severity="warning",
            reason=f"lead silent for {silent_hours:.1f}h (>= 24h)",
        )
    return None


# -----------------------------------------------------------------------------
# Composite
# -----------------------------------------------------------------------------

def check_all_triggers(
    *,
    confidence: Optional[float] = None,
    message_text: Optional[str] = None,
    recent_lead_messages: Optional[list[str]] = None,
    recent_bot_replies: Optional[list[str]] = None,
    estimated_budget_rub: Optional[int] = None,
    silent_hours: float = 0,
    confidence_gate: float = 0.7,
    budget_threshold_rub: int = 1_000_000,
    dialog_loop_threshold: int = 3,
) -> list[EscalationTrigger]:
    """Run all 7 checks. Returns list of fired triggers (may be empty).

    All inputs are optional so callers only pass signals they have. Order
    of results matches the Notion §21 list — caller is free to dedupe or
    re-prioritise.
    """
    out: list[EscalationTrigger] = []

    if (t := check_low_confidence(confidence, confidence_gate)) is not None:
        out.append(t)
    if (t := check_two_negatives_in_row(recent_lead_messages or [])) is not None:
        out.append(t)
    if (t := check_large_deal(estimated_budget_rub, budget_threshold_rub)) is not None:
        out.append(t)
    if (t := check_legal_keywords(message_text)) is not None:
        out.append(t)
    if (t := check_dialog_loop(recent_bot_replies or [], dialog_loop_threshold)) is not None:
        out.append(t)
    if (t := check_explicit_handoff(message_text)) is not None:
        out.append(t)
    if (t := check_silence_after_24h(silent_hours)) is not None:
        out.append(t)

    return out
