"""Phase 13: returning lead memory — archived_at, parent_conversation_id

Revision ID: 006_returning_lead_memory
Revises: 005_warming_dedup
Create Date: 2026-05-28 16:00:00
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "006_returning_lead_memory"
down_revision = "005_warming_dedup"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_conversations_archived_at",
        "conversations",
        ["archived_at"],
    )
    op.add_column(
        "conversations",
        sa.Column("parent_conversation_id", postgresql.UUID(as_uuid=True), nullable=True),
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
