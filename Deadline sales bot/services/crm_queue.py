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
        logger.warning(
            "[crm_queue] queue full (size=%d), DROPPING event type=%s customer=%s",
            queue.qsize(), event.type, event.customer_id,
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


async def _dispatch(ev: CRMEvent, adapter: CRMAdapter) -> None:
    """Translate event type → adapter call."""
    p = ev.payload
    if ev.type == "upsert_contact":
        contact_id = await adapter.upsert_contact(p["lead"])
        # Caller can read this back via the optional callback in payload
        cb = p.get("on_contact_id")
        if cb is not None:
            cb(contact_id)
        return

    if ev.type == "create_deal":
        deal_id = await adapter.create_deal(p["deal"], p["contact_id"])
        cb = p.get("on_deal_id")
        if cb is not None:
            cb(deal_id)
        return

    if ev.type == "update_deal_stage":
        await adapter.update_deal_stage(
            p["deal_id"], p["stage"], p.get("lost_reason"),
        )
        return

    if ev.type == "update_lead_temperature":
        await adapter.update_lead_temperature(p["contact_id"], p["temperature"])
        return

    if ev.type == "log_message":
        await adapter.log_message(p["msg"], p["contact_id"])
        return

    if ev.type == "create_task":
        task_id = await adapter.create_task(
            contact_id=p["contact_id"],
            deal_id=p.get("deal_id"),
            title=p["title"],
            due_at=p["due_at"],
            category=p.get("category", "callback"),
            description=p.get("description"),
        )
        cb = p.get("on_task_id")
        if cb is not None:
            cb(task_id)
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
            logger.error(
                "[crm_queue] queue full during retry, DROPPING event %s customer=%s",
                ev.type, ev.customer_id,
            )
    else:
        logger.error(
            "[crm_queue] event %s customer=%s DROPPED after %d attempts: %s",
            ev.type, ev.customer_id, ev.attempt + 1, exc,
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
) -> CRMEvent:
    return CRMEvent(
        type="update_deal_stage",
        customer_id=customer_id,
        payload={"deal_id": deal_id, "stage": stage, "lost_reason": lost_reason},
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
    on_task_id=None,
) -> CRMEvent:
    return CRMEvent(
        type="create_task",
        customer_id=customer_id,
        payload={
            "contact_id": contact_id,
            "deal_id": deal_id,
            "title": title,
            "due_at": due_at,
            "category": category,
            "description": description,
            "on_task_id": on_task_id,
        },
    )
