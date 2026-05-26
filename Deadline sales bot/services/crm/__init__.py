"""
CRM adapter package (Phase 1, 2026-05-26).

Public surface — import these from services.crm directly:

    from services.crm import CRMAdapter, build_adapter
    from services.crm import Lead, Deal, MessageLog
    from services.crm import LeadStage, LostReason, InteractionType, Temperature

The factory picks an implementation by env var CRM_PROVIDER:
    - "noop"     → NoOpAdapter, logs to our Postgres only (safe default)
    - "hubspot"  → HubSpotAdapter (Phase 2, TODO)
    - "bitrix24" → Bitrix24Adapter (Phase 3, deferred per Nikolay 2026-05-26)

When settings.crm_enabled == False, main.py never calls the adapter regardless
of provider — see the if-guards in /webhooks/* hot paths.
"""

from services.crm.base import (
    CRMAdapter,
    Channel,
    Deal,
    InteractionType,
    Lead,
    LeadStage,
    LostReason,
    MessageLog,
    TaskCategory,
    Temperature,
)
from services.crm.factory import build_adapter
from services.crm.noop import NoOpAdapter

__all__ = [
    "CRMAdapter",
    "Channel",
    "Deal",
    "InteractionType",
    "Lead",
    "LeadStage",
    "LostReason",
    "MessageLog",
    "NoOpAdapter",
    "TaskCategory",
    "Temperature",
    "build_adapter",
]
