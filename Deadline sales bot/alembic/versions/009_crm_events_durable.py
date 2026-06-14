"""crm_events — durable persistence очереди CRM-событий (recovery после рестарта)

Revision ID: 009_crm_events_durable
Revises: 008_dedup_and_claim
Create Date: 2026-06-01

Аддитивная миграция (новая таблица) — безопасна, ничего не трогает.
Очередь CRM была in-memory → при рестарте Railway несохранённые события (карточки,
стадии, созвоны, задачи) терялись. Теперь воркер пишет строку crm_events (pending),
помечает done/failed; на старте pending-строки восстанавливаются и переигрываются.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB


revision: str = "009_crm_events_durable"
down_revision: Union[str, None] = "008_dedup_and_claim"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "crm_events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        # customer_id / conversation_id — без FK намеренно: чтобы reset/удаление
        # клиента не каскадило и не ломало recovery (orphan просто no-op writeback).
        sa.Column("customer_id", UUID(as_uuid=True), nullable=True),
        sa.Column("conversation_id", UUID(as_uuid=True), nullable=True),
        sa.Column("payload", JSONB(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(length=500), nullable=True),
    )
    # Горячий запрос recovery: pending-события по времени.
    op.create_index(
        "ix_crm_events_status_created", "crm_events", ["status", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_crm_events_status_created", table_name="crm_events")
    op.drop_table("crm_events")
