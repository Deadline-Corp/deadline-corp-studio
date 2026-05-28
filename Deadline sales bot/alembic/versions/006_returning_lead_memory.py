"""Phase 13: returning lead memory — archived_at, parent_conversation_id

Revision ID: 006_returning_lead_memory
Revises: 005_warming_dedup
Create Date: 2026-05-28 16:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "006_returning_lead_memory"
down_revision: Union[str, None] = "005_warming_dedup"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column(
            "archived_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="Set when this conversation was sidelined in favor of a new project branch (Phase 13)",
        ),
    )
    op.create_index(
        "ix_conversations_archived_at",
        "conversations",
        ["archived_at"],
    )
    op.add_column(
        "conversations",
        sa.Column(
            "parent_conversation_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
            comment="If this conversation was spawned as a new-project branch off an older one, links back to the parent. NULL for top-level (Phase 13).",
        ),
    )
    op.create_index(
        "ix_conversations_parent_conversation_id",
        "conversations",
        ["parent_conversation_id"],
    )
    op.create_foreign_key(
        "fk_conversations_parent_conversation_id",
        "conversations",
        "conversations",
        ["parent_conversation_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_conversations_parent_conversation_id", "conversations", type_="foreignkey"
    )
    op.drop_index("ix_conversations_parent_conversation_id", table_name="conversations")
    op.drop_column("conversations", "parent_conversation_id")
    op.drop_index("ix_conversations_archived_at", table_name="conversations")
    op.drop_column("conversations", "archived_at")
