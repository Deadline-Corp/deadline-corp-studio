"""deal_value — сумма сделки на разговоре для revenue-аналитики

Revision ID: 023_deal_value
Revises: 022_activity_log
Create Date: 2026-06-17

Сумма сделки (deal_value) + валюта (deal_currency) на conversations — чтобы считать
выручку по воронке/каналам, средний чек, потери в деньгах («сколько денег принёс бот»).
Оба поля nullable → существующие строки валидны без бэкфилла. Идемпотентно.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "023_deal_value"
down_revision: Union[str, None] = "022_activity_log"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("conversations", sa.Column("deal_value", sa.Numeric(14, 2), nullable=True))
    op.add_column("conversations", sa.Column("deal_currency", sa.String(8), nullable=True))


def downgrade() -> None:
    op.drop_column("conversations", "deal_currency")
    op.drop_column("conversations", "deal_value")
