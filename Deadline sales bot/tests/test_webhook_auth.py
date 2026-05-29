"""Authn tests for the inbound Telegram webhook + admin token gates.

Covers the 2026-05-29 security fix:
  - /webhooks/telegram now fails CLOSED on a missing/mismatched
    X-Telegram-Bot-Api-Secret-Token. Previously the endpoint was
    unauthenticated — anyone who knew the public Railway URL could forge an
    update that impersonates an operator and pushes attacker-controlled text
    to real leads through the bot's own outbound tokens.
  - /metrics and /admin/training/* token checks now use hmac.compare_digest
    instead of `!=` (constant-time).

These hit real FastAPI routes via TestClient. We deliberately do NOT enter the
TestClient as a context manager, so the app's startup event never runs (no
webhook registration / heavy init). The background pipeline is monkeypatched
to a no-op so a valid-secret call returns 200 without touching the DB or LLM.

Run from project root:
    venv/Scripts/python.exe -m pytest tests/test_webhook_auth.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# Make `import main` work when pytest is invoked from project root.
sys.path.insert(0, str(Path(__file__).parent.parent))

import main  # noqa: E402


@pytest.fixture
def client(monkeypatch):
    # Neutralise the heavy background pipeline so a valid-secret webhook call
    # returns 200 without spawning the real _handle_message thread (DB + LLM).
    monkeypatch.setattr(main, "_process_in_thread", lambda payload: None)
    # Isolate dedup state so update_ids can't collide across tests.
    main._PROCESSED_UPDATES.clear()
    yield TestClient(main.app)
    main._PROCESSED_UPDATES.clear()


_PAYLOAD = {
    "message": {
        "chat": {"id": 1, "type": "private"},
        "from": {"id": 1, "is_bot": False},
        "text": "hi",
    }
}


# ---- /webhooks/telegram — fail-closed secret check -------------------------

def test_telegram_webhook_refuses_when_secret_unset(client, monkeypatch):
    """Misconfiguration (no TELEGRAM_WEBHOOK_SECRET) → 503, nothing processed."""
    monkeypatch.setattr(main.settings, "telegram_webhook_secret", None)
    r = client.post("/webhooks/telegram", json={**_PAYLOAD, "update_id": 10})
    assert r.status_code == 503


def test_telegram_webhook_rejects_missing_header(client, monkeypatch):
    """Secret configured but the request carries no header → 401."""
    monkeypatch.setattr(main.settings, "telegram_webhook_secret", "right-secret")
    r = client.post("/webhooks/telegram", json={**_PAYLOAD, "update_id": 11})
    assert r.status_code == 401


def test_telegram_webhook_rejects_wrong_secret(client, monkeypatch):
    """Forged update with the wrong secret → 401."""
    monkeypatch.setattr(main.settings, "telegram_webhook_secret", "right-secret")
    r = client.post(
        "/webhooks/telegram",
        json={**_PAYLOAD, "update_id": 12},
        headers={"X-Telegram-Bot-Api-Secret-Token": "wrong-secret"},
    )
    assert r.status_code == 401


def test_telegram_webhook_accepts_valid_secret(client, monkeypatch):
    """Genuine Telegram update carrying the matching secret → 200."""
    monkeypatch.setattr(main.settings, "telegram_webhook_secret", "right-secret")
    r = client.post(
        "/webhooks/telegram",
        json={**_PAYLOAD, "update_id": 13},
        headers={"X-Telegram-Bot-Api-Secret-Token": "right-secret"},
    )
    assert r.status_code == 200


# ---- admin token gates use constant-time compare ---------------------------

def test_metrics_rejects_wrong_token(client, monkeypatch):
    """Wrong bearer on /metrics → 403 (constant-time compare path)."""
    monkeypatch.setattr(main.settings, "metrics_auth_token", "secret-metrics")
    r = client.get("/metrics", headers={"Authorization": "Bearer nope"})
    assert r.status_code == 403


def test_training_rejects_wrong_token(client, monkeypatch):
    """Wrong bearer on /admin/training/list → 403, before any DB access."""
    monkeypatch.setattr(main.settings, "training_auth_token", "secret-train")
    r = client.get("/admin/training/list", headers={"Authorization": "Bearer nope"})
    assert r.status_code == 403
