"""automation_rules + automation_runs + custom_field_defs + stage_transitions

Revision ID: 013_automations_fields_analytics
Revises: 012_custom_funnel_and_settings
Create Date: 2026-06-11

Аддитивная миграция (4 новые таблицы) — прод не трогает.
Фундамент «AI Sales OS» (вариант B, модель GoHighLevel для RU):
- automation_rules/runs: конструктор «Когда → Если → То» без кода (исполняет крон)
- custom_field_defs: поля лида под нишу (значения в customers.profile_data)
- stage_transitions: история воронки → конверсионная аналитика
Пустые таблицы = поведение 1:1 как раньше.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB


revision: str = "013_automations_fields_analytics"
down_revision: Union[str, None] = "012_custom_funnel_and_settings"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "automation_rules",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("trigger", JSONB(), nullable=False),
        sa.Column("conditions", JSONB(), nullable=True),
        sa.Column("actions", JSONB(), nullable=False),
        sa.Column("cooldown_hours", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_automation_rules_enabled", "automation_rules", ["enabled"])

    op.create_table(
        "automation_runs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("rule_id", UUID(as_uuid=True),
                  sa.ForeignKey("automation_rules.id", ondelete="CASCADE"), nullable=False),
        sa.Column("conversation_id", UUID(as_uuid=True), nullable=False),
        sa.Column("fired_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("detail", JSONB(), nullable=True),
    )
    op.create_index("ix_automation_runs_rule_conv", "automation_runs", ["rule_id", "conversation_id"])

    op.create_table(
        "custom_field_defs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("key", sa.String(length=40), nullable=False, unique=True),
        sa.Column("label", sa.String(length=80), nullable=False),
        sa.Column("field_type", sa.String(length=10), nullable=False, server_default="text"),
        sa.Column("options", JSONB(), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "stage_transitions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("conversation_id", UUID(as_uuid=True), nullable=False),
        sa.Column("customer_id", UUID(as_uuid=True), nullable=True),
        sa.Column("from_stage", sa.String(length=40), nullable=True),
        sa.Column("to_stage", sa.String(length=40), nullable=False),
        sa.Column("by", sa.String(length=20), nullable=False, server_default="admin"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_stage_transitions_conv", "stage_transitions", ["conversation_id", "created_at"])
    op.create_index("ix_stage_transitions_created", "stage_transitions", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_stage_transitions_created", table_name="stage_transitions")
    op.drop_index("ix_stage_transitions_conv", table_name="stage_transitions")
    op.drop_table("stage_transitions")
    op.drop_table("custom_field_defs")
    op.drop_index("ix_automation_runs_rule_conv", table_name="automation_runs")
    op.drop_table("automation_runs")
    op.drop_index("ix_automation_rules_enabled", table_name="automation_rules")
    op.drop_table("automation_rules")
