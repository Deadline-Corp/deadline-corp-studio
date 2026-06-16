"""next_action — умная следующая задача по лиду (CRM-логика)

Revision ID: 020_next_action
Revises: 019_pending_call_suggestion
Create Date: 2026-06-16

Аддитивная nullable JSONB-колонка conversations.next_action. Мозг читает диалог и
решает следующий шаг по лиду: {mode, label, draft, reason, ts}. Показывается в
задачнике, когда нет конкретного scheduled_action. NULL = ещё не считан.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision: str = "020_next_action"
down_revision: Union[str, None] = "019_pending_call_suggestion"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("conversations", sa.Column("next_action", JSONB, nullable=True))


def downgrade() -> None:
    op.drop_column("conversations", "next_action")
