"""Lead temperature dynamics (Phase 5, 2026-05-26).

Implements Notion §7 — six-state thermometer that goes up on engagement
signals and decays on silence. Pure functions; the cron worker in Phase
6/7 calls these and writes the result to Customer.lead_temperature.

States (cold-to-hot, plus frozen as a side branch):
    cold   ← default, no engagement
    warm   ← 2+ content replies
    hot    ← lead asked about cena / сроки / portfolio / etc
    ready  ← lead said "что нужно чтобы начать" / "ready to begin"
    client ← prepayment received (set by funnel state machine, not here)
    frozen ← 21+ days silence

Decay rules:
    14+ days silence → step down one level (hot→warm, warm→cold, etc)
    21+ days silence → frozen (regardless of current temperature)
    client never auto-decays — they're a paying customer

Upgrade rules:
    Never automatic downgrade. detect_from_message returns max(current,
    candidate) so a single hot keyword can't be undone by a later cold
    message.
"""

from __future__ import annotations

from typing import Optional


# Order matters — used by step_down and is_higher to compare levels.
# Note: "client" sits at the top but is NOT a decay-eligible state, and
# "frozen" lives outside this ordering (set/reset separately).
ACTIVE_TEMPERATURES: tuple[str, ...] = ("cold", "warm", "hot", "ready", "client")
ALL_TEMPERATURES: tuple[str, ...] = ACTIVE_TEMPERATURES + ("frozen",)


# =============================================================================
# Comparison
# =============================================================================

def is_higher(t1: str, t2: str) -> bool:
    """True if t1 is hotter than t2 in the cold→client ordering.

    Frozen is incomparable — it's a side branch, not part of the ramp.
    """
    if t1 == "frozen" or t2 == "frozen":
        return False
    try:
        return ACTIVE_TEMPERATURES.index(t1) > ACTIVE_TEMPERATURES.index(t2)
    except ValueError:
        return False


def upgrade_to(current: str, candidate: str) -> str:
    """Return the hotter of (current, candidate). Never downgrades.

    Special case: frozen exits the moment any candidate is provided —
    the lead is no longer silent, so frozen no longer applies. We don't
    drop them straight to client though; the candidate sets the new floor.
    """
    if current == "frozen":
        # Coming out of frozen — start fresh at whatever candidate suggests
        return candidate
    if candidate == "frozen":
        # Don't go to frozen via upgrade (frozen is set by decay, not engagement)
        return current
    if is_higher(candidate, current):
        return candidate
    return current


# =============================================================================
# Detection from message
# =============================================================================

def detect_from_message(
    *,
    current_temperature: str,
    content_replies_so_far: int,
    message_text: Optional[str],
    config_temperature: dict,
) -> str:
    """Notion §7 trigger rules. Returns target temperature (>= current).

    Priority (hottest first):
      ready: lead said one of the phrases in config.ready.phrases
      hot:   lead used one of the keywords in config.hot.keywords
      warm:  lead has sent >= config.warm.content_replies_count messages
      cold:  default (no trigger fired) — current temperature unchanged

    All checks are case-insensitive substring match on message_text.
    """
    triggers = (config_temperature or {}).get("triggers", {})
    text_lower = (message_text or "").lower()

    # 1. ready — hottest, explicit "I want to start"
    ready_cfg = triggers.get("ready", {}) or {}
    for phrase in ready_cfg.get("phrases", []) or []:
        if phrase.lower() in text_lower:
            return upgrade_to(current_temperature, "ready")

    # 2. hot — price / timeline / portfolio inquiry
    hot_cfg = triggers.get("hot", {}) or {}
    for kw in hot_cfg.get("keywords", []) or []:
        if kw.lower() in text_lower:
            return upgrade_to(current_temperature, "hot")

    # 3. warm — engagement threshold
    warm_cfg = triggers.get("warm", {}) or {}
    threshold = int(warm_cfg.get("content_replies_count", 2))
    if content_replies_so_far >= threshold:
        return upgrade_to(current_temperature, "warm")

    return current_temperature


# =============================================================================
# Decay
# =============================================================================

def step_down(temp: str) -> str:
    """One step down the ACTIVE_TEMPERATURES list, floored at 'cold'.

    Special cases:
      - 'client' does NOT step down (they paid us)
      - 'frozen' returns 'frozen' (separate state, decay doesn't compound)
      - unknown values return themselves (defensive)
    """
    if temp == "frozen":
        return "frozen"
    if temp == "client":
        return "client"
    if temp not in ACTIVE_TEMPERATURES:
        return temp
    idx = ACTIVE_TEMPERATURES.index(temp)
    if idx == 0:
        return "cold"
    return ACTIVE_TEMPERATURES[idx - 1]


def apply_decay(
    current_temperature: str,
    silent_days: float,
    decay_days: int = 14,
    frozen_after_days: int = 21,
) -> str:
    """Silence decay (Notion §7).

      silent_days >= frozen_after_days  → frozen
      silent_days >= decay_days         → step_down one level
      silent_days < decay_days          → unchanged

    'client' temperature is preserved even on silence — they're a paying
    customer, no longer a lead in the funnel sense.
    """
    if current_temperature == "client":
        return "client"
    if current_temperature == "frozen":
        return "frozen"
    if silent_days >= frozen_after_days:
        return "frozen"
    if silent_days >= decay_days:
        return step_down(current_temperature)
    return current_temperature


# =============================================================================
# Tenant config integration
# =============================================================================

def decide_from_tenant_config(
    *,
    current_temperature: str,
    content_replies_so_far: int,
    message_text: Optional[str],
    silent_days: float,
    tenant_config: dict,
) -> str:
    """Composite: apply engagement upgrade first, then decay.

    Engagement always wins over decay — if the lead just sent a hot
    keyword AFTER being silent 30 days, we treat them as 'hot' (exiting
    frozen) rather than dropping to frozen.

    Returns the final temperature value to write to Customer.lead_temperature.
    """
    temp_cfg = (tenant_config or {}).get("temperature", {}) or {}

    # 1. Engagement upgrade
    upgraded = detect_from_message(
        current_temperature=current_temperature,
        content_replies_so_far=content_replies_so_far,
        message_text=message_text,
        config_temperature=temp_cfg,
    )
    if upgraded != current_temperature:
        # Engagement happened; ignore decay this turn
        return upgraded

    # 2. Decay (no engagement signal this turn)
    decay_days = int(temp_cfg.get("decay_days", 14))
    frozen_after = int(temp_cfg.get("frozen_after_days", 21))
    return apply_decay(
        current_temperature=current_temperature,
        silent_days=silent_days,
        decay_days=decay_days,
        frozen_after_days=frozen_after,
    )
