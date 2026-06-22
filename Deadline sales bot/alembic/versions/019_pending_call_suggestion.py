"""pending_call_suggestion — предложение созвона на подтверждение менеджера

Revision ID: 019_pending_call_suggestion
Revises: 018_wa_classification
Create Date: 2026-06-16

Аддитивная nullable JSONB-колонка. Бот распознаёт договорённость о созвоне в
переписке → кладёт сюда {at, when_human, medium, reason}; менеджер подтверждает в
карточке → создаётся событие в календаре. NULL = нет предложения.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision: str = "019_pending_call_suggestion"
down_revision: Union[str, None] = "018_wa_classification"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("conversations", sa.Column("pending_call_suggestion", JSONB, nullable=True))


def downgrade() -> None:
    op.drop_column("conversations", "pending_call_suggestion")
