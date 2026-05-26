"""Lead warming scheduler (Phase 6, 2026-05-26).

Implements Notion §14 — re-engagement cadence for silent leads, bucketed
by temperature (hot/warm/cold/frozen). Cron job calls plan_warming(...)
for every candidate customer; if it returns a WarmingAction, the worker
creates a CRM task or sends an outbound message.

Pure functions — DB lookup of "silent customers" + the actual outbound
send happens in the cron worker (Phase 7+).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional


@dataclass(frozen=True)
class WarmingAction:
    """One scheduled warming touch."""
    customer_id: str
    temperature: str           # current temperature bucket (hot/warm/cold/frozen)
    format: str                # direct_followup / case_study / content / offer / special_offer / archive
    suggested_due_at: datetime
    reason: str


def should_warm_now(
    *,
    current_temperature: str,
    silent_days: float,
    last_warmed_days_ago: Optional[float],
    config_warming: dict,
) -> tuple[bool, Optional[str], str]:
    """Decide whether to warm this lead RIGHT NOW.

    Notion §14 table:
        Hot     silence<14d   cadence 1-2d  format: direct_followup
        Warm    silence<60d   cadence 7d    format: case_study / content
        Cold    silence<180d  cadence 21d   format: content + offer
        Frozen  silence>180d  cadence 90d   format: special_offer / archive

    Returns (should_warm, format, reason).
    """
    bucket = (config_warming or {}).get(current_temperature, {})
    if not bucket:
        return False, None, f"no warming config for temperature={current_temperature!r}"

    silence_max = float(bucket.get("silence_max_d", 9999))
    cadence = float(bucket.get("cadence_d", 7))
    formats = bucket.get("formats", []) or ["direct_followup"]

    # The lead has been silent past this bucket's window — temperature decay
    # will have already moved them to a colder bucket. Skip here.
    if silent_days > silence_max:
        return False, None, (
            f"silent_days={silent_days:.1f} > {current_temperature} bucket max {silence_max}d "
            f"— lead should already have decayed to next bucket"
        )

    # Honour cadence — don't spam someone we already warmed yesterday
    if last_warmed_days_ago is not None and last_warmed_days_ago < cadence:
        return False, None, (
            f"warmed {last_warmed_days_ago:.1f}d ago, "
            f"{current_temperature} bucket cadence={cadence}d"
        )

    return True, formats[0], (
        f"warming bucket={current_temperature} silent={silent_days:.1f}d cadence={cadence}d"
    )


def plan_warming(
    *,
    customer_id: str,
    current_temperature: str,
    silent_days: float,
    last_warmed_days_ago: Optional[float],
    config_warming: dict,
    now: Optional[datetime] = None,
) -> Optional[WarmingAction]:
    """Return a WarmingAction if this customer should be touched now, else None.

    Used in batch by the cron worker:
        for customer in candidates:
            action = plan_warming(...)
            if action: enqueue(action)
    """
    should, fmt, reason = should_warm_now(
        current_temperature=current_temperature,
        silent_days=silent_days,
        last_warmed_days_ago=last_warmed_days_ago,
        config_warming=config_warming,
    )
    if not should:
        return None
    return WarmingAction(
        customer_id=customer_id,
        temperature=current_temperature,
        format=fmt or "direct_followup",
        suggested_due_at=(now or datetime.now(timezone.utc)),
        reason=reason,
    )
