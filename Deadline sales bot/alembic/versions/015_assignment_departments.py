"""assignment + departments — назначение лида на сотрудника и отделы (P3b)

Revision ID: 015_assignment_departments
Revises: 014_workspace_members
Create Date: 2026-06-13

Аддитивная миграция (только nullable-колонки) — прод не трогает, поведение 1:1
пока поля пусты.
- conversations.assigned_member_id — на кого назначен лид (id workspace_member, без
  жёсткого FK: app-level целостность, чтобы деактивация сотрудника не каскадила).
- workspace_members.department — отдел (напр. cleaning / repair).
- workspace_members.telegram_chat_id — личный chat сотрудника для уведомлений.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision: str = "015_assignment_departments"
down_revision: Union[str, None] = "014_workspace_members"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("workspace_members", sa.Column("department", sa.String(length=40), nullable=True))
    op.add_column("workspace_members", sa.Column("telegram_chat_id", sa.String(length=40), nullable=True))
    op.add_column("conversations", sa.Column("assigned_member_id", UUID(as_uuid=True), nullable=True))


def downgrade() -> None:
    op.drop_column("conversations", "assigned_member_id")
    op.drop_column("workspace_members", "telegram_chat_id")
    op.drop_column("workspace_members", "department")
