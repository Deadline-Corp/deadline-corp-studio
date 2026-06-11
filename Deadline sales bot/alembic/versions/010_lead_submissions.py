"""lead_submissions — durable record of every lead-form submission + contact-check verdict

Revision ID: 010_lead_submissions
Revises: 009_crm_events_durable
Create Date: 2026-06-09

Аддитивная миграция (новая таблица) — безопасна, ничего не трогает.
Раньше POST /lead-submit был fire-and-forget (только сообщение в Telegram):
опечатка в контакте = безвозвратно потерянный лид. Теперь каждая заявка пишется
в lead_submissions: поля формы + вердикт проверки контакта (contact_type /
contact_exists) + ip / user_agent / referer.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision: str = "010_lead_submissions"
down_revision: Union[str, None] = "009_crm_events_durable"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "lead_submissions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("contact", sa.String(length=200), nullable=False),
        sa.Column("contact_type", sa.String(length=20), nullable=True),
        sa.Column("contact_exists", sa.Boolean(), nullable=True),
        sa.Column("need", sa.String(length=500), nullable=True),
        sa.Column("business", sa.String(length=300), nullable=True),
        sa.Column("task", sa.Text(), nullable=True),
        sa.Column("timeframe", sa.String(length=50), nullable=True),
        sa.Column("source", sa.String(length=100), nullable=True),
        sa.Column("campaign", sa.String(length=200), nullable=True),
        sa.Column("lang", sa.String(length=5), nullable=True),
        sa.Column("ip", sa.String(length=64), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("referer", sa.Text(), nullable=True),
        sa.Column("customer_id", UUID(as_uuid=True), nullable=True),
        sa.Column(
            "telegram_delivered", sa.Boolean(), nullable=False, server_default="false"
        ),
        sa.Column(
            "crm_enqueued", sa.Boolean(), nullable=False, server_default="false"
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
    )
    op.create_index("ix_lead_submissions_created", "lead_submissions", ["created_at"])
    op.create_index("ix_lead_submissions_customer", "lead_submissions", ["customer_id"])


def downgrade() -> None:
    op.drop_index("ix_lead_submissions_customer", table_name="lead_submissions")
    op.drop_index("ix_lead_submissions_created", table_name="lead_submissions")
    op.drop_table("lead_submissions")
