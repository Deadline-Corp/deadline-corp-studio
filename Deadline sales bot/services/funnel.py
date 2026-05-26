"""Lead funnel state machine (Phase 4, 2026-05-26).

Pure functions over LeadStage values from Notion §20. No DB / CRM I/O —
those side-effects are wired in by the caller (hot path in main.py and
the event queue worker in Phase 7).

Why pure: each decide_* function takes plain inputs and returns a
TransitionDecision dataclass. This makes the state machine trivially
testable, replayable on historical conversations, and easy to compose
with the queue's retry/dedup logic without worrying about idempotency
of side-effects.

Hierarchy of authority for transitions:
  1. Operator override (via /admin/training or future /admin/funnel UI):
     can move to ANY stage at any time. validate_transition is bypassed.
  2. Bot auto-transitions (this module's decide_* functions): restricted
     to forward-only motion plus 'lost'. Backward = operator only.
  3. CRM-side stage change (operator changes stage in HubSpot UI): we
     pull this back through a webhook (Phase 7+) and treat it as case 1.

Lost stage requires lost_reason — Notion §20 split is enforced here.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterable, Optional


logger = logging.getLogger(__name__)


# =============================================================================
# Stage / reason taxonomy — must match services/crm/base.py LeadStage / LostReason
# =============================================================================

ACTIVE_STAGES: tuple[str, ...] = (
    "new_lead",
    "in_dialog",
    "qualified",
    "nda",
    "on_call",
    "tz_approved",
    "proposal",
    "prepayment",
    "in_work",
    "completed_won",
    "post_sale",
)
TERMINAL_LOST: str = "lost"
ALL_STAGES: tuple[str, ...] = ACTIVE_STAGES + (TERMINAL_LOST,)

LOST_REASONS: tuple[str, ...] = (
    "price",
    "not_our_format",
    "competitor",
    "delayed",
    "no_budget",
    "hard_stop",
)


# Allowed forward / lost transitions for auto-decisions. Skips are allowed
# where Notion §20 / our practice tolerates them: qualified can skip NDA;
# in_dialog can skip straight to qualified (handoff classifier fires).
ALLOWED_AUTO_TRANSITIONS: dict[str, frozenset[str]] = {
    "new_lead":      frozenset({"in_dialog", "qualified", TERMINAL_LOST}),
    "in_dialog":     frozenset({"qualified", TERMINAL_LOST}),
    # NDA is optional per project_type — see determine_next_after_qualified()
    "qualified":     frozenset({"nda", "on_call", TERMINAL_LOST}),
    "nda":           frozenset({"on_call", TERMINAL_LOST}),
    "on_call":       frozenset({"tz_approved", TERMINAL_LOST}),
    "tz_approved":   frozenset({"proposal", TERMINAL_LOST}),
    "proposal":      frozenset({"prepayment", TERMINAL_LOST}),
    "prepayment":    frozenset({"in_work", TERMINAL_LOST}),
    "in_work":       frozenset({"completed_won", TERMINAL_LOST}),
    "completed_won": frozenset({"post_sale"}),    # won projects never auto-lose
    "post_sale":     frozenset(),                  # terminal-positive
    TERMINAL_LOST:   frozenset(),                  # terminal-negative; operator reopens
}


# =============================================================================
# Decision objects
# =============================================================================

@dataclass(frozen=True)
class TransitionDecision:
    """Pure data — caller decides what to do with it."""
    should_transition: bool
    target_stage: Optional[str] = None
    lost_reason: Optional[str] = None
    reason: str = ""  # human-readable diagnostic, logged when applied

    @classmethod
    def no_change(cls, reason: str = "") -> "TransitionDecision":
        return cls(False, None, None, reason)


# =============================================================================
# Validation
# =============================================================================

def can_auto_transition(from_stage: str, to_stage: str) -> bool:
    """True iff the bot is allowed to move from_stage -> to_stage without operator action."""
    if from_stage == to_stage:
        return False
    return to_stage in ALLOWED_AUTO_TRANSITIONS.get(from_stage, frozenset())


def validate_transition(
    from_stage: str,
    to_stage: str,
    lost_reason: Optional[str] = None,
    operator_override: bool = False,
) -> None:
    """Raise ValueError if the transition is illegal.

    With operator_override=True only the basic stage / reason vocabulary
    is checked — operators can move backward, jump arbitrarily, etc.
    """
    if from_stage not in ALL_STAGES:
        raise ValueError(f"Unknown source stage: {from_stage!r}")
    if to_stage not in ALL_STAGES:
        raise ValueError(f"Unknown target stage: {to_stage!r}")

    if to_stage == TERMINAL_LOST:
        if not lost_reason:
            raise ValueError("Transition to 'lost' requires lost_reason")
        if lost_reason not in LOST_REASONS:
            raise ValueError(
                f"Invalid lost_reason: {lost_reason!r}. Valid: {sorted(LOST_REASONS)}"
            )

    if operator_override:
        return  # operator can do anything below the vocabulary level

    if not can_auto_transition(from_stage, to_stage):
        raise ValueError(
            f"Invalid auto-transition: {from_stage} -> {to_stage}. "
            f"Allowed: {sorted(ALLOWED_AUTO_TRANSITIONS.get(from_stage, []))}"
        )


# =============================================================================
# Stage-routing helpers
# =============================================================================

def should_skip_nda(
    project_type: Optional[str],
    require_nda_for: Iterable[str],
) -> bool:
    """True if NDA stage should be skipped for this project_type.

    Default tenant config.yaml: require_nda_for_project_types: [ai_agents].
    So Web and Automation projects skip NDA; AI Agent projects don't.
    """
    if not project_type:
        return True
    normalized = project_type.lower().strip()
    needs = {p.lower().strip() for p in require_nda_for}
    return normalized not in needs


def determine_next_after_qualified(
    project_type: Optional[str],
    require_nda_for: Iterable[str],
) -> str:
    """Where does the funnel send a qualified lead?

    Either straight to 'on_call' (most projects) or via 'nda' first.
    """
    return "on_call" if should_skip_nda(project_type, require_nda_for) else "nda"


# =============================================================================
# decide_* — auto-transition rules. Each is a pure function used at the
# decision point in the hot path / cron job.
# =============================================================================

def decide_on_lead_message(
    current_stage: str,
    lead_messages_so_far: int,
    auto_qualify_after: int,
) -> TransitionDecision:
    """Lead has just sent a message. Move new_lead -> in_dialog after threshold.

    auto_qualify_after defaults to 3 in tenant config — meaning the 3rd
    lead message (i.e. lead has clearly engaged) graduates us to in_dialog.
    Subsequent calls on in_dialog stage don't trigger anything here.
    """
    if current_stage == "new_lead" and lead_messages_so_far >= auto_qualify_after:
        return TransitionDecision(
            True,
            "in_dialog",
            reason=f"lead has sent {lead_messages_so_far} messages (>= {auto_qualify_after})",
        )
    return TransitionDecision.no_change()


def decide_on_handoff_classifier(
    current_stage: str,
    classifier_says_ready: bool,
) -> TransitionDecision:
    """handoff_classifier fired ready_for_handoff=True. Notion §21 trigger.

    From new_lead / in_dialog this fast-tracks the deal to qualified. From
    later stages it's a no-op — handoff might re-fire on a qualified lead
    if they reopen the chat, but the deal is already past that gate.
    """
    if classifier_says_ready and current_stage in {"new_lead", "in_dialog"}:
        return TransitionDecision(
            True,
            "qualified",
            reason="handoff_classifier signalled ready_for_handoff",
        )
    return TransitionDecision.no_change()


def decide_on_silence(
    current_stage: str,
    silent_days: int,
    silence_lost_threshold_d: int,
) -> TransitionDecision:
    """Warming cron found a silent lead. in_dialog + long silence -> lost(delayed).

    Notion §13 silent-unexplained type with the configured threshold.
    Other stages don't auto-lose on silence — operators decide for stages
    like proposal/prepayment/in_work where money/legal is involved.
    """
    if current_stage == "in_dialog" and silent_days >= silence_lost_threshold_d:
        return TransitionDecision(
            True,
            TERMINAL_LOST,
            "delayed",
            reason=f"silent for {silent_days}d on in_dialog (>= {silence_lost_threshold_d}d)",
        )
    return TransitionDecision.no_change()


def decide_on_hard_stop(current_stage: str) -> TransitionDecision:
    """Lead explicitly refused (Notion §4 HardStop / §13 hard stop type).

    From any active stage -> lost(hard_stop). From terminal-positive
    (post_sale) this is a no-op.
    """
    if current_stage in ACTIVE_STAGES:
        return TransitionDecision(
            True,
            TERMINAL_LOST,
            "hard_stop",
            reason="lead explicit refusal / hard stop",
        )
    return TransitionDecision.no_change()


def decide_on_prepayment_received(current_stage: str) -> TransitionDecision:
    """Operator confirms prepayment landed in our account."""
    if current_stage == "prepayment":
        return TransitionDecision(True, "in_work", reason="prepayment confirmed")
    return TransitionDecision.no_change()


def decide_on_project_completed(current_stage: str) -> TransitionDecision:
    """Operator marks project shipped."""
    if current_stage == "in_work":
        return TransitionDecision(True, "completed_won", reason="project delivered")
    return TransitionDecision.no_change()


def decide_post_sale_window(
    current_stage: str,
    days_since_completed: int,
    post_sale_window_d: int = 30,
) -> TransitionDecision:
    """completed_won + N days -> post_sale (upsell + agent program window)."""
    if current_stage == "completed_won" and days_since_completed >= post_sale_window_d:
        return TransitionDecision(
            True,
            "post_sale",
            reason=f"completed_won + {days_since_completed}d -> post_sale window",
        )
    return TransitionDecision.no_change()


# =============================================================================
# Tenant config integration — wraps decide_* with config-driven thresholds
# =============================================================================

def decide_from_tenant_config(
    current_stage: str,
    tenant_config: dict,
    *,
    lead_messages_so_far: int = 0,
    classifier_says_ready: bool = False,
    silent_days: int = 0,
    hard_stop_signal: bool = False,
    prepayment_received: bool = False,
    project_completed: bool = False,
    days_since_completed: int = 0,
) -> TransitionDecision:
    """Composite decision driver using thresholds from tenant config.yaml.

    The hot path passes whichever signals fired in this turn. We check
    them in priority order and return the FIRST positive decision —
    multiple signals in one turn collapse to a single transition.

    Priority (highest first):
        hard_stop > handoff_classifier > prepayment > project_completed >
        silence_lost > post_sale_window > lead_message_count
    """
    funnel_cfg = tenant_config.get("funnel", {}) if tenant_config else {}
    auto_qualify_after = int(funnel_cfg.get("auto_qualify_after_messages", 3))
    silence_lost_d = int(funnel_cfg.get("silence_lost_threshold_d", 7))

    # 1. Hard stop trumps everything
    if hard_stop_signal:
        d = decide_on_hard_stop(current_stage)
        if d.should_transition:
            return d

    # 2. Handoff classifier
    if classifier_says_ready:
        d = decide_on_handoff_classifier(current_stage, True)
        if d.should_transition:
            return d

    # 3. Prepayment confirmation
    if prepayment_received:
        d = decide_on_prepayment_received(current_stage)
        if d.should_transition:
            return d

    # 4. Project delivered
    if project_completed:
        d = decide_on_project_completed(current_stage)
        if d.should_transition:
            return d

    # 5. Silence lost
    if silent_days > 0:
        d = decide_on_silence(current_stage, silent_days, silence_lost_d)
        if d.should_transition:
            return d

    # 6. Post-sale upsell window
    if days_since_completed > 0:
        d = decide_post_sale_window(current_stage, days_since_completed)
        if d.should_transition:
            return d

    # 7. Engagement threshold (default route from new_lead)
    if lead_messages_so_far > 0:
        d = decide_on_lead_message(current_stage, lead_messages_so_far, auto_qualify_after)
        if d.should_transition:
            return d

    return TransitionDecision.no_change()
