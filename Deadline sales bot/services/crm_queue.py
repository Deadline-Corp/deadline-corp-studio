"""CRM event queue + async worker (Phase 7, 2026-05-26).

Hot paths in main.py (webhooks/* and _handle_message) MUST stay fast and
side-effect-free w.r.t. external CRM. They put CRMEvent objects on this
queue and return immediately. A single background worker drains the
queue and dispatches calls to the configured CRMAdapter.

Why a queue:
  1. HubSpot Free has a 100 req / 10 sec rate limit. A single voice
     message can fan out into 4-5 CRM calls (upsert_contact, log_message,
     update_stage, update_temperature, create_task). Bursting these
     synchronously trips the limit.
  2. CRM downtime / 5xx / network blip should NOT take the bot offline.
     Queue + retry + drop-on-exhaust gives graceful degradation.
  3. Telegram webhook has a 10-second timeout. Synchronous CRM call in
     the webhook handler risks reproducing the 2026-05-20 OOM bug.

Design choices:
  - Single in-memory asyncio.Queue, single worker task. Simplest thing
    that works. Multi-worker can come later if throughput demands it.
  - Bounded queue (maxsize=1000) so a wedged adapter doesn't grow RAM
    unbounded. If full, we log a warning and DROP the event — the bot's
    own Postgres still has the truth.
  - Exponential backoff 1s / 3s / 10s for 3 retries, then drop with
    error log. CRM is a replica; the bot's Postgres is the source.
  - Worker is one task per process; on shutdown it drains the queue.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal, Optional

from services.crm.base import (
    CRMAdapter,
    Deal,
    Lead,
    LeadStage,
    LostReason,
    MessageLog,
    TaskCategory,
    Temperature,
)


logger = logging.getLogger(__name__)


# Event types — match CRMAdapter methods 1:1
EventType = Literal[
    "upsert_contact",
    "create_deal",
    "update_deal_stage",
    "update_lead_temperature",
    "log_message",
    "create_task",
    "schedule_followup",   # Task Engine B2 — записать отложенное действие бота
]


# Backoff schedule for transient failures, in seconds. After exhausting
# RETRY_BACKOFF the event is dropped with an error log.
RETRY_BACKOFF: tuple[float, ...] = (1.0, 3.0, 10.0)

# Bounded queue — protects against unbounded growth if the adapter is wedged.
DEFAULT_MAX_QUEUE_SIZE: int = 1000


@dataclass
class CRMEvent:
    """One unit of work for the CRM worker.

    payload shape depends on type — see _dispatch() for what each event
    expects. We keep payload as plain dict instead of typed-per-event
    dataclasses so the queue can carry mixed types without a tagged-union
    dance; the small loss of type safety is worth the simplicity.
    """
    type: EventType
    payload: dict[str, Any]
    customer_id: str                           # used for diagnostic logging + future per-customer ordering
    enqueued_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    attempt: int = 0                            # 0 = first try; incremented on each retry


# =============================================================================
# Singleton queue + worker controls
# =============================================================================

_queue: Optional[asyncio.Queue[CRMEvent]] = None
_worker_task: Optional[asyncio.Task] = None
_max_size: int = DEFAULT_MAX_QUEUE_SIZE

# Observability (Phase C reliability): count events dropped (queue full or
# retries exhausted). A dropped event = a CRM update that silently never
# happened — surfaced via /metrics so a non-zero value is an alert signal.
_dropped_events: int = 0


def _record_drop() -> None:
    global _dropped_events
    _dropped_events += 1


def get_dropped_count() -> int:
    """Total CRM events dropped (queue-full or retry-exhausted) since start."""
    return _dropped_events


def get_queue_depth() -> int:
    """Current number of pending events in the queue (0 if not yet created)."""
    return _queue.qsize() if _queue is not None else 0


def get_queue() -> asyncio.Queue[CRMEvent]:
    """Lazy singleton — created on first call inside an event loop."""
    global _queue
    if _queue is None:
        _queue = asyncio.Queue(maxsize=_max_size)
    return _queue


def is_running() -> bool:
    return _worker_task is not None and not _worker_task.done()


async def start_worker(adapter: CRMAdapter, max_size: int = DEFAULT_MAX_QUEUE_SIZE) -> None:
    """Start the single background worker. Idempotent."""
    global _worker_task, _max_size
    if is_running():
        return
    _max_size = max_size
    queue = get_queue()
    _worker_task = asyncio.create_task(_worker_loop(queue, adapter))
    logger.info("[crm_queue] worker started (adapter=%s, max_size=%d)",
                adapter.provider_name, max_size)


async def stop_worker(timeout: float = 5.0) -> None:
    """Stop the worker, draining queue best-effort within timeout."""
    global _worker_task
    if not is_running():
        return
    queue = get_queue()
    try:
        await asyncio.wait_for(queue.join(), timeout=timeout)
        logger.info("[crm_queue] worker drained successfully")
    except asyncio.TimeoutError:
        logger.warning(
            "[crm_queue] drain timeout — %d events still pending", queue.qsize(),
        )
    if _worker_task is not None:
        _worker_task.cancel()
        try:
            await _worker_task
        except asyncio.CancelledError:
            pass
    _worker_task = None


def enqueue(event: CRMEvent) -> bool:
    """Put an event on the queue. Returns False if queue is full (event dropped).

    Non-blocking — designed to be called from hot paths. If you need a
    blocking variant later, use queue.put() directly.
    """
    queue = get_queue()
    try:
        queue.put_nowait(event)
        return True
    except asyncio.QueueFull:
        _record_drop()
        logger.error(
            "[crm_queue] queue full (size=%d), DROPPING event type=%s customer=%s "
            "(total dropped=%d)",
            queue.qsize(), event.type, event.customer_id, _dropped_events,
        )
        return False


# =============================================================================
# Worker loop + dispatch
# =============================================================================

async def _worker_loop(queue: asyncio.Queue[CRMEvent], adapter: CRMAdapter) -> None:
    """Drain queue forever. One event at a time; retries inline."""
    logger.info("[crm_queue] worker loop entered")
    while True:
        ev = await queue.get()
        try:
            await _dispatch(ev, adapter)
        except asyncio.CancelledError:
            queue.task_done()
            raise
        except Exception as exc:  # noqa: BLE001 — we want to swallow EVERYTHING here
            await _handle_failure(ev, exc, queue)
        finally:
            queue.task_done()


def _resolve_pending_contact_id(customer_id: str) -> str:
    """Look up the real CRM contact_id from DB.

    Used by events enqueued with contact_id='pending' — the writeback
    from an earlier upsert_contact event should have populated
    Customer.crm_contact_id by the time the worker gets here. If not,
    raise so retry backoff kicks in (writeback may still be in flight).
    """
    from db.connection import session_scope
    from db.models import Customer
    from uuid import UUID

    with session_scope() as s:
        cust = s.query(Customer).filter(Customer.id == UUID(customer_id)).first()
        if cust and cust.crm_contact_id:
            return cust.crm_contact_id
    raise RuntimeError(
        f"contact_id still pending for customer {customer_id} — "
        f"writeback from upsert_contact not yet applied"
    )


def _resolve_pending_deal_id(conversation_id: str) -> str:
    """Look up the real CRM deal_id from DB."""
    from db.connection import session_scope
    from db.models import Conversation
    from uuid import UUID

    with session_scope() as s:
        conv = s.query(Conversation).filter(Conversation.id == UUID(conversation_id)).first()
        if conv and conv.crm_deal_id:
            return conv.crm_deal_id
    raise RuntimeError(
        f"deal_id still pending for conversation {conversation_id} — "
        f"writeback from create_deal not yet applied"
    )


async def _dispatch(ev: CRMEvent, adapter: CRMAdapter) -> None:
    """Translate event type → adapter call.

    Events that depend on a CRM-side id resolved by an earlier event in
    the FIFO (contact_id, deal_id marked as 'pending') are lazy-resolved
    from DB via _resolve_pending_*. If the prior event's writeback hasn't
    landed yet, those resolvers raise and we let retry backoff buy time.
    """
    # NOTE (2026-05-29 incident fix): every SYNC DB op below — the
    # _resolve_pending_* lookups and the writeback callbacks (cb) — opens a
    # sync session_scope() (psycopg2 pool checkout). This worker runs ON the
    # main event loop, so calling them inline froze the whole loop under load
    # (a blocking pool checkout deadlocked against coroutines holding
    # connections through slow LLM turns — every endpoint incl /health hung).
    # We push all sync DB work to a thread via asyncio.to_thread so the event
    # loop stays free. Adapter calls are already async (httpx) and stay awaited.
    p = ev.payload
    if ev.type == "upsert_contact":
        contact_id = await adapter.upsert_contact(p["lead"])
        # Caller can read this back via the optional callback in payload
        cb = p.get("on_contact_id")
        if cb is not None:
            await asyncio.to_thread(cb, contact_id)
        return

    if ev.type == "create_deal":
        contact_id = p["contact_id"]
        if contact_id == "pending":
            contact_id = await asyncio.to_thread(_resolve_pending_contact_id, ev.customer_id)
        deal_id = await adapter.create_deal(p["deal"], contact_id)
        cb = p.get("on_deal_id")
        if cb is not None:
            await asyncio.to_thread(cb, deal_id)
        return

    if ev.type == "update_deal_stage":
        deal_id = p["deal_id"]
        if deal_id == "pending":
            deal_id = await asyncio.to_thread(_resolve_pending_deal_id, p["conversation_id"])
        await adapter.update_deal_stage(
            deal_id, p["stage"], p.get("lost_reason"),
            title=p.get("title"),
            description=p.get("description"),
            project_type=p.get("project_type"),
        )
        return

    if ev.type == "update_lead_temperature":
        contact_id = p["contact_id"]
        if contact_id == "pending":
            contact_id = await asyncio.to_thread(_resolve_pending_contact_id, ev.customer_id)
        await adapter.update_lead_temperature(contact_id, p["temperature"])
        return

    if ev.type == "log_message":
        contact_id = p["contact_id"]
        if contact_id == "pending":
            contact_id = await asyncio.to_thread(_resolve_pending_contact_id, ev.customer_id)
        await adapter.log_message(p["msg"], contact_id)
        return

    if ev.type == "create_task":
        contact_id = p["contact_id"]
        if contact_id == "pending":
            contact_id = await asyncio.to_thread(_resolve_pending_contact_id, ev.customer_id)
        deal_id_raw = p.get("deal_id")
        deal_id = None
        if deal_id_raw == "pending":
            deal_id = await asyncio.to_thread(_resolve_pending_deal_id, p["conversation_id"])
        elif deal_id_raw:
            deal_id = deal_id_raw
        task_id = await adapter.create_task(
            contact_id=contact_id,
            deal_id=deal_id,
            title=p["title"],
            due_at=p["due_at"],
            category=p.get("category", "callback"),
            description=p.get("description"),
        )
        cb = p.get("on_task_id")
        if cb is not None:
            await asyncio.to_thread(cb, task_id)
        return

    if ev.type == "schedule_followup":
        # Task Engine B2 — записать строку отложенного действия бота (off event
        # loop, через to_thread). Само-отправку делает крон run_due_followups.
        from services.scheduled_actions import write_scheduled_action
        await asyncio.to_thread(
            write_scheduled_action,
            customer_id=p["customer_id"],
            conversation_id=p.get("conversation_id"),
            channel=p["channel"],
            chat_id=p.get("chat_id"),
            due_at=p["due_at"],
            text=p.get("text"),
            crm_task_id=p.get("crm_task_id"),
        )
        return

    logger.error("[crm_queue] unknown event type: %s", ev.type)


async def _handle_failure(
    ev: CRMEvent, exc: Exception, queue: asyncio.Queue[CRMEvent],
) -> None:
    """Retry with backoff up to RETRY_BACKOFF length, then drop."""
    if ev.attempt < len(RETRY_BACKOFF):
        backoff = RETRY_BACKOFF[ev.attempt]
        logger.warning(
            "[crm_queue] event %s customer=%s failed (attempt=%d): %s — retry in %.1fs",
            ev.type, ev.customer_id, ev.attempt + 1, exc, backoff,
        )
        await asyncio.sleep(backoff)
        ev.attempt += 1
        try:
            queue.put_nowait(ev)
        except asyncio.QueueFull:
            _record_drop()
            logger.error(
                "[crm_queue] queue full during retry, DROPPING event %s customer=%s "
                "(total dropped=%d)",
                ev.type, ev.customer_id, _dropped_events,
            )
    else:
        _record_drop()
        logger.error(
            "[crm_queue] event %s customer=%s DROPPED after %d attempts: %s "
            "(total dropped=%d)",
            ev.type, ev.customer_id, ev.attempt + 1, exc, _dropped_events,
        )


# =============================================================================
# Convenience helpers — typed event constructors for callers
# =============================================================================

def make_upsert_contact_event(
    customer_id: str, lead: Lead, on_contact_id=None,
) -> CRMEvent:
    return CRMEvent(
        type="upsert_contact",
        customer_id=customer_id,
        payload={"lead": lead, "on_contact_id": on_contact_id},
    )


def make_create_deal_event(
    customer_id: str, deal: Deal, contact_id: str, on_deal_id=None,
) -> CRMEvent:
    return CRMEvent(
        type="create_deal",
        customer_id=customer_id,
        payload={"deal": deal, "contact_id": contact_id, "on_deal_id": on_deal_id},
    )


def make_update_stage_event(
    customer_id: str, deal_id: str, stage: LeadStage,
    lost_reason: Optional[LostReason] = None,
    conversation_id: Optional[str] = None,
    title: Optional[str] = None,
    description: Optional[str] = None,
    project_type: Optional[str] = None,
) -> CRMEvent:
    return CRMEvent(
        type="update_deal_stage",
        customer_id=customer_id,
        payload={
            "deal_id": deal_id, "stage": stage, "lost_reason": lost_reason,
            "conversation_id": conversation_id,  # needed for lazy resolution when deal_id="pending"
            # Phase C1: optional readable card content, set at handoff/qualified
            "title": title, "description": description, "project_type": project_type,
        },
    )


def make_update_temperature_event(
    customer_id: str, contact_id: str, temperature: Temperature,
) -> CRMEvent:
    return CRMEvent(
        type="update_lead_temperature",
        customer_id=customer_id,
        payload={"contact_id": contact_id, "temperature": temperature},
    )


def make_log_message_event(
    customer_id: str, msg: MessageLog, contact_id: str,
) -> CRMEvent:
    return CRMEvent(
        type="log_message",
        customer_id=customer_id,
        payload={"msg": msg, "contact_id": contact_id},
    )


def make_create_task_event(
    customer_id: str, contact_id: str, deal_id: Optional[str],
    title: str, due_at: datetime,
    category: TaskCategory = "callback",
    description: Optional[str] = None,
    conversation_id: Optional[str] = None,
    on_task_id=None,
) -> CRMEvent:
    return CRMEvent(
        type="create_task",
        customer_id=customer_id,
        payload={
            "contact_id": contact_id,
            "deal_id": deal_id,
            "conversation_id": conversation_id,  # needed for lazy resolution when deal_id="pending"
            "title": title,
            "due_at": due_at,
            "category": category,
            "description": description,
            "on_task_id": on_task_id,
        },
    )


def make_schedule_followup_event(
    customer_id: str,
    conversation_id: Optional[str],
    channel: str,
    chat_id: Optional[str],
    due_at: datetime,
    text: Optional[str] = None,
    crm_task_id: Optional[str] = None,
) -> CRMEvent:
    """Task Engine B2 — событие записи отложенного действия бота в scheduled_actions."""
    return CRMEvent(
        type="schedule_followup",
        customer_id=customer_id,
        payload={
            "customer_id": customer_id,
            "conversation_id": conversation_id,
            "channel": channel,
            "chat_id": chat_id,
            "due_at": due_at,
            "text": text,
            "crm_task_id": crm_task_id,
        },
    )
