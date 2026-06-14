"""workspace_members — роли (owner/менеджер) для Admin UI

Revision ID: 014_workspace_members
Revises: 013_automations_fields_analytics
Create Date: 2026-06-12

Аддитивная миграция (одна таблица) — прод не трогает.
Owner = главный токен из env; менеджеры — именные токены здесь (sha256-хэш,
сам токен показывается один раз). Пустая таблица = поведение как раньше
(вход только по главному токену).
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision: str = "014_workspace_members"
down_revision: Union[str, None] = "013_automations_fields_analytics"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "workspace_members",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False, server_default="manager"),
        sa.Column("token_hash", sa.String(length=64), nullable=False, unique=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("workspace_members")
