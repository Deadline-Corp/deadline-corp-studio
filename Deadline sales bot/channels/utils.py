"""Shared channel helpers.

Currently:
- is_proactive_allowed(last_user_message_at) — Meta 24-hour messaging window
  per https://developers.facebook.com/docs/messenger-platform/policy/policy-overview
  After 24h since user's last message, the bot may only send messages with
  an allowed message tag (HUMAN_AGENT, CONFIRMED_EVENT_UPDATE, etc.).
  For MVP we just refuse to send.
- verify_meta_signature(...) — X-Hub-Signature-256 HMAC verification, used by
  the IG / FB webhook handlers.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional


log = logging.getLogger(__name__)

# Meta policy: 24h window since last inbound user message
META_PROACTIVE_WINDOW = timedelta(hours=24)


def is_proactive_allowed(last_user_message_at: Optional[datetime]) -> bool:
    """Return True if we can send a non-tagged message right now.

    Rule: must be ≤24h since the user's last message. If we have no record
    of a user message yet, return False (the very first outbound MUST be
    a reply, not proactive).
    """
    if last_user_message_at is None:
        return False
    # Tolerate naive datetimes from the DB by assuming UTC
    if last_user_message_at.tzinfo is None:
        last_user_message_at = last_user_message_at.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - last_user_message_at) < META_PROACTIVE_WINDOW


def verify_meta_signature(
    app_secret: Optional[str],
    signature_header: Optional[str],
    body: bytes,
) -> bool:
    """Verify Meta webhook signature (X-Hub-Signature-256).

    Meta signs request bodies with HMAC-SHA256 keyed by App Secret.
    Header format: "sha256=hex_digest"

    Fails CLOSED: returns True ONLY if a secret is configured AND the signature
    matches. A missing secret, missing/malformed header, or bad signature all
    return False. (An unauthenticated Meta webhook can inject content into lead
    conversations and trigger outbound replies — same threat as the Telegram
    webhook, so we refuse rather than accept when unverifiable.)
    """
    if not app_secret:
        # Fail CLOSED — refuse rather than accept an unverifiable webhook.
        log.error("META_APP_SECRET not set — rejecting Meta webhook (fail-closed)")
        return False

    if not signature_header or not signature_header.startswith("sha256="):
        log.warning(f"meta signature: missing or malformed header — got {signature_header!r}")
        return False

    expected = "sha256=" + hmac.new(
        app_secret.encode("utf-8"),
        msg=body,
        digestmod=hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected, signature_header):
        log.warning("meta signature mismatch")
        return False

    return True
