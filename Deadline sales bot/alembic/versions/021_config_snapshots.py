"""config_snapshots — версии конфигурации (откат на любую точку)

Revision ID: 021_config_snapshots
Revises: 020_next_action
Create Date: 2026-06-16

Снимок конфигурации = бандл {воронка, кастом-поля, автоматизации, bot_settings,
активный системный промпт} в одной JSONB-строке. Авто-снимок ПЕРЕД каждым
конфиг-меняющим действием + ручной чекпойнт → откат на любую версию без потери
данных лидов (восстанавливаем ТОЛЬКО конфиг-таблицы, не переписки).
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID


revision: str = "021_config_snapshots"
down_revision: Union[str, None] = "020_next_action"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "config_snapshots",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("label", sa.String(120), nullable=False),
        sa.Column("created_by", sa.String(100), nullable=False, server_default="admin-ui"),
        sa.Column("reason", sa.String(200), nullable=True),
        sa.Column("payload", JSONB, nullable=False),
    )
    op.create_index("ix_config_snapshots_created", "config_snapshots", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_config_snapshots_created", table_name="config_snapshots")
    op.drop_table("config_snapshots")
