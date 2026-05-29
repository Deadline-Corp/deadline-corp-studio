"""Regression test for the 2026-05-29 prod incident: CRM worker froze the
event loop.

Root cause: services/crm_queue._worker_loop runs on the MAIN event loop, and
_dispatch() called SYNC blocking DB ops directly on the loop
(_resolve_pending_contact_id / _deal_id and the writeback callbacks, all via a
sync session_scope() -> psycopg2 pool checkout). Under concurrent load that
blocking call froze the whole loop -> every endpoint (incl /health) hung.

This test reproduces the mechanism WITHOUT a real DB: it patches the worker's
sync resolver with a function that blocks the thread (time.sleep). A separate
"health ticker" coroutine measures the largest gap between its ticks while the
worker processes one event. On the buggy code the gap ~= the block duration
(loop frozen). After the fix (sync DB pushed off the loop via asyncio.to_thread),
the loop stays responsive and the gap stays small.

Run:
    venv/Scripts/python.exe -m pytest tests/test_crm_worker_nonblocking.py -v
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from services import crm_queue  # noqa: E402
from services.crm.base import Deal  # noqa: E402


# The synthetic blocking duration that stands in for a wedged sync pool
# checkout / slow sync query on the event loop.
_BLOCK_SECONDS = 1.0
# Max acceptable event-loop stall. A responsive loop ticks every ~50ms; we
# allow generous slack for CI but far below _BLOCK_SECONDS.
_MAX_ACCEPTABLE_GAP = 0.4


class _MockAdapter:
    """Async, instant CRM adapter — isolates the test from HubSpot/network."""
    provider_name = "mock"

    async def upsert_contact(self, lead):  # noqa: ANN001
        return "contact-mock"

    async def create_deal(self, deal, contact_id):  # noqa: ANN001
        return "deal-mock"

    async def update_deal_stage(self, *a, **k):
        return None

    async def update_lead_temperature(self, *a, **k):
        return None

    async def log_message(self, *a, **k):
        return None

    async def create_task(self, *a, **k):
        return "task-mock"


@pytest.fixture(autouse=True)
def _reset_queue_singletons():
    """Each test gets a fresh queue + worker (module-level singletons)."""
    crm_queue._queue = None
    crm_queue._worker_task = None
    yield
    crm_queue._queue = None
    crm_queue._worker_task = None


@pytest.mark.asyncio
async def test_crm_worker_does_not_freeze_event_loop(monkeypatch):
    """A create_deal event with contact_id='pending' triggers the worker's
    sync resolver. That resolver must NOT block the event loop. We simulate a
    slow/blocked sync DB checkout and assert the loop stays responsive."""

    def _blocking_resolve(customer_id):
        # Stands in for `with session_scope() as s: s.query(...)` hitting an
        # exhausted/slow sync pool — a blocking call on whatever thread runs it.
        time.sleep(_BLOCK_SECONDS)
        return "contact-resolved"

    monkeypatch.setattr(crm_queue, "_resolve_pending_contact_id", _blocking_resolve)

    await crm_queue.start_worker(_MockAdapter())

    gaps: list[float] = []

    async def health_ticker():
        last = time.monotonic()
        for _ in range(30):
            await asyncio.sleep(0.05)
            now = time.monotonic()
            gaps.append(now - last)
            last = now

    ticker = asyncio.create_task(health_ticker())
    # Let the ticker establish a baseline, then fire the event that makes the
    # worker run the (blocking) resolver.
    await asyncio.sleep(0.1)
    crm_queue.enqueue(
        crm_queue.make_create_deal_event(
            customer_id="cust-1",
            deal=Deal(
                lead_id="cust-1",
                conversation_id="conv-1",
                title="probe",
                stage="new_lead",
            ),
            contact_id="pending",  # forces _resolve_pending_contact_id
        )
    )
    await ticker
    await crm_queue.stop_worker(timeout=2.0)

    max_gap = max(gaps)
    assert max_gap < _MAX_ACCEPTABLE_GAP, (
        f"event loop stalled for {max_gap:.2f}s while the CRM worker ran a "
        f"sync DB op — the worker is blocking the loop (regression of the "
        f"2026-05-29 hang)."
    )
