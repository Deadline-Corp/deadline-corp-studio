"""6 pause/resume strategies (Notion §13, Phase 6, 2026-05-26).

When a lead goes silent, classify WHY (one of 6 pause types) and plan
the next outbound attempt accordingly. Pure functions; the cron worker
in Phase 7 calls compute_next_attempt(...) per silent conversation.

Notion §13 strategies:
    1. unexplained         — пропал без объяснений (most common)
    2. busy_later          — лид сказал «занят / позже»
    3. named_date          — лид назвал конкретную дату возврата
    4. awaiting_our_info   — лид ждёт инфо от нас
    5. unanswered_question — лид не ответил на конкретный вопрос
    6. operator_pause      — оператор поставил на паузу
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional


PauseType = Literal[
    "unexplained",
    "busy_later",
    "named_date",
    "awaiting_our_info",
    "unanswered_question",
    "operator_pause",
]


# Default plans — overridable via tenant config.pause_strategies.
# All values in hours unless suffixed _d (days).
DEFAULT_TIMINGS: dict[str, dict[str, float]] = {
    "unexplained":         {"first_h": 4,    "second_d": 1,  "third_d": 3},
    "busy_later":          {"first_h": 24,                                   },
    "named_date":          {                                                  },  # specific date — handler computes
    "awaiting_our_info":   {"first_h": 24,   "second_d": 3                   },
    "unanswered_question": {"first_h": 5                                     },
    "operator_pause":      {                                                  },  # wait for explicit release
}


@dataclass(frozen=True)
class NextAttempt:
    """When and how to attempt the next outbound contact."""
    due_at: Optional[datetime]              # None = wait for external signal (named_date, operator)
    attempt_number: int                      # 1-based
    angle_change: bool                       # change topic / framing on this attempt
    channel_switch: bool                     # move to a different channel
    reason: str


def _hours_from(timings: dict[str, float], attempt_number: int) -> Optional[float]:
    """Lookup the configured hours-from-now for this attempt number."""
    if attempt_number == 1:
        h = timings.get("first_h")
        if h is None and "first_d" in timings:
            h = timings["first_d"] * 24
        return h
    if attempt_number == 2:
        h = timings.get("second_h")
        if h is None and "second_d" in timings:
            h = timings["second_d"] * 24
        return h
    if attempt_number == 3:
        h = timings.get("third_h")
        if h is None and "third_d" in timings:
            h = timings["third_d"] * 24
        return h
    return None


def get_timings(
    pause_type: str,
    config_pause: Optional[dict] = None,
) -> dict[str, float]:
    """Merge default timings with tenant config overrides."""
    defaults = DEFAULT_TIMINGS.get(pause_type, {}).copy()
    if config_pause and pause_type in config_pause:
        # config keys use first_attempt_h / second_attempt_d etc — normalise
        cfg = config_pause[pause_type] or {}
        for src_key, tgt_key in [
            ("first_attempt_h", "first_h"),
            ("first_attempt_d", "first_d"),
            ("second_attempt_h", "second_h"),
            ("second_attempt_d", "second_d"),
            ("third_attempt_h", "third_h"),
            ("third_attempt_d", "third_d"),
        ]:
            if src_key in cfg:
                defaults[tgt_key] = float(cfg[src_key])
    return defaults


def compute_next_attempt(
    *,
    pause_type: str,
    attempt_number: int,
    last_attempt_at: datetime,
    named_date: Optional[datetime] = None,
    config_pause: Optional[dict] = None,
) -> Optional[NextAttempt]:
    """Schedule the next outbound attempt for a paused conversation.

    attempt_number is the upcoming attempt's 1-based index. Returns None
    when no further auto-attempt should fire (operator-controlled types
    or attempts exhausted).
    """
    # Operator-controlled — wait for /release command
    if pause_type == "operator_pause":
        return NextAttempt(
            due_at=None, attempt_number=attempt_number,
            angle_change=False, channel_switch=False,
            reason="operator_pause — waiting for /release",
        )

    # Named date — handler-provided datetime
    if pause_type == "named_date":
        if named_date is None:
            return None
        return NextAttempt(
            due_at=named_date, attempt_number=attempt_number,
            angle_change=False, channel_switch=False,
            reason=f"named_date — ping on the lead's named date {named_date.isoformat()}",
        )

    timings = get_timings(pause_type, config_pause)
    hours = _hours_from(timings, attempt_number)
    if hours is None:
        return None  # no more attempts configured

    due_at = last_attempt_at + timedelta(hours=hours)

    # Angle change / channel switch rules (Notion §13):
    #   - unexplained: 1st same; 2nd angle change; 3rd channel switch
    #   - busy_later: 1st angle change (since "позже" implies same topic won't help)
    #   - awaiting_our_info: 1st straight ping; 2nd angle change
    #   - unanswered_question: 1st rephrase (counts as angle change)
    angle_change = False
    channel_switch = False
    if pause_type == "unexplained":
        angle_change = attempt_number >= 2
        channel_switch = attempt_number >= 3
    elif pause_type == "busy_later":
        angle_change = True
    elif pause_type == "awaiting_our_info":
        angle_change = attempt_number >= 2
    elif pause_type == "unanswered_question":
        angle_change = True  # rephrase

    return NextAttempt(
        due_at=due_at,
        attempt_number=attempt_number,
        angle_change=angle_change,
        channel_switch=channel_switch,
        reason=(
            f"{pause_type} attempt #{attempt_number} in {hours}h "
            f"(angle_change={angle_change}, channel_switch={channel_switch})"
        ),
    )


# =============================================================================
# Classification from signal
# =============================================================================

# Heuristics for detecting pause_type from the last lead message.
BUSY_LATER_RU = ("занят", "позже", "потом", "не сейчас", "перезвоните", "напишите позже")
BUSY_LATER_EN = ("busy", "later", "not now", "call back", "ping me later")

NAMED_DATE_HINTS_RU = ("в понедельник", "во вторник", "в среду", "в четверг",
                      "в пятницу", "на следующей неделе", "после", "через")
NAMED_DATE_HINTS_EN = ("monday", "tuesday", "wednesday", "thursday", "friday",
                      "next week", "after", "in a week")


def classify_pause(
    *,
    last_lead_message: Optional[str] = None,
    operator_paused: bool = False,
    has_named_date: bool = False,
    we_asked_question_last: bool = False,
    we_promised_info: bool = False,
) -> str:
    """Heuristically pick a PauseType from available signals.

    Priority (most specific first):
        operator_paused      → operator_pause
        has_named_date       → named_date
        last msg ∈ BUSY/LATER → busy_later
        we_promised_info     → awaiting_our_info
        we_asked_question_last → unanswered_question
        else                 → unexplained (default)
    """
    if operator_paused:
        return "operator_pause"
    if has_named_date:
        return "named_date"

    text = (last_lead_message or "").lower()
    if text:
        for kw in BUSY_LATER_RU + BUSY_LATER_EN:
            if kw in text:
                return "busy_later"

    if we_promised_info:
        return "awaiting_our_info"
    if we_asked_question_last:
        return "unanswered_question"

    return "unexplained"
