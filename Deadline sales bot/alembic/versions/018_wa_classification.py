"""wa_classification — триаж лид/не-лид импортированных WhatsApp-чатов

Revision ID: 018_wa_classification
Revises: 017_wa_autonomous
Create Date: 2026-06-14

Аддитивная миграция (один JSONB nullable) — прод не трогает, поведение 1:1.
conversations.wa_classification: результат классификации диалога при импорте
истории из WAHA (services/whatsapp_sync.py + lead_classifier): лид это или нет,
уверенность, категория, причина, температура. NULL = не классифицирован.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "018_wa_classification"
down_revision: Union[str, None] = "017_wa_autonomous"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column("wa_classification", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("conversations", "wa_classification")
