"""pipeline_stages + bot_settings — своя воронка и настройки поведения из Admin UI

Revision ID: 012_custom_funnel_and_settings
Revises: 011_prompt_versions
Create Date: 2026-06-11

Аддитивная миграция (две новые таблицы) — прод не трогает.
- pipeline_stages: кастомные стадии канбана (пустая таблица = встроенные 8).
- bot_settings: key-value оверрайды tenant config (прогрев/нудж) без деплоя.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB


revision: str = "012_custom_funnel_and_settings"
down_revision: Union[str, None] = "011_prompt_versions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "pipeline_stages",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("key", sa.String(length=40), nullable=False, unique=True),
        sa.Column("label", sa.String(length=80), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("kind", sa.String(length=10), nullable=False, server_default="active"),
        sa.Column("builtin", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
    )
    op.create_index("ix_pipeline_stages_pos", "pipeline_stages", ["active", "position"])

    op.create_table(
        "bot_settings",
        sa.Column("key", sa.String(length=60), primary_key=True),
        sa.Column("value", JSONB(), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("bot_settings")
    op.drop_index("ix_pipeline_stages_pos", table_name="pipeline_stages")
    op.drop_table("pipeline_stages")
