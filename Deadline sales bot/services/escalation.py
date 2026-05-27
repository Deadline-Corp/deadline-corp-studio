"""Per-turn escalation runner (Phase 9c, 2026-05-27).

Wraps services.triggers.check_all_triggers with in-memory rate-limiting
so the same trigger type can't spam the operator topic. Hot path calls
run_escalation_checks() once per turn after the bot reply is persisted.

Rate limit: a (conversation_id, trigger_type) pair fires at most once per
RATE_LIMIT_WINDOW_SEC seconds. Reset on process restart — acceptable for
MVP, alternative would be a DB column on Conversation. The window is
30 minutes by default — long enough to avoid noise, short enough that
a new genuine signal isn't suppressed for hours.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

from services.triggers import EscalationTrigger, check_all_triggers


logger = logging.getLogger(__name__)


RATE_LIMIT_WINDOW_SEC: float = 30 * 60   # 30 minutes per (conv, type)


# In-memory rate-limit cache. Key: (conversation_id, trigger_type). Value:
# UNIX timestamp of last fire. Trimmed lazily — we never accumulate more
# than a few thousand entries unless the bot has explosive conversation
# turnover, in which case the memory cost is still tiny.
_last_fired: dict[tuple[str, str], float] = {}


def _should_fire(conversation_id: str, trigger_type: str, now: float) -> bool:
    """Rate-limit check + bookkeeping."""
    key = (conversation_id, trigger_type)
    last = _last_fired.get(key)
    if last is not None and (now - last) < RATE_LIMIT_WINDOW_SEC:
        return False
    _last_fired[key] = now
    return True


def reset_rate_limit() -> None:
    """For tests — clear the cache between cases."""
    _last_fired.clear()


def run_escalation_checks(
    *,
    conversation_id: str,
    confidence: Optional[float] = None,
    message_text: Optional[str] = None,
    recent_lead_messages: Optional[list[str]] = None,
    recent_bot_replies: Optional[list[str]] = None,
    estimated_budget_rub: Optional[int] = None,
    silent_hours: float = 0,
    tenant_config: Optional[dict] = None,
    now: Optional[float] = None,
) -> list[EscalationTrigger]:
    """Run 7 trigger checks; return only those that pass the rate-limit gate.

    Caller is responsible for actually notifying — usually by mirroring a
    formatted alert into the operator's forum topic.
    """
    cfg = tenant_config or {}
    operator_cfg = cfg.get("operator_mode") or {}
    discount_cfg = cfg.get("discount") or {}

    confidence_gate = float(operator_cfg.get("confidence_gate", 0.7))
    budget_threshold = int(discount_cfg.get("auto_above_budget_threshold", 1_000_000))

    fired = check_all_triggers(
        confidence=confidence,
        message_text=message_text,
        recent_lead_messages=recent_lead_messages,
        recent_bot_replies=recent_bot_replies,
        estimated_budget_rub=estimated_budget_rub,
        silent_hours=silent_hours,
        confidence_gate=confidence_gate,
        budget_threshold_rub=budget_threshold,
    )
    if not fired:
        return []

    now_ts = now if now is not None else time.time()
    rate_limited: list[EscalationTrigger] = []
    for t in fired:
        if _should_fire(conversation_id, t.type, now_ts):
            rate_limited.append(t)
        else:
            logger.debug(
                "[escalation] rate-limited %s in conv=%s — last fired < %.0fs ago",
                t.type, conversation_id, RATE_LIMIT_WINDOW_SEC,
            )
    return rate_limited


def format_alert_text(trigger: EscalationTrigger) -> str:
    """Build a short alert string suitable for posting in an operator topic."""
    icon = "🚨" if trigger.severity == "alert" else "⚠️"
    return f"{icon} ESCALATION · {trigger.type}\n{trigger.reason}"
