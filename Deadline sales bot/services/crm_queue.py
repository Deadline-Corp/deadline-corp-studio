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
    "complete_task",       # закрыть задачу в CRM, когда бот её исполнил (followup отправлен)
    "update_task",         # дополнить задачу (напр. канал созвона, названный позже)
    "schedule_followup",   # Task Engine B2 — записать отложенное действие бота
    "merge_contacts",      # слить две CRM-карточки в одну (Postgres-кастомеры склеились)
    "sync_call_medium",    # дозаписать канал в задачи созвона (найти их по сделке)
]


# Backoff schedule for transient failures, in seconds. After exhausting
# RETRY_BACKOFF the event is dropped with an error log.
RETRY_BACKOFF: tuple[float, ...] = (1.0, 3.0, 10.0)

# Bounded queue — protects against unbounded growth if the adapter is wedged.
DEFAULT_MAX_QUEUE_SIZE: int = 1000

# =============================================================================
# Durable persistence (recovery после рестарта)
# =============================================================================
# Очередь in-memory → события в ней теряются при рестарте. Воркер пишет строку
# crm_events (pending) когда берёт событие, помечает done/failed. На старте
# recover_pending_events() переигрывает оставшиеся pending. payload сериализуем
# JSON-safe: closures-колбэки (on_*) выбрасываем (восстановим при recovery по
# customer_id/conversation_id), dataclasses (Lead/Deal/MessageLog) и datetime
# кодируем тегами.

import dataclasses as _dc

_DC_REGISTRY: dict = {"Lead": Lead, "Deal": Deal, "MessageLog": MessageLog}


def _encode(v):
    if _dc.is_dataclass(v) and not isinstance(v, type):
        return {"__dc__": type(v).__name__, "f": _encode(_dc.asdict(v))}
    if isinstance(v, datetime):
        return {"__dt__": v.isoformat()}
    if isinstance(v, dict):
        return {k: _encode(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_encode(x) for x in v]
    return v


def _decode(v):
    if isinstance(v, dict):
        if "__dt__" in v and len(v) == 1:
            return datetime.fromisoformat(v["__dt__"])
        if "__dc__" in v:
            cls = _DC_REGISTRY.get(v["__dc__"])
            fields = _decode(v.get("f") or {})
            return cls(**fields) if cls else fields
        return {k: _decode(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_decode(x) for x in v]
    return v


def _serialize_payload(payload: dict) -> dict:
    """JSON-safe снимок payload: без callable-колбэков, dataclasses/datetime закодированы."""
    return {k: _encode(val) for k, val in payload.items() if not callable(val)}


def _uuid_or_none(s):
    from uuid import UUID
    try:
        return UUID(str(s)) if s else None
    except Exception:  # noqa: BLE001
        return None


def _persist_pending(ev: "CRMEvent") -> Optional[str]:
    """Записать crm_events (pending). Возвращает id строки. Sync — звать через to_thread."""
    from db.connection import session_scope
    from db.models import CRMEvent as CRMEventRow
    try:
        payload_json = _serialize_payload(ev.payload)
        with session_scope() as s:
            row = CRMEventRow(
                event_type=ev.type,
                customer_id=_uuid_or_none(ev.customer_id),
                conversation_id=_uuid_or_none(ev.payload.get("conversation_id")),
                payload=payload_json,
                status="pending",
                attempts=ev.attempt,
            )
            s.add(row)
            s.flush()
            return str(row.id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[crm_queue] persist pending failed type=%s: %s", ev.type, exc)
        return None


def _mark_event(row_id: Optional[str], status: str, error: Optional[str] = None) -> None:
    """Пометить crm_events строку done/failed. Sync — звать через to_thread."""
    if not row_id:
        return
    from db.connection import session_scope
    from db.models import CRMEvent as CRMEventRow
    from uuid import UUID
    try:
        with session_scope() as s:
            row = s.get(CRMEventRow, UUID(row_id))
            if row is not None:
                row.status = status
                row.processed_at = datetime.now(timezone.utc)
                if error:
                    row.last_error = str(error)[:500]
    except Exception as exc:  # noqa: BLE001
        logger.warning("[crm_queue] mark %s failed row=%s: %s", status, row_id, exc)


def _load_pending_rows(limit: int = 500) -> list[dict]:
    """Прочитать pending crm_events (для recovery). Sync — звать через to_thread."""
    from db.connection import session_scope
    from db.models import CRMEvent as CRMEventRow
    out: list[dict] = []
    try:
        with session_scope() as s:
            rows = (
                s.query(CRMEventRow)
                .filter(CRMEventRow.status == "pending")
                .order_by(CRMEventRow.created_at.asc())
                .limit(limit)
                .all()
            )
            for r in rows:
                out.append({
                    "id": str(r.id),
                    "event_type": r.event_type,
                    "customer_id": str(r.customer_id) if r.customer_id else None,
                    "conversation_id": str(r.conversation_id) if r.conversation_id else None,
                    "payload": r.payload or {},
                    "attempts": r.attempts or 0,
                })
    except Exception as exc:  # noqa: BLE001
        logger.warning("[crm_queue] load pending failed: %s", exc)
    return out


def _reconstruct_event(r: dict) -> Optional["CRMEvent"]:
    """Восстановить CRMEvent из crm_events строки + перевесить writeback-колбэки."""
    try:
        payload = _decode(r.get("payload") or {})
        et = r["event_type"]
        # Перевешиваем критичные writeback-колбэки (contact_id/deal_id) по id —
        # они нужны для pending-резолва и идемпотентности. task_id-writeback
        # пропускаем (некритично: зеркальная задача просто не авто-закроется).
        from services import crm_dispatch as _cd
        if et == "upsert_contact" and r.get("customer_id"):
            payload["on_contact_id"] = _cd._make_contact_id_writeback(r["customer_id"])
        elif et == "create_deal" and r.get("conversation_id"):
            payload["on_deal_id"] = _cd._make_deal_id_writeback(r["conversation_id"])
        ev = CRMEvent(
            type=et, payload=payload,
            customer_id=r.get("customer_id") or "",
            attempt=r.get("attempts") or 0,
            row_id=r["id"],
        )
        return ev
    except Exception as exc:  # noqa: BLE001
        logger.warning("[crm_queue] reconstruct failed row=%s: %s", r.get("id"), exc)
        return None


async def recover_pending_events() -> int:
    """Старт: переиграть pending crm_events, оставшиеся от прошлого процесса
    (потеряны из in-memory очереди при рестарте). Дубль-обработка безопасна:
    create_deal идемпотентен, update_stage идемпотентен, log/task — мягкие дубли."""
    rows = await asyncio.to_thread(_load_pending_rows)
    n = 0
    for r in rows:
        ev = _reconstruct_event(r)
        if ev is not None and enqueue(ev):
            n += 1
    if rows:
        logger.info("[crm_queue] recovery: re-enqueued %d/%d pending CRM events", n, len(rows))
    return n


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
    row_id: Optional[str] = None               # id durable-строки crm_events (для mark done/failed)


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
    # Recovery: переиграть pending CRM-события прошлого процесса (потеряны из
    # in-memory очереди при рестарте). Fire-and-forget — не тормозим старт.
    try:
        asyncio.create_task(recover_pending_events())
    except Exception as exc:  # noqa: BLE001
        logger.warning("[crm_queue] could not schedule recovery: %s", exc)


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
            # Durable: фиксируем событие (pending) ДО отправки в CRM, чтобы
            # пережить рестарт. Уже восстановленные (row_id есть) не дублируем.
            if ev.row_id is None:
                ev.row_id = await asyncio.to_thread(_persist_pending, ev)
            await _dispatch(ev, adapter)
            await asyncio.to_thread(_mark_event, ev.row_id, "done")
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


def _existing_deal_id(conversation_id: Optional[str]) -> Optional[str]:
    """Вернуть уже созданный deal_id диалога (или None). НЕ бросает — для
    идемпотентности create_deal: если предыдущий create_deal уже записал
    deal_id, второй (гонка двух turn'ов) НЕ создаёт дубль сделки."""
    if not conversation_id:
        return None
    from db.connection import session_scope
    from db.models import Conversation
    from uuid import UUID
    try:
        with session_scope() as s:
            conv = s.query(Conversation).filter(Conversation.id == UUID(conversation_id)).first()
            return conv.crm_deal_id if conv else None
    except Exception as exc:  # noqa: BLE001
        logger.warning("[crm] _existing_deal_id failed for %s: %s", conversation_id, exc)
        return None


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
        contact_id = await adapter.upsert_contact(p["lead"], known_id=p.get("known_id"))
        # Caller can read this back via the optional callback in payload
        cb = p.get("on_contact_id")
        if cb is not None:
            await asyncio.to_thread(cb, contact_id)
        return

    if ev.type == "create_deal":
        # Идемпотентность: если у диалога УЖЕ есть deal_id (предыдущий create_deal
        # успел записать) — не создаём дубль сделки (гонка двух сообщений подряд).
        _conv_id = p.get("conversation_id")
        _already = await asyncio.to_thread(_existing_deal_id, _conv_id)
        if _already:
            logger.info("[crm] create_deal skip — conv %s already has deal %s", _conv_id, _already)
            cb = p.get("on_deal_id")
            if cb is not None:
                await asyncio.to_thread(cb, _already)
            return
        contact_id = p["contact_id"]
        if contact_id == "pending":
            contact_id = await asyncio.to_thread(_resolve_pending_contact_id, ev.customer_id)
        # ДЕДУП #2: у контакта уже есть ОТКРЫТАЯ сделка (другой диалог / другой канал
        # с тем же контактом)? → переиспользуем её, не плодим «2 карточки на одного».
        # Один клиент = одна активная сделка; новую заводим только если все закрыты.
        try:
            _reuse = await adapter.find_open_deal_for_contact(contact_id)
        except Exception as _re:  # noqa: BLE001
            logger.warning("[crm] find_open_deal_for_contact failed: %s", _re)
            _reuse = None
        if _reuse:
            logger.info("[crm] create_deal REUSE open deal %s for contact %s (no dup card)", _reuse, contact_id)
            cb = p.get("on_deal_id")
            if cb is not None:
                await asyncio.to_thread(cb, _reuse)
            return
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
            next_meeting_at=p.get("next_meeting_at"),
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

    if ev.type == "sync_call_medium":
        # Канал назван после брони → найти задачи созвона ПО СДЕЛКЕ в HubSpot и
        # дозаписать канал в тему/тело. Не зависит от гоночного profile_data.
        _deal = p.get("deal_id")
        if _deal == "pending":
            try:
                _deal = await asyncio.to_thread(_resolve_pending_deal_id, p["conversation_id"])
            except Exception:  # noqa: BLE001
                return  # сделки ещё нет — ретрай по backoff
        if not _deal:
            return
        tasks = await adapter.list_tasks_for_deal(_deal)
        if not tasks:
            return
        from services.crm_dispatch import _call_task_subject, _fmt_call_time
        _when = _fmt_call_time(p["call_at"])
        for t in tasks:
            subj = t.get("subject") or ""
            kind = "day" if subj.startswith("📞 Подтвердить созвон") else (
                "hour" if subj.startswith("⏰ Через час созвон") else None)
            if kind:
                _ns, _nb = _call_task_subject(kind, p["lead_name"], _when, p["medium"])
                await adapter.update_task(str(t["id"]), subject=_ns, body=_nb)
        return

    if ev.type == "merge_contacts":
        # Два наших Postgres-кастомера склеились в один (общий email / deep-link),
        # а в CRM остались ДВЕ карточки на человека. Сливаем secondary в primary.
        prim = p.get("primary_id"); sec = p.get("secondary_id")
        if prim and sec and str(prim) != str(sec):
            await adapter.merge_contacts(str(prim), str(sec))
        return

    if ev.type == "complete_task":
        # Бот исполнил отложенное действие (отправил followup лиду) → закрываем
        # зеркальную задачу в CRM, чтобы оператор не видел «вечно открытую».
        tid = p.get("task_id")
        if tid:
            await adapter.complete_task(str(tid))
        return

    if ev.type == "update_task":
        # Дополнить существующую задачу (напр. канал созвона, названный лидом
        # СЛЕДУЮЩИМ сообщением после брони — раньше канал не попадал в задачу).
        tid = p.get("task_id")
        if tid:
            await adapter.update_task(str(tid), subject=p.get("subject"), body=p.get("body"))
        return

    if ev.type == "schedule_followup":
        # Task Engine B2 — записать строку отложенного действия бота (off event
        # loop, через to_thread). Само-отправку делает крон run_due_followups.
        from services.scheduled_actions import write_scheduled_action
        rid, was_new = await asyncio.to_thread(
            write_scheduled_action,
            customer_id=p["customer_id"],
            conversation_id=p.get("conversation_id"),
            channel=p["channel"],
            chat_id=p.get("chat_id"),
            due_at=p["due_at"],
            text=p.get("text"),
            crm_task_id=p.get("crm_task_id"),
        )
        # Взаимосвязь с CRM: только для НОВОГО followup (не повтора) создаём
        # зеркальную CRM-задачу «написать лиду» и привязываем её к этой строке
        # (on_task_id → крон закроет её при исполнении). Повтор просьбы task_id
        # унаследовал в write_scheduled_action — новую задачу не плодим (дедуп).
        conv_id = p.get("conversation_id")
        if rid and was_new and p.get("task_title") and conv_id:
            from services.crm_dispatch import _make_followup_task_writeback
            enqueue(make_create_task_event(
                customer_id=p["customer_id"],
                contact_id=p.get("task_contact_id") or "pending",
                deal_id=p.get("task_deal_id"),
                conversation_id=conv_id,
                title=p["task_title"],
                due_at=p["due_at"],
                category="callback",
                description=p.get("task_description"),
                on_task_id=_make_followup_task_writeback(conv_id),
            ))
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
        # Durable: помечаем строку failed (для разбора/реконсиляции, не теряем след).
        await asyncio.to_thread(_mark_event, ev.row_id, "failed", str(exc))


# =============================================================================
# Convenience helpers — typed event constructors for callers
# =============================================================================

def make_upsert_contact_event(
    customer_id: str, lead: Lead, on_contact_id=None, known_id=None,
) -> CRMEvent:
    return CRMEvent(
        type="upsert_contact",
        customer_id=customer_id,
        payload={"lead": lead, "on_contact_id": on_contact_id, "known_id": known_id},
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
    next_meeting_at=None,
) -> CRMEvent:
    return CRMEvent(
        type="update_deal_stage",
        customer_id=customer_id,
        payload={
            "deal_id": deal_id, "stage": stage, "lost_reason": lost_reason,
            "conversation_id": conversation_id,  # needed for lazy resolution when deal_id="pending"
            # Phase C1: optional readable card content, set at handoff/qualified
            "title": title, "description": description, "project_type": project_type,
            # Созвон: дата/время назначенного созвона (datetime) → в карточку сделки.
            "next_meeting_at": next_meeting_at,
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


def make_complete_task_event(customer_id: str, task_id: str) -> CRMEvent:
    """Закрыть задачу в CRM (бот исполнил followup). payload — простая строка,
    переживает persist/recovery (колбэков нет)."""
    return CRMEvent(
        type="complete_task",
        customer_id=customer_id,
        payload={"task_id": task_id},
    )


def make_sync_call_medium_event(
    customer_id: str, conversation_id: str, deal_id: str,
    lead_name: str, call_at: datetime, medium: str,
) -> CRMEvent:
    """Дозаписать канал в задачи созвона (воркер найдёт их по сделке). datetime
    в payload кодируется при persist/recovery (см. _encode)."""
    return CRMEvent(
        type="sync_call_medium",
        customer_id=customer_id,
        payload={
            "conversation_id": conversation_id,
            "deal_id": deal_id,
            "lead_name": lead_name,
            "call_at": call_at,
            "medium": medium,
        },
    )


def make_merge_contacts_event(
    customer_id: str, primary_id: str, secondary_id: str,
) -> CRMEvent:
    """Слить secondary CRM-контакт в primary. payload — простые строки,
    переживает persist/recovery (колбэков нет)."""
    return CRMEvent(
        type="merge_contacts",
        customer_id=customer_id,
        payload={"primary_id": primary_id, "secondary_id": secondary_id},
    )


def make_update_task_event(
    customer_id: str, task_id: str, subject: Optional[str] = None, body: Optional[str] = None,
) -> CRMEvent:
    """Дополнить задачу (subject/body) — напр. добавить канал созвона."""
    return CRMEvent(
        type="update_task",
        customer_id=customer_id,
        payload={"task_id": task_id, "subject": subject, "body": body},
    )


def make_schedule_followup_event(
    customer_id: str,
    conversation_id: Optional[str],
    channel: str,
    chat_id: Optional[str],
    due_at: datetime,
    text: Optional[str] = None,
    crm_task_id: Optional[str] = None,
    task_title: Optional[str] = None,
    task_description: Optional[str] = None,
    task_contact_id: Optional[str] = None,
    task_deal_id: Optional[str] = None,
) -> CRMEvent:
    """Task Engine B2 — событие записи отложенного действия бота в scheduled_actions.

    task_* (опц.): если переданы, воркер создаст зеркальную CRM-задачу «написать
    лиду» и привяжет её к followup-строке — НО только когда followup НОВЫЙ (не
    повтор). При повторе (лид снова просит «позже») write_scheduled_action
    переносит task_id со старой строки и новую задачу не плодим (дедуп)."""
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
            "task_title": task_title,
            "task_description": task_description,
            "task_contact_id": task_contact_id,
            "task_deal_id": task_deal_id,
        },
    )
