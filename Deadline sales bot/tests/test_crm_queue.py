"""Tests for services/crm_queue.py — async worker + retry logic."""

import asyncio
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock

import pytest

from services.crm import NoOpAdapter
from services.crm.base import Lead, MessageLog
from services.crm_queue import (
    CRMEvent,
    RETRY_BACKOFF,
    enqueue,
    get_queue,
    is_running,
    make_log_message_event,
    make_upsert_contact_event,
    start_worker,
    stop_worker,
)
import services.crm_queue as cq


def _make_lead(customer_id: str = "test-1") -> Lead:
    return Lead(
        id=customer_id,
        contact_name="Test Lead",
        contact_handle="test@example.com",
        channel="telegram",
        channel_user_id="tg-123",
        first_message_at=datetime.now(timezone.utc),
        source_url=None,
        identity_keys={"email": "test@example.com"},
    )


@pytest.fixture(autouse=True)
def reset_queue():
    """Each test starts with a fresh queue + no worker."""
    cq._queue = None
    cq._worker_task = None
    yield
    # Tear down any lingering worker
    if cq._worker_task is not None and not cq._worker_task.done():
        cq._worker_task.cancel()


class TestCRMEvent:
    def test_construction(self):
        ev = CRMEvent(type="log_message", payload={}, customer_id="c-1")
        assert ev.type == "log_message"
        assert ev.attempt == 0
        assert ev.enqueued_at is not None


class TestEnqueueDequeue:
    @pytest.mark.asyncio
    async def test_enqueue_returns_true_when_room(self):
        ev = make_upsert_contact_event("c-1", _make_lead("c-1"))
        assert enqueue(ev) is True

    @pytest.mark.asyncio
    async def test_enqueue_returns_false_when_full(self):
        # Force tiny queue
        cq._queue = asyncio.Queue(maxsize=1)
        ok1 = enqueue(make_upsert_contact_event("c-1", _make_lead("c-1")))
        ok2 = enqueue(make_upsert_contact_event("c-2", _make_lead("c-2")))
        assert ok1 is True
        assert ok2 is False  # dropped — log warning issued


class TestWorkerLifecycle:
    @pytest.mark.asyncio
    async def test_start_then_stop(self):
        adapter = NoOpAdapter()
        await start_worker(adapter)
        assert is_running()
        await stop_worker(timeout=2.0)
        assert not is_running()

    @pytest.mark.asyncio
    async def test_start_is_idempotent(self):
        adapter = NoOpAdapter()
        await start_worker(adapter)
        await start_worker(adapter)  # second call no-op
        assert is_running()
        await stop_worker(timeout=2.0)


class TestDispatchEndToEnd:
    @pytest.mark.asyncio
    async def test_noop_adapter_drains_queue(self):
        adapter = NoOpAdapter()
        await start_worker(adapter)
        try:
            for i in range(5):
                lead = _make_lead(f"c-{i}")
                enqueue(make_upsert_contact_event(f"c-{i}", lead))

            # Wait for worker to drain
            await asyncio.wait_for(get_queue().join(), timeout=2.0)
        finally:
            await stop_worker(timeout=2.0)


class TestRetryOnFailure:
    @pytest.mark.asyncio
    async def test_failure_retries_then_drops(self, monkeypatch):
        """Adapter that always raises — event should retry then drop."""
        # Speed up retries for the test
        monkeypatch.setattr(cq, "RETRY_BACKOFF", (0.01, 0.01, 0.01))

        adapter = NoOpAdapter()
        adapter.upsert_contact = AsyncMock(side_effect=RuntimeError("simulated failure"))

        await start_worker(adapter)
        try:
            enqueue(make_upsert_contact_event("c-fail", _make_lead("c-fail")))
            # Wait long enough for 3 retries to fire and drop
            await asyncio.wait_for(get_queue().join(), timeout=2.0)

            # 1 initial + 3 retries = 4 calls before drop
            assert adapter.upsert_contact.call_count == 4
        finally:
            await stop_worker(timeout=2.0)


class TestEventConstructors:
    def test_upsert_contact_payload(self):
        lead = _make_lead("c-1")
        ev = make_upsert_contact_event("c-1", lead)
        assert ev.type == "upsert_contact"
        assert ev.payload["lead"] is lead

    def test_log_message_payload(self):
        msg = MessageLog(
            lead_id="c-1",
            conversation_id="conv-1",
            role="lead",
            channel="telegram",
            text="hello",
            timestamp=datetime.now(timezone.utc),
        )
        ev = make_log_message_event("c-1", msg, "contact-hubspot-123")
        assert ev.type == "log_message"
        assert ev.payload["msg"] is msg
        assert ev.payload["contact_id"] == "contact-hubspot-123"
