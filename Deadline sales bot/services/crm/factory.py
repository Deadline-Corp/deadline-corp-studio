"""
build_adapter() — pick a CRMAdapter implementation based on env config.

The factory is the only place that knows about concrete adapter classes.
Callers receive an opaque CRMAdapter — they never construct one directly.

Selection rule (in order):
    1. If settings.crm_enabled == False  →  NoOpAdapter (master switch off)
    2. settings.crm_provider determines which concrete adapter to construct:
        - "noop"     → NoOpAdapter
        - "hubspot"  → HubSpotAdapter (Phase 2, raises NotImplementedError today)
        - "bitrix24" → Bitrix24Adapter (Phase 3 deferred, raises NotImplementedError)
    3. If the chosen adapter is misconfigured (missing creds), we log a
       warning and fall back to NoOpAdapter — the bot keeps working without
       CRM rather than crashing.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from services.crm.base import CRMAdapter
from services.crm.noop import NoOpAdapter

if TYPE_CHECKING:
    # Avoid runtime circular import — Settings lives in main.py
    from main import Settings


logger = logging.getLogger(__name__)


def build_adapter(settings: "Settings") -> CRMAdapter:
    """Construct the CRM adapter selected by env config.

    Always returns a working adapter — never raises for config issues,
    only logs and falls back to NoOp. This keeps the bot operational
    even if HubSpot creds get rotated and the env isn't updated yet.
    """
    if not settings.crm_enabled:
        logger.info("[crm] CRM_ENABLED=False, using NoOpAdapter")
        return NoOpAdapter()

    provider = (settings.crm_provider or "noop").lower().strip()

    if provider == "noop":
        logger.info("[crm] CRM_PROVIDER=noop, using NoOpAdapter")
        return NoOpAdapter()

    if provider == "hubspot":
        # Phase 2 — HubSpotAdapter not yet implemented
        if not settings.hubspot_access_token:
            logger.warning(
                "[crm] CRM_PROVIDER=hubspot but HUBSPOT_ACCESS_TOKEN unset; "
                "falling back to NoOpAdapter"
            )
            return NoOpAdapter()
        try:
            from services.crm.hubspot import HubSpotAdapter  # noqa: F401 - Phase 2
            return HubSpotAdapter(
                access_token=settings.hubspot_access_token,
                portal_id=settings.hubspot_portal_id,
                region=settings.hubspot_region,
                owner_id=settings.hubspot_owner_id,
            )
        except ImportError:
            logger.warning(
                "[crm] HubSpotAdapter not yet implemented (Phase 2); "
                "falling back to NoOpAdapter"
            )
            return NoOpAdapter()

    if provider == "bitrix24":
        # Phase 3 deferred per Nikolay 2026-05-26 — same fallback pattern
        if not settings.bitrix24_webhook_url:
            logger.warning(
                "[crm] CRM_PROVIDER=bitrix24 but BITRIX24_WEBHOOK_URL unset; "
                "falling back to NoOpAdapter"
            )
            return NoOpAdapter()
        try:
            from services.crm.bitrix24 import Bitrix24Adapter  # noqa: F401 - Phase 3
            return Bitrix24Adapter(
                webhook_url=settings.bitrix24_webhook_url,
                default_category_id=settings.bitrix24_default_category_id,
            )
        except ImportError:
            logger.warning(
                "[crm] Bitrix24Adapter not yet implemented (Phase 3 deferred); "
                "falling back to NoOpAdapter"
            )
            return NoOpAdapter()

    logger.warning(
        "[crm] Unknown CRM_PROVIDER=%r; falling back to NoOpAdapter", provider
    )
    return NoOpAdapter()
