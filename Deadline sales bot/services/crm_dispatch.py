"""High-level CRM event dispatcher (Phase 7+8, 2026-05-26).

Bridges the bot's hot path to the CRM event queue. Each function is a
small composition: read Customer + Conversation state, decide what CRM
events the current turn implies, enqueue them. Returns immediately —
all CRM I/O happens later in the worker.

dispatch_on_message_turn() is the ONE function the hot path calls per
message turn — it handles all the branching internally (new lead vs
returning, log message, handoff transition, etc).

Writeback pattern:
  Worker resolves the real CRM-side ids asynchronously. We pass a
  callback that opens a fresh DB session (via session_scope) and writes
  the id back to Customer.crm_contact_id / Conversation.crm_deal_id.
  Subsequent log_message events for the same conversation will see the
  populated id and flow through; events fired during the gap drop
  silently (acceptable — our Postgres has the truth in messages table).
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


# =============================================================================
# Hot-path entry point — called once per message turn from _handle_message
# =============================================================================

def dispatch_on_message_turn(
    *,
    customer: Any,          # db.models.Customer — has crm_contact_id field
    conversation: Any,      # db.models.Conversation — has crm_deal_id, lead_stage
    last_lead_message: Optional[str],
    last_bot_reply: Optional[str],
    handoff_just_fired: bool,
    channel: str,
    project_type: Optional[str] = None,
) -> None:
    """Process one message turn — enqueue all CRM events implied.

    This is THE function the bot's hot path calls. Everything else is
    internal composition. Never raises — exceptions are logged and
    swallowed so a CRM hiccup never breaks the bot.

    Branching (determined from Customer/Conversation state):
      1. customer.crm_contact_id is None  → enqueue upsert_contact + create_deal
         (with DB-writeback callbacks for the real CRM ids)
      2. Else, log_message events for lead message + bot reply
      3. handoff_just_fired → update_deal_stage(qualified) + operator task

    Args use Customer + Conversation directly (not unpacked) because the
    hot path already has them in hand and unpacking 10 fields here would
    be noise. We only read attributes, never write to them — writebacks
    happen in worker callbacks via a fresh session_scope.
    """
    try:
        customer_id = str(customer.id)
        contact_id = customer.crm_contact_id
        deal_id = conversation.crm_deal_id

        # "New for CRM" = we haven't synced this customer yet. This is the
        # right cue rather than is_first_turn, because the same lead can
        # return on a different channel and we still want one CRM contact.
        is_new_for_crm = not contact_id

        if is_new_for_crm:
            _enqueue_new_lead(
                customer=customer,
                conversation=conversation,
                first_message_text=last_lead_message,
                channel=channel,
                project_type=project_type,
            )
            # Don't return early — we still want to log this first message,
            # but it'll skip because contact_id is None until worker resolves.

        # Log lead message
        if last_lead_message:
            dispatch_message_log(
                customer_id=customer_id,
                crm_contact_id=contact_id,
                conversation_id=str(conversation.id),
                role="lead",
                channel=channel,
                text=last_lead_message,
            )

        # Log bot reply
        if last_bot_reply:
            dispatch_message_log(
                customer_id=customer_id,
                crm_contact_id=contact_id,
                conversation_id=str(conversation.id),
                role="bot",
                channel=channel,
                text=last_bot_reply,
            )

        # Handoff just fired → move deal to qualified + create operator task
        if handoff_just_fired:
            dispatch_stage_change(
                customer_id=customer_id,
                crm_deal_id=deal_id,
                new_stage="qualified",
            )
            dispatch_operator_task(
                customer_id=customer_id,
                crm_contact_id=contact_id,
                crm_deal_id=deal_id,
                title=f"Take over lead — {customer.name or customer.email or 'unknown'}",
                category="qualification",
                due_in_minutes=15,
                description=(last_lead_message or "")[:500],
            )

    except Exception as exc:  # noqa: BLE001
        logger.warning("[crm_dispatch] dispatch_on_message_turn failed: %s", exc)


def _enqueue_new_lead(
    *,
    customer: Any,
    conversation: Any,
    first_message_text: Optional[str],
    channel: str,
    project_type: Optional[str],
) -> None:
    """Enqueue upsert_contact + create_deal with DB writeback callbacks.

    Worker will populate the real CRM-side ids by calling the callbacks,
    which open a fresh session_scope and patch Customer + Conversation rows.
    """
    customer_id = str(customer.id)
    conversation_id = str(conversation.id)

    # Build identity_keys from customer fields
    identity_keys: dict[str, Any] = {}
    if customer.email:
        identity_keys["email"] = customer.email
    if customer.phone:
        identity_keys["phone"] = customer.phone

    # Get tg_handle from channel_identities for the telegram channel
    tg_handle = None
    for ident in (customer.identities or []):
        if ident.channel == "telegram" and ident.username:
            tg_handle = ident.username
            identity_keys["tg_handle"] = ident.username
            break

    contact_handle = customer.email or tg_handle or customer.phone

    # Find external_id for this channel (used as channel_user_id)
    channel_user_id = ""
    for ident in (customer.identities or []):
        if ident.channel == channel:
            channel_user_id = ident.external_id
            break

    lead = Lead(
        id=customer_id,
        contact_name=customer.name,
        contact_handle=contact_handle,
        channel=channel,  # type: ignore[arg-type]
        channel_user_id=channel_user_id,
        first_message_at=datetime.now(timezone.utc),
        source_url=None,
        interaction_type=getattr(customer, "interaction_type", "P2") or "P2",
        temperature=getattr(customer, "lead_temperature", "cold") or "cold",
        score=getattr(customer, "lead_score", 0) or 0,
        identity_keys=identity_keys,
    )

    from services.crm_queue import enqueue, make_upsert_contact_event, make_create_deal_event

    enqueue(make_upsert_contact_event(
        customer_id=customer_id,
        lead=lead,
        on_contact_id=_make_contact_id_writeback(customer_id),
    ))

    deal = Deal(
        lead_id=customer_id,
        conversation_id=conversation_id,
        title=_build_deal_title(customer.name, project_type, channel),
        stage=getattr(conversation, "lead_stage", "new_lead") or "new_lead",
        project_type=project_type,
        brief=(first_message_text[:500] if first_message_text else None),
    )
    # NOTE: contact_id="pending" — worker substitutes the real id from
    # the upsert_contact event ahead of this in the FIFO queue.
    enqueue(make_create_deal_event(
        customer_id=customer_id,
        deal=deal,
        contact_id="pending",
        on_deal_id=_make_deal_id_writeback(conversation_id),
    ))


# =============================================================================
# DB writeback callbacks — worker calls these once it has the real CRM ids
# =============================================================================

def _make_contact_id_writeback(customer_id: str):
    """Factory: returns a callback that writes contact_id to Customer.crm_contact_id."""
    def writeback(contact_id: str) -> None:
        try:
            from db.connection import session_scope
            from db.models import Customer
            from uuid import UUID
            with session_scope() as s:
                cust = s.query(Customer).filter(Customer.id == UUID(customer_id)).first()
                if cust and not cust.crm_contact_id:
                    cust.crm_contact_id = contact_id
                    logger.info(
                        "[crm_dispatch] customer %s crm_contact_id <- %s",
                        customer_id, contact_id,
                    )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[crm_dispatch] contact_id writeback failed for %s: %s",
                customer_id, exc,
            )
    return writeback


def _make_deal_id_writeback(conversation_id: str):
    """Factory: returns a callback that writes deal_id to Conversation.crm_deal_id."""
    def writeback(deal_id: str) -> None:
        try:
            from db.connection import session_scope
            from db.models import Conversation
            from uuid import UUID
            with session_scope() as s:
                conv = s.query(Conversation).filter(Conversation.id == UUID(conversation_id)).first()
                if conv and not conv.crm_deal_id:
                    conv.crm_deal_id = deal_id
                    logger.info(
                        "[crm_dispatch] conversation %s crm_deal_id <- %s",
                        conversation_id, deal_id,
                    )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[crm_dispatch] deal_id writeback failed for %s: %s",
                conversation_id, exc,
            )
    return writeback
