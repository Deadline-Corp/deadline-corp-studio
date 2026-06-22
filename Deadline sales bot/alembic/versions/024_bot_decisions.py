"""bot_decisions — отдельный журнал РЕШЕНИЙ БОТА (что и почему)

Revision ID: 024_bot_decisions
Revises: 023_deal_value
Create Date: 2026-06-17

Отдельная от activity_log (системный журнал) таблица: каждое автономное решение
бота (стадия/созвон/дожим/хэндофф/классификация/recall/поля/win-back) с
человекочитаемой причиной. Цель — прозрачность логики бота для владельца.
Без FK (как activity_log) — reset/удаление не ломает историю. Идемпотентно.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID


revision: str = "024_bot_decisions"
down_revision: Union[str, None] = "023_deal_value"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "bot_decisions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("conversation_id", UUID(as_uuid=True), nullable=True),
        sa.Column("customer_id", UUID(as_uuid=True), nullable=True),
        sa.Column("decision_type", sa.String(32), nullable=False),
        sa.Column("reason", sa.String(600), nullable=False),
        sa.Column("detail", JSONB, nullable=True),
        sa.Column("actor", sa.String(24), nullable=False, server_default="bot"),
    )
    op.create_index("ix_bot_decisions_created", "bot_decisions", ["created_at"])
    op.create_index("ix_bot_decisions_conv_created", "bot_decisions", ["conversation_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_bot_decisions_conv_created", table_name="bot_decisions")
    op.drop_index("ix_bot_decisions_created", table_name="bot_decisions")
    op.drop_table("bot_decisions")
