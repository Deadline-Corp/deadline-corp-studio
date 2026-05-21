"""Unit tests for webhook update-id dedup mechanism.

Why this exists:
  Telegram webhook has a 10-second handler timeout. For large voice messages
  (Whisper ~5s + RAG ~100ms + LLM 2-5s + handoff classifier 2-3s) total
  exceeds 10s → Telegram retries the SAME update_id → second handler instance
  starts processing the same payload concurrently → OOM on the second
  bge-m3 / Whisper load. Documented incident: 2026-05-20 08:34 UTC.

Fix: BackgroundTasks pattern (return 200 OK in <100ms) + in-memory dedup
on update_id so even if a retry slips through the fast-200, the second
handler short-circuits without re-processing.

Tests cover the dedup primitive `_seen_update(update_id)`:

  1. First call with a fresh id returns False (NOT a duplicate, mark it)
  2. Second call with the SAME id returns True (duplicate detected)
  3. Two different ids each get one False
  4. update_id=None returns False without storing (Telegram sometimes omits)
  5. TTL eviction: after _DEDUP_MAX_AGE_SEC passes, the id is forgotten
  6. Size cap eviction: after _DEDUP_MAX_SIZE entries, oldest are FIFO-evicted

Run from project root:
    venv/Scripts/python.exe -m pytest tests/test_dedup.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make `from main import ...` work when pytest is invoked from project root.
sys.path.insert(0, str(Path(__file__).parent.parent))

# We import the symbols lazily inside each test (after monkeypatching time
# if needed) so module-level state doesn't bleed across tests. We also
# reset the global dedup dict at the start of each test for isolation.
import main  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_dedup_state():
    """Clear the global dedup OrderedDict before every test so cases don't
    interfere with each other. Without this, a test that inserted id=42 would
    poison every later test that checks id=42."""
    main._PROCESSED_UPDATES.clear()
    yield
    main._PROCESSED_UPDATES.clear()


# ============================================================================
# Basic happy path
# ============================================================================


def test_first_call_returns_false():
    """A fresh update_id is NOT considered a duplicate on first sight."""
    assert main._seen_update(1001) is False


def test_second_call_returns_true():
    """Same update_id seen twice → second call returns True (duplicate)."""
    assert main._seen_update(1002) is False
    assert main._seen_update(1002) is True


def test_two_different_ids_both_false():
    """Different ids each get their own first-call=False without confusing each other."""
    assert main._seen_update(2001) is False
    assert main._seen_update(2002) is False
    # And both are now stored, so a re-check returns True for each
    assert main._seen_update(2001) is True
    assert main._seen_update(2002) is True


# ============================================================================
# Edge case: missing update_id
# ============================================================================


def test_none_update_id_returns_false_without_storing():
    """If update_id is None (malformed payload), we treat as non-duplicate
    but do NOT store anything (would corrupt the set with None keys)."""
    assert main._seen_update(None) is False
    # Calling again should still be False — we didn't remember anything
    assert main._seen_update(None) is False
    # And the dict should still be empty
    assert len(main._PROCESSED_UPDATES) == 0


# ============================================================================
# TTL-based eviction
# ============================================================================


def test_ttl_eviction_after_max_age(monkeypatch):
    """An entry older than _DEDUP_MAX_AGE_SEC is forgotten on the next call."""
    # Freeze time at t=1000
    current_time = {"t": 1000.0}
    monkeypatch.setattr(main.time, "time", lambda: current_time["t"])

    # Mark id=3001 at t=1000
    assert main._seen_update(3001) is False
    # Still recent at t=1000+1 → duplicate
    current_time["t"] = 1001.0
    assert main._seen_update(3001) is True

    # Advance past TTL window — at t=1000 + MAX_AGE + 1
    current_time["t"] = 1000.0 + main._DEDUP_MAX_AGE_SEC + 1
    # Same id should now be treated as fresh (TTL-evicted)
    assert main._seen_update(3001) is False


def test_ttl_eviction_does_not_drop_recent_entries(monkeypatch):
    """When old entries age out, fresh entries in the same call should remain."""
    current_time = {"t": 1000.0}
    monkeypatch.setattr(main.time, "time", lambda: current_time["t"])

    main._seen_update(4001)  # old
    current_time["t"] = 1000.0 + main._DEDUP_MAX_AGE_SEC - 10
    main._seen_update(4002)  # fresh

    # Advance past TTL for 4001 but not 4002
    current_time["t"] = 1000.0 + main._DEDUP_MAX_AGE_SEC + 1
    # Insert another id which triggers eviction sweep
    main._seen_update(4003)

    # 4001 was evicted (old), 4002 still recent (within window)
    assert 4001 not in main._PROCESSED_UPDATES
    assert 4002 in main._PROCESSED_UPDATES
    assert 4003 in main._PROCESSED_UPDATES


# ============================================================================
# Size-cap eviction (FIFO)
# ============================================================================


def test_size_cap_evicts_oldest_fifo(monkeypatch):
    """When the dict grows past _DEDUP_MAX_SIZE, oldest entries are
    evicted FIFO regardless of their age."""
    # Freeze time so TTL doesn't interfere
    monkeypatch.setattr(main.time, "time", lambda: 5000.0)

    # Use a smaller cap for the test to keep it fast
    monkeypatch.setattr(main, "_DEDUP_MAX_SIZE", 3)

    main._seen_update(5001)
    main._seen_update(5002)
    main._seen_update(5003)
    # Dict is now at capacity (3 entries)
    assert len(main._PROCESSED_UPDATES) == 3

    # Adding a 4th should evict the oldest (5001)
    main._seen_update(5004)
    assert len(main._PROCESSED_UPDATES) == 3
    assert 5001 not in main._PROCESSED_UPDATES
    assert 5002 in main._PROCESSED_UPDATES
    assert 5003 in main._PROCESSED_UPDATES
    assert 5004 in main._PROCESSED_UPDATES

    # And 5001 is treated as fresh again
    assert main._seen_update(5001) is False
