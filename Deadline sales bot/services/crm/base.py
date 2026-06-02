"""
CRMAdapter abstract base class + value types.

This is the contract every CRM integration must satisfy. Concrete adapters:
    - NoOpAdapter (services/crm/noop.py) — Phase 1, default
    - HubSpotAdapter (services/crm/hubspot.py) — Phase 2, in progress
    - Bitrix24Adapter (services/crm/bitrix24.py) — Phase 3, deferred

Design notes:

1. Methods are async because most real adapters do network I/O. The NoOp
   still uses `async def` so the call sites don't have to branch on adapter type.

2. Return values for upsert_contact / create_deal / create_task are the
   CRM's own ID strings. The caller (services/funnel.py et al) stores them on
   our Customer.crm_contact_id and Conversation.crm_deal_id columns so future
   updates target the right row.

3. update_deal_stage takes an optional lost_reason — required when stage ==
   "lost", ignored otherwise. We did NOT make a separate `close_deal_lost`
   method because most CRMs treat "lost" as a stage with reason metadata,
   not a separate operation.

4. Notion §3 / §4 / §5 / §7 — extra axes (InteractionType, LeadScore,
   Temperature, identity_keys for merge) live as fields on Lead and as
   custom properties on the CRM contact. update_lead_temperature is the
   one frequently-updated axis so it gets its own method; score / interaction
   stay write-once at upsert_contact time.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Optional


# =============================================================================
# Type aliases — match db enums where they overlap
# =============================================================================

Channel = Literal[
    "telegram",
    "instagram",
    "messenger",
    "website",
    "whatsapp",
    "email",
]

# Active funnel stages — Notion §20 (11 stages) + terminal "lost"
LeadStage = Literal[
    "new_lead",
    "in_dialog",
    "qualified",
    "nda",
    "on_call",
    "tz_approved",
    "proposal",
    "prepayment",
    "in_work",
    "completed_won",
    "post_sale",
    "lost",
]

# Reason when stage == "lost" (Notion §20 расщепление)
LostReason = Literal[
    "price",
    "not_our_format",
    "competitor",
    "delayed",
    "no_budget",
    "hard_stop",
]

# First-touch interaction type (Notion §4) — fixed at creation, never changes
InteractionType = Literal[
    "P1",  # direct request / ad click
    "P2",  # form submission without explicit need
    "P3",  # return of a cold lead
    "P4",  # neutral reply, comment
    "P5",  # stories, reactions
    "P6",  # we write first (outbound)
    "HardStop",
]

# Lead temperature (Notion §7) — dynamic, has decay rules
Temperature = Literal[
    "cold",
    "warm",
    "hot",
    "ready",
    "client",
    "frozen",
]

# Task category — for CRM task tagging
TaskCategory = Literal[
    "qualification",  # operator: take this lead (after handoff_classifier)
    "warming",        # bot/operator: re-engage silent lead
    "dunning",        # operator: chase overdue payment
    "callback",       # operator: scheduled call / follow-up
]


# =============================================================================
# Value objects passed to the adapter
# =============================================================================

@dataclass
class Lead:
    """One lead = one Customer in our DB. Mapped to a Contact in the CRM.

    identity_keys are used for dedup/merge on the CRM side: the adapter
    looks up an existing contact by email or phone before creating a new
    one (Notion §3 identity merge).
    """
    id: str                          # our customer.id (UUID stringified)
    contact_name: Optional[str]
    contact_handle: Optional[str]    # @username or email
    channel: Channel
    channel_user_id: str             # external_id on the channel
    first_message_at: datetime
    source_url: Optional[str]

    # Notion §4 §5 §7 §3 axes — see config.yaml
    interaction_type: InteractionType = "P2"
    temperature: Temperature = "cold"
    score: int = 0
    identity_keys: dict = field(default_factory=dict)
    # Free-form metadata mirrored from Customer.profile_data — company,
    # industry, etc, as the bot enriches over conversation
    profile: dict = field(default_factory=dict)


@dataclass
class Deal:
    """One deal per conversation. Mapped to a Deal in the CRM, in the
    custom pipeline configured via tenant config.yaml (crm.pipeline_name).
    """
    lead_id: str                       # our customer.id
    conversation_id: str               # our conversation.id
    title: str                         # "<name> — <project_type>"
    stage: LeadStage
    lost_reason: Optional[LostReason] = None  # required iff stage == "lost"
    project_type: Optional[str] = None        # web | automation | ai_agents | other
    estimated_budget: Optional[str] = None    # operator-fillable; bot leaves None
    estimated_timeline: Optional[str] = None  # same
    brief: Optional[str] = None               # short summary from handoff_classifier


@dataclass
class MessageLog:
    """One CRM timeline entry per chat message (lead / bot / operator).

    metadata can carry voice_duration_s, has_image, training_rule_id, etc.
    Adapters serialize this as a JSON block in the message body when the
    CRM has no native field for it.
    """
    lead_id: str
    conversation_id: str
    role: Literal["lead", "bot", "operator"]
    channel: Channel
    text: str
    timestamp: datetime
    metadata: dict = field(default_factory=dict)


# =============================================================================
# Abstract base class — every adapter implements this
# =============================================================================

class CRMAdapter(ABC):
    """Contract for any CRM backend (HubSpot, Bitrix24, Notion-as-CRM, NoOp).

    Hot-path callers (webhooks/*, services/funnel.py) only depend on this
    interface — adapter swap = one line in factory.build_adapter().
    """

    # Adapter implementations set this in __init__ for logging / diagnostics
    provider_name: str = "abstract"

    @abstractmethod
    async def upsert_contact(self, lead: Lead, known_id: Optional[str] = None) -> str:
        """Create or update a contact. Returns the CRM-side contact id.

        Implementations should dedup by lead.identity_keys (email, phone,
        tg_handle) before creating a new contact, so the same person from
        different channels lands on one record.

        known_id: если задан — обновлять контакт по этому id (минуя поиск).
        """

    @abstractmethod
    async def create_deal(self, deal: Deal, contact_id: str) -> str:
        """Create a deal in the configured pipeline. Returns CRM deal id.

        On first run, the adapter may need to auto-create the pipeline +
        custom properties — driven by tenant config.crm.auto_create_pipeline.
        """

    async def find_open_deal_for_contact(self, contact_id: Optional[str]) -> Optional[str]:
        """Вернуть id ОТКРЫТОЙ сделки контакта (не закрытой won/lost) или None.

        Дедуп #2: один клиент = одна активная сделка. Перед созданием новой
        сделки воркер зовёт это; если открытая уже есть — переиспользует её
        вместо плодения «2 карточек». Дефолт — None (адаптеры без поддержки
        просто всегда создают новую, как раньше)."""
        return None

    @abstractmethod
    async def update_deal_stage(
        self,
        deal_id: str,
        stage: LeadStage,
        lost_reason: Optional[LostReason] = None,
    ) -> None:
        """Move a deal to a new stage. If stage == 'lost', lost_reason is required."""

    @abstractmethod
    async def update_lead_temperature(
        self,
        contact_id: str,
        temperature: Temperature,
    ) -> None:
        """Notion §7 — frequently changes as bot signals fire / decay applies."""

    @abstractmethod
    async def log_message(self, msg: MessageLog, contact_id: str) -> None:
        """Add a timeline entry to the contact / deal."""

    @abstractmethod
    async def create_task(
        self,
        contact_id: str,
        deal_id: Optional[str],
        title: str,
        due_at: datetime,
        category: TaskCategory = "callback",
        description: Optional[str] = None,
    ) -> str:
        """Create a task for the operator. Returns CRM task id."""

    async def complete_task(self, task_id: str) -> bool:
        """Mark a CRM task COMPLETED (закрыть зеркальную задачу после
        самоисполнения ботом). Default no-op — переопределяется в адаптере."""
        return False

    async def update_task(self, task_id: str, subject=None, body=None) -> bool:
        """Дополнить задачу (тема/тело). Default no-op — переопределяется в адаптере."""
        return False

    @abstractmethod
    async def health_check(self) -> bool:
        """Cheap call to verify credentials + connectivity. Called at startup."""
