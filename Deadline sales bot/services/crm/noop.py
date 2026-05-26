"""
NoOpAdapter — does nothing externally, logs locally.

Used when:
    1. settings.crm_provider == "noop" (no CRM configured, demo mode)
    2. settings.crm_enabled == False (master switch, even if provider set)
    3. Graceful degradation — real adapter throws, fall back to this so the
       bot keeps working (real CRM failure should never break the bot)

Methods return synthetic UUIDs in the place of CRM-side ids. The bot stores
these on Customer.crm_contact_id / Conversation.crm_deal_id like any other
adapter response, so the rest of the pipeline doesn't have to special-case
NoOp. When a real adapter is wired in later for an existing customer, those
synthetic ids will be overwritten on next upsert.
"""

import logging
import uuid
from datetime import datetime
from typing import Optional

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


def _fake_id(prefix: str) -> str:
    """Synthetic id with a prefix — `noop:contact:<uuid>`."""
    return f"noop:{prefix}:{uuid.uuid4().hex[:12]}"


class NoOpAdapter(CRMAdapter):
    provider_name = "noop"

    async def upsert_contact(self, lead: Lead) -> str:
        contact_id = _fake_id("contact")
        logger.info(
            "[noop crm] upsert_contact lead_id=%s channel=%s name=%s -> %s",
            lead.id, lead.channel, lead.contact_name, contact_id,
        )
        return contact_id

    async def create_deal(self, deal: Deal, contact_id: str) -> str:
        deal_id = _fake_id("deal")
        logger.info(
            "[noop crm] create_deal conv=%s contact=%s stage=%s title=%r -> %s",
            deal.conversation_id, contact_id, deal.stage, deal.title, deal_id,
        )
        return deal_id

    async def update_deal_stage(
        self,
        deal_id: str,
        stage: LeadStage,
        lost_reason: Optional[LostReason] = None,
    ) -> None:
        reason_part = f" lost_reason={lost_reason}" if stage == "lost" else ""
        logger.info("[noop crm] update_deal_stage deal=%s stage=%s%s", deal_id, stage, reason_part)

    async def update_lead_temperature(self, contact_id: str, temperature: Temperature) -> None:
        logger.info(
            "[noop crm] update_lead_temperature contact=%s temperature=%s",
            contact_id, temperature,
        )

    async def log_message(self, msg: MessageLog, contact_id: str) -> None:
        text_preview = (msg.text[:60] + "...") if len(msg.text) > 60 else msg.text
        logger.info(
            "[noop crm] log_message contact=%s role=%s channel=%s text=%r",
            contact_id, msg.role, msg.channel, text_preview,
        )

    async def create_task(
        self,
        contact_id: str,
        deal_id: Optional[str],
        title: str,
        due_at: datetime,
        category: TaskCategory = "callback",
        description: Optional[str] = None,
    ) -> str:
        task_id = _fake_id("task")
        logger.info(
            "[noop crm] create_task contact=%s deal=%s category=%s due=%s title=%r -> %s",
            contact_id, deal_id, category, due_at.isoformat(), title, task_id,
        )
        return task_id

    async def health_check(self) -> bool:
        # NoOp is always healthy by definition — no external dependency
        return True
