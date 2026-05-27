"""Conversation.last_warmed_at for warming dedup

Revision ID: 005_warming_dedup
Revises: 004_crm_and_lead_axes
Create Date: 2026-05-27

Phase 10d (see ADR v1.3 + Phase 9 lessons learned).

Without this column the cron worker dispatches a fresh warming task to
HubSpot every hour for every silent customer — at 24 silent hours per
day per customer that's 24 duplicate "Warm cold lead" tasks landing in
the operator inbox. The column stores when we last successfully
dispatched a warming task; plan_warming honours the per-bucket cadence
(hot=1d, warm=7d, cold=21d, frozen=90d) against this timestamp.

Server_default omitted on purpose — existing rows get NULL, which the
cron interprets as "never warmed" and therefore eligible. First warming
sweep after this migration may produce one task per silent customer (a
small one-time burst); subsequent sweeps will dedup correctly.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "005_warming_dedup"
down_revision: Union[str, None] = "004_crm_and_lead_axes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column(
            "last_warmed_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="When the cron last dispatched a warming task for this conversation. "
                    "NULL = never warmed. Cron honours per-bucket cadence against this.",
        ),
    )


def downgrade() -> None:
    op.drop_column("conversations", "last_warmed_at")
