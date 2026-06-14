"""pending_wa_draft — черновик WhatsApp-ответа на одобрение администратора

Revision ID: 016_pending_wa_draft
Revises: 015_assignment_departments
Create Date: 2026-06-14

Аддитивная миграция (одна nullable JSONB-колонка) — прод не трогает, поведение
1:1 пока wa_draft_mode выключен.
- conversations.pending_wa_draft — {text, phone_number_id, to_wa_id, client_msg, ts}
  ожидающего одобрения черновика; NULL = нет ожидающего.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision: str = "016_pending_wa_draft"
down_revision: Union[str, None] = "015_assignment_departments"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("conversations", sa.Column("pending_wa_draft", JSONB, nullable=True))


def downgrade() -> None:
    op.drop_column("conversations", "pending_wa_draft")
