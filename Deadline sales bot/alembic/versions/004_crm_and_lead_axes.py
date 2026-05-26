"""CRM integration columns + Notion-spec lead axes

Revision ID: 004_crm_and_lead_axes
Revises: 003_training_corrections
Create Date: 2026-05-26

Phase 1 of CRM integration (see ADR v1.3 — 2026-05-26_ADR_CRM_Adapter_Architecture.md):

Adds columns to customers and conversations so the bot can:
  1. Sync each lead to an external CRM (HubSpot / Bitrix24 / NoOp) — crm_contact_id, crm_deal_id
  2. Carry Notion-spec orthogonal axes alongside funnel stage:
     - interaction_type   (Notion §4 — P1..P6 / HardStop, set once at first touch)
     - lead_score         (Notion §5 — base + content + source, decays on silence)
     - lead_temperature   (Notion §7 — cold/warm/hot/ready/client/frozen, dynamic)
     - identity_keys      (Notion §3 — extra dedup hooks for cross-channel merge)
  3. Track funnel position (Notion §20 — 11 active stages + lost+lost_reason)

All new columns on existing rows get safe defaults via server_default — the
migration is non-blocking and doesn't require any pre-fill data script.
Columns use plain String + check-at-app-layer rather than DB ENUMs because
the Notion spec is still evolving and we don't want migration friction
every time we add a new stage.

Migration is idempotent at the Alembic version-tracker level: no IF NOT
EXISTS guards in the SQL itself, but the version table prevents re-runs.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision: str = "004_crm_and_lead_axes"
down_revision: Union[str, None] = "003_training_corrections"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # =========================================================================
    # customers — CRM contact link + Notion §3 §4 §5 §7 axes
    # =========================================================================
    op.add_column(
        "customers",
        sa.Column(
            "crm_contact_id",
            sa.String(100),
            nullable=True,
            comment="ID of this lead's contact record in the external CRM. NULL until first sync.",
        ),
    )
    op.add_column(
        "customers",
        sa.Column(
            "lead_score",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
            comment="Notion §5 — base(interaction_type) + content(keywords) + source(canal). Decays on silence.",
        ),
    )
    op.add_column(
        "customers",
        sa.Column(
            "lead_temperature",
            sa.String(20),
            nullable=False,
            server_default=sa.text("'cold'"),
            comment="Notion §7 — cold/warm/hot/ready/client/frozen. Dynamic, has decay rules.",
        ),
    )
    op.add_column(
        "customers",
        sa.Column(
            "interaction_type",
            sa.String(10),
            nullable=False,
            server_default=sa.text("'P2'"),
            comment="Notion §4 — P1..P6 / HardStop. Set once at first touch, never changes.",
        ),
    )
    op.add_column(
        "customers",
        sa.Column(
            "identity_keys",
            JSONB,
            nullable=True,
            comment="Notion §3 — extra dedup hooks beyond email/phone (e.g. {tg_handle, ig_username}). "
                    "Used by CRMAdapter.upsert_contact to find existing contact across channels.",
        ),
    )
    op.create_index(
        "ix_customers_crm_contact_id",
        "customers",
        ["crm_contact_id"],
    )
    op.create_index(
        "ix_customers_lead_temperature",
        "customers",
        ["lead_temperature"],
    )

    # =========================================================================
    # conversations — CRM deal link + Notion §20 funnel stage
    # =========================================================================
    op.add_column(
        "conversations",
        sa.Column(
            "crm_deal_id",
            sa.String(100),
            nullable=True,
            comment="ID of this conversation's deal in the external CRM. NULL until first sync.",
        ),
    )
    op.add_column(
        "conversations",
        sa.Column(
            "lead_stage",
            sa.String(30),
            nullable=False,
            server_default=sa.text("'new_lead'"),
            comment="Notion §20 active funnel stage: new_lead/in_dialog/qualified/nda/on_call/"
                    "tz_approved/proposal/prepayment/in_work/completed_won/post_sale/lost.",
        ),
    )
    op.add_column(
        "conversations",
        sa.Column(
            "lost_reason",
            sa.String(30),
            nullable=True,
            comment="Notion §20 split — required iff lead_stage='lost'. "
                    "Values: price / not_our_format / competitor / delayed / no_budget / hard_stop.",
        ),
    )
    op.add_column(
        "conversations",
        sa.Column(
            "last_temperature_update_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="When customer.lead_temperature was last recalculated — used by decay cron job.",
        ),
    )
    op.create_index(
        "ix_conversations_crm_deal_id",
        "conversations",
        ["crm_deal_id"],
    )
    op.create_index(
        "ix_conversations_lead_stage",
        "conversations",
        ["lead_stage"],
    )


def downgrade() -> None:
    op.drop_index("ix_conversations_lead_stage", table_name="conversations")
    op.drop_index("ix_conversations_crm_deal_id", table_name="conversations")
    op.drop_column("conversations", "last_temperature_update_at")
    op.drop_column("conversations", "lost_reason")
    op.drop_column("conversations", "lead_stage")
    op.drop_column("conversations", "crm_deal_id")

    op.drop_index("ix_customers_lead_temperature", table_name="customers")
    op.drop_index("ix_customers_crm_contact_id", table_name="customers")
    op.drop_column("customers", "identity_keys")
    op.drop_column("customers", "interaction_type")
    op.drop_column("customers", "lead_temperature")
    op.drop_column("customers", "lead_score")
    op.drop_column("customers", "crm_contact_id")
