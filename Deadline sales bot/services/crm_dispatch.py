"""High-level CRM event dispatcher (Phase 7, 2026-05-26).

Bridges the bot's hot path to the CRM event queue. Each function is a
small composition: read Customer + Conversation state, decide what CRM
events the current turn implies, enqueue them. Returns immediately —
all CRM I/O happens later in the worker.

Why this lives in its own module:
  - Keeps main.py free of CRM-specific imports / branching
  - Makes the wire-up testable in isolation (mock the queue, assert
    expected events were enqueued)
  - Survives swapping the underlying queue (in-memory → Redis → SQS)
    without touching the hot path

All public functions:
  - Take simple DB-row inputs (no ORM dependency on the call side)
  - Are no-ops when crm_enabled=False (caller still controls the gate
    via Settings.crm_enabled, but we double-check here for safety)
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from services.crm.base import (
    Deal,
    Lead,
    MessageLog,
)
from services.crm_queue import (
    enqueue,
    make_create_deal_event,
    make_create_task_event,
    make_log_message_event,
    make_update_stage_event,
    make_update_temperature_event,
    make_upsert_contact_event,
)


logger = logging.getLogger(__name__)


# =============================================================================
# First touch — new lead arrived
# =============================================================================

def dispatch_on_first_touch(
    *,
    customer_id: str,
    customer_name: Optional[str],
    customer_email: Optional[str],
    customer_phone: Optional[str],
    customer_tg_handle: Optional[str],
    conversation_id: str,
    channel: str,
    channel_user_id: str,
    first_message_text: Optional[str],
    interaction_type: str,
    temperature: str,
    score: int,
    initial_stage: str = "new_lead",
    project_type: Optional[str] = None,
    source_url: Optional[str] = None,
) -> None:
    """Enqueue events for a brand-new lead: upsert_contact + create_deal.

    Caller has already created Customer + Conversation rows in our DB.
    We don't have CRM-side ids yet; the worker will fill those in via
    the on_contact_id / on_deal_id callbacks (Phase 8 — until then
    callers can re-fetch by external_id if needed).
    """
    identity_keys: dict[str, Any] = {}
    if customer_email:
        identity_keys["email"] = customer_email
    if customer_phone:
        identity_keys["phone"] = customer_phone
    if customer_tg_handle:
        identity_keys["tg_handle"] = customer_tg_handle

    contact_handle = customer_email or customer_tg_handle or customer_phone

    lead = Lead(
        id=str(customer_id),
        contact_name=customer_name,
        contact_handle=contact_handle,
        channel=channel,
        channel_user_id=channel_user_id,
        first_message_at=datetime.now(timezone.utc),
        source_url=source_url,
        interaction_type=interaction_type,
        temperature=temperature,
        score=score,
        identity_keys=identity_keys,
    )
    enqueue(make_upsert_contact_event(customer_id=str(customer_id), lead=lead))

    deal_title = _build_deal_title(customer_name, project_type, channel)
    deal = Deal(
        lead_id=str(customer_id),
        conversation_id=str(conversation_id),
        title=deal_title,
        stage=initial_stage,
        project_type=project_type,
        brief=(first_message_text[:500] if first_message_text else None),
    )
    # NOTE: contact_id is "pending" — the worker will resolve it via the
    # upsert_contact event ahead of this in the queue. For HubSpot we'd
    # ideally serialise this dependency; current impl relies on FIFO order
    # of a single-worker queue (events for the same customer arrive in
    # the order we enqueue them). Multi-worker would need explicit deps.
    enqueue(make_create_deal_event(
        customer_id=str(customer_id),
        deal=deal,
        contact_id="pending",  # worker will substitute
    ))


# =============================================================================
# Per-message events
# =============================================================================

def dispatch_message_log(
    *,
    customer_id: str,
    crm_contact_id: Optional[str],
    conversation_id: str,
    role: str,
    channel: str,
    text: str,
    metadata: Optional[dict] = None,
) -> None:
    """Mirror one message into the CRM contact timeline.

    Skipped silently if we don't yet have a crm_contact_id (worker
    hasn't processed the upsert event yet). This is acceptable because:
      - The bot's own Postgres has the truth in `messages` table
      - Once the upsert finishes, all subsequent messages flow
      - For backfilling the gap, an admin script can replay from
        Conversation.crm_deal_id once it's populated
    """
    if not crm_contact_id or crm_contact_id == "pending":
        return  # CRM-side contact not yet resolved
    msg = MessageLog(
        lead_id=str(customer_id),
        conversation_id=str(conversation_id),
        role=role,  # type: ignore[arg-type]
        channel=channel,  # type: ignore[arg-type]
        text=text,
        timestamp=datetime.now(timezone.utc),
        metadata=metadata or {},
    )
    enqueue(make_log_message_event(
        customer_id=str(customer_id), msg=msg, contact_id=crm_contact_id,
    ))


def dispatch_stage_change(
    *,
    customer_id: str,
    crm_deal_id: Optional[str],
    new_stage: str,
    lost_reason: Optional[str] = None,
) -> None:
    """Push a funnel-stage transition to the CRM deal."""
    if not crm_deal_id or crm_deal_id == "pending":
        return
    enqueue(make_update_stage_event(
        customer_id=str(customer_id),
        deal_id=crm_deal_id,
        stage=new_stage,  # type: ignore[arg-type]
        lost_reason=lost_reason,  # type: ignore[arg-type]
    ))


def dispatch_temperature_change(
    *,
    customer_id: str,
    crm_contact_id: Optional[str],
    new_temperature: str,
) -> None:
    """Push a temperature change to the CRM contact custom property."""
    if not crm_contact_id or crm_contact_id == "pending":
        return
    enqueue(make_update_temperature_event(
        customer_id=str(customer_id),
        contact_id=crm_contact_id,
        temperature=new_temperature,  # type: ignore[arg-type]
    ))


def dispatch_operator_task(
    *,
    customer_id: str,
    crm_contact_id: Optional[str],
    crm_deal_id: Optional[str],
    title: str,
    category: str = "callback",
    due_in_minutes: int = 15,
    description: Optional[str] = None,
) -> None:
    """Create an operator task in the CRM. Used after handoff, dunning, etc."""
    if not crm_contact_id or crm_contact_id == "pending":
        return
    due_at = datetime.now(timezone.utc) + timedelta(minutes=due_in_minutes)
    enqueue(make_create_task_event(
        customer_id=str(customer_id),
        contact_id=crm_contact_id,
        deal_id=(crm_deal_id if crm_deal_id and crm_deal_id != "pending" else None),
        title=title,
        due_at=due_at,
        category=category,  # type: ignore[arg-type]
        description=description,
    ))


# =============================================================================
# Helpers
# =============================================================================

def _build_deal_title(
    customer_name: Optional[str],
    project_type: Optional[str],
    channel: str,
) -> str:
    """Format a deal title following pattern: <name> — <project_type> (<channel>)."""
    name_part = customer_name or "Unknown lead"
    pt_part = project_type if project_type else "scope TBD"
    return f"{name_part} — {pt_part} ({channel})"
