"""Per-turn lead signal updates (Phase 9, 2026-05-27).

apply_signals_on_turn() is the ONE entry point called from _handle_message
right after the lead's message is persisted to DB. It updates Customer
fields driven by Notion §4 (interaction_type, set once), §5 (lead_score,
incremental) and §7 (lead_temperature, dynamic with triggers).

All four customer columns get mutated in place. DB commit happens later
in _handle_message — this module never commits on its own. Callers also
decide if they want to dispatch the updated values to CRM via
crm_dispatch.dispatch_temperature_change.

Pure-ish: takes customer + conversation + raw inputs, returns a diff
record so caller can log what changed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

from services.scoring import (
    add_message_score,
    compute_initial_score,
    detect_interaction_type,
)
from services.temperature import decide_from_tenant_config as decide_temperature


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SignalUpdate:
    """Diff between pre-turn and post-turn customer state — for logging + dispatch."""
    interaction_type: str          # always set, even if unchanged (first-touch detection)
    old_score: int
    new_score: int
    old_temperature: str
    new_temperature: str
    matched_keywords: tuple[str, ...] = ()
    is_first_touch: bool = False


def _count_lead_messages(recent_messages: list) -> int:
    """Count how many user-role messages are in the recent history."""
    count = 0
    for m in recent_messages:
        role = m.role.value if hasattr(m.role, "value") else str(m.role)
        if role == "user":
            count += 1
    return count


def _detect_first_touch_signals(
    *,
    channel: str,
    message_type: str,
    has_email_already: bool,
    message_text: str,
) -> dict:
    """Map hot-path runtime signals into the boolean inputs that
    services.scoring.detect_interaction_type expects.

    Conservative defaults — default everything to False; if a clear signal
    is visible we set the corresponding flag. detect_interaction_type
    falls back to P2 if nothing fires, which is the right default for an
    inbound DM with no specific markers.
    """
    text_lower = (message_text or "").lower()
    is_public_comment = (message_type == "comment")
    is_form_submission = (channel == "website" and has_email_already)
    # "Hard stop" — explicit refusal in the very first message is rare but real
    hard_stop_phrases = (
        "не пишите", "не обращайтесь", "stop messaging", "do not contact",
        "удалите меня", "unsubscribe",
    )
    is_hard_stop = any(p in text_lower for p in hard_stop_phrases)
    # Explicit request — clearly transactional language
    explicit_phrases = (
        "хочу", "нужен", "мне нужно", "ищу", "want to", "need ", "looking for",
        "interested in", "помогите с", "разработать",
    )
    is_explicit_request = any(p in text_lower for p in explicit_phrases)

    return dict(
        channel=channel,
        is_public_comment=is_public_comment,
        is_form_submission=is_form_submission,
        is_hard_stop=is_hard_stop,
        is_explicit_request=is_explicit_request,
        # The rest stay False — we don't have reliable signal for them yet
        is_ad_click=False,
        is_cold_return=False,
        is_reaction_or_story=False,
        is_outbound=False,
    )


def apply_signals_on_turn(
    *,
    customer: Any,                          # db.models.Customer (mutated in place)
    recent_messages: list,                  # last N messages from get_recent_messages
    lead_message_text: str,
    channel: str,
    message_type: str,
    tenant_config: dict,
    silent_days_before_this_turn: float = 0.0,
) -> SignalUpdate:
    """Update customer.interaction_type / lead_score / lead_temperature for this turn.

    Called inside _handle_message AFTER append_message(role='user') so
    recent_messages already includes the current message.

    Returns a SignalUpdate describing what changed — caller uses it for
    logging and to decide if CRM dispatch is needed.
    """
    scoring_cfg = (tenant_config or {}).get("scoring", {}) or {}

    lead_msg_count = _count_lead_messages(recent_messages)
    is_first_touch = (lead_msg_count <= 1)

    old_interaction_type = customer.interaction_type or "P2"
    old_score = customer.lead_score or 0
    old_temperature = customer.lead_temperature or "cold"

    new_interaction_type = old_interaction_type
    new_score = old_score
    matched_keywords: tuple[str, ...] = ()

    # --- Notion §4: interaction_type detection (only on first touch) ---
    if is_first_touch:
        first_touch_signals = _detect_first_touch_signals(
            channel=channel,
            message_type=message_type,
            has_email_already=bool(customer.email),
            message_text=lead_message_text,
        )
        new_interaction_type = detect_interaction_type(**first_touch_signals)
        customer.interaction_type = new_interaction_type

        # Notion §5: initial score uses base from interaction_type + content
        components = compute_initial_score(
            interaction_type=new_interaction_type,
            channel=channel,
            first_message_text=lead_message_text,
            config_scoring=scoring_cfg,
        )
        new_score = components.total
        matched_keywords = components.matched_keywords
        customer.lead_score = new_score
    else:
        # Returning lead — add incremental score from this message (no base re-add)
        new_score, matched_keywords = add_message_score(
            current_score=old_score,
            channel=channel,
            message_text=lead_message_text,
            config_scoring=scoring_cfg,
        )
        customer.lead_score = new_score

    # --- Notion §7: temperature ---
    # `content_replies_so_far` is the count of substantive lead replies seen
    # in this conversation; for first touch that's 1.
    new_temperature = decide_temperature(
        current_temperature=old_temperature,
        content_replies_so_far=lead_msg_count,
        message_text=lead_message_text,
        silent_days=silent_days_before_this_turn,
        tenant_config=tenant_config,
    )
    if new_temperature != old_temperature:
        customer.lead_temperature = new_temperature

    return SignalUpdate(
        interaction_type=new_interaction_type,
        old_score=old_score,
        new_score=new_score,
        old_temperature=old_temperature,
        new_temperature=new_temperature,
        matched_keywords=matched_keywords,
        is_first_touch=is_first_touch,
    )
