"""Lead scoring + interaction-type detection (Phase 5, 2026-05-26).

Implements Notion §5 (scoring) and Notion §4 (InteractionType classification
at first touch). Pure functions — tenant config + per-message inputs in,
ScoreComponents out. No DB / CRM side-effects.

Score formula:
    total = int((base_score(interaction_type) + content_score(message))
                * source_factor(channel))

Decay:
    -decay_per_48h per 48 hours of silence, floored at min_score (default 0).

Initial scoring on a new lead:
    score = compute_score(...)  — uses interaction_type set at first touch.

Per-message updates:
    new_score = current_score + content_score(message_text) * source_factor
    (base part is only counted at creation, not re-added every message).

InteractionType detection feeds into the same scoring — it's set ONCE at
customer creation from first-touch signals; later messages don't change
the interaction_type (Notion §4: "Set once at first touch, never changes").
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Optional


# =============================================================================
# Score data
# =============================================================================

@dataclass(frozen=True)
class ScoreComponents:
    """Breakdown of a score calculation. Stored on Customer.lead_score (total only)."""
    base: int                                     # base_by_interaction_type[type]
    content: int                                  # sum of matched keyword points
    source_factor_x10: int                        # source weight * 10, for compact int storage
    total: int                                    # final score (base + content) * factor
    matched_keywords: tuple[str, ...] = ()        # diagnostic: what fired the content points


# =============================================================================
# Building blocks
# =============================================================================

def base_score(interaction_type: str, base_map: Mapping[str, int]) -> int:
    """Notion §5 — base points by interaction type (P1..P6 / HardStop)."""
    return int(base_map.get(interaction_type, 0))


def content_score(
    message_text: Optional[str],
    keyword_map: Mapping[str, int],
) -> tuple[int, tuple[str, ...]]:
    """Sum of points for every keyword that appears (case-insensitive) in message_text.

    Returns (total_points, matched_keywords_tuple).
    Same keyword counted once even if it appears multiple times.
    """
    if not message_text:
        return 0, ()
    text_lower = message_text.lower()
    matched: list[str] = []
    total = 0
    for kw, pts in keyword_map.items():
        if kw.lower() in text_lower:
            matched.append(kw)
            total += int(pts)
    return total, tuple(matched)


def source_factor(
    channel: str,
    weights: Mapping[str, float],
    default: float = 1.0,
) -> float:
    """Per-channel multiplier from tenant config.scoring.source_weight."""
    return float(weights.get(channel, default))


# =============================================================================
# Composite calculations
# =============================================================================

def compute_initial_score(
    *,
    interaction_type: str,
    channel: str,
    first_message_text: Optional[str],
    config_scoring: dict,
) -> ScoreComponents:
    """Score for a brand-new lead — base from interaction_type, content from first message."""
    base_map = config_scoring.get("base_by_interaction_type", {})
    keyword_map = config_scoring.get("content_keywords", {})
    source_weights = config_scoring.get("source_weight", {})

    base = base_score(interaction_type, base_map)
    content, matched = content_score(first_message_text, keyword_map)
    factor = source_factor(channel, source_weights)
    total = int((base + content) * factor)
    return ScoreComponents(
        base=base,
        content=content,
        source_factor_x10=int(factor * 10),
        total=total,
        matched_keywords=matched,
    )


def add_message_score(
    *,
    current_score: int,
    channel: str,
    message_text: Optional[str],
    config_scoring: dict,
) -> tuple[int, tuple[str, ...]]:
    """Increment score from a new message (base is NOT re-added; only content × source).

    Returns (new_total_score, matched_keywords).
    """
    keyword_map = config_scoring.get("content_keywords", {})
    source_weights = config_scoring.get("source_weight", {})

    content, matched = content_score(message_text, keyword_map)
    if content == 0:
        return current_score, ()
    factor = source_factor(channel, source_weights)
    delta = int(content * factor)
    return current_score + delta, matched


def apply_decay(
    current_score: int,
    hours_silent: float,
    decay_per_48h: int = -1,
    min_score: int = 0,
) -> int:
    """Notion §5 — silence decay. Default -1 per 48h, floored at min_score.

    Idempotent: calling repeatedly with the same hours_silent gives the same result.
    Caller is responsible for tracking the last_decay_at timestamp.
    """
    if hours_silent < 48 or current_score <= min_score:
        return current_score
    intervals = int(hours_silent // 48)
    new_score = current_score + decay_per_48h * intervals
    return max(new_score, min_score)


def is_qualified(score: int, qualification_threshold: int) -> bool:
    """True if score has reached the qualification cutoff (Notion §5)."""
    return score >= qualification_threshold


# =============================================================================
# InteractionType detection (Notion §4) — set once at first touch
# =============================================================================

def detect_interaction_type(
    *,
    channel: str,
    is_explicit_request: bool = False,
    is_ad_click: bool = False,
    is_form_submission: bool = False,
    is_cold_return: bool = False,
    is_public_comment: bool = False,
    is_reaction_or_story: bool = False,
    is_outbound: bool = False,
    is_hard_stop: bool = False,
) -> str:
    """Map first-touch signals to P1..P6 / HardStop.

    Notion §4 taxonomy:
        P1       — direct request / ad click           (highest priority queue)
        P2       — form submission, no explicit need
        P3       — return of a cold/archived lead
        P4       — neutral reply / public comment
        P5       — stories / reactions (passive)
        P6       — we write first (outbound campaign)
        HardStop — explicit refusal — archive immediately

    Priority: HardStop > P6 > P1 > P2 > P3 > P4 > P5.
    Default for a DM with no signals → P2 (treat as form-equivalent).
    """
    if is_hard_stop:
        return "HardStop"
    if is_outbound:
        return "P6"
    if is_explicit_request or is_ad_click:
        return "P1"
    if is_form_submission:
        return "P2"
    if is_cold_return:
        return "P3"
    if is_public_comment:
        return "P4"
    if is_reaction_or_story:
        return "P5"
    return "P2"  # default — DM without explicit signals
