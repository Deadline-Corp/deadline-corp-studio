"""scheduled_actions — движок самоисполнения задач (Task Engine, Фаза B)

Revision ID: 007_scheduled_actions
Revises: 006_returning_lead_memory
Create Date: 2026-05-30

Своя очередь отложенных действий бота в Postgres — источник правды для крона.
Бот по ней работает (followup_message → шлёт сообщение лиду в Telegram в срок),
в HubSpot держит зеркальную задачу (crm_task_id). См. db/models.py::ScheduledAction
и services/scheduled_actions.py.

Миграция аддитивная (новая таблица) — безопасна, ничего существующего не трогает.
Идемпотентна на уровне alembic (version table).
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB


revision: str = "007_scheduled_actions"
down_revision: Union[str, None] = "006_returning_lead_memory"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "scheduled_actions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "customer_id", UUID(as_uuid=True),
            sa.ForeignKey("customers.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "conversation_id", UUID(as_uuid=True),
            sa.ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True,
        ),
        # channel хранится как VARCHAR (ORM: SQLEnum native_enum=False → enum NAME)
        sa.Column("channel", sa.String(length=20), nullable=False),
        sa.Column("chat_id", sa.String(length=200), nullable=True),
        sa.Column("action_type", sa.String(length=32), nullable=False),
        sa.Column("executor", sa.String(length=16), nullable=False, server_default="bot"),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("payload", JSONB(), nullable=True),
        sa.Column("crm_task_id", sa.String(length=100), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Горячий запрос крона: pending bot-действия со сроком <= now.
    op.create_index(
        "ix_scheduled_actions_due", "scheduled_actions",
        ["status", "executor", "due_at"],
    )
    op.create_index(
        "ix_scheduled_actions_conv", "scheduled_actions", ["conversation_id"],
    )
    op.create_index(
        "ix_scheduled_actions_customer_id", "scheduled_actions", ["customer_id"],
    )
    op.create_index(
        "ix_scheduled_actions_due_at", "scheduled_actions", ["due_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_scheduled_actions_due_at", table_name="scheduled_actions")
    op.drop_index("ix_scheduled_actions_customer_id", table_name="scheduled_actions")
    op.drop_index("ix_scheduled_actions_conv", table_name="scheduled_actions")
    op.drop_index("ix_scheduled_actions_due", table_name="scheduled_actions")
    op.drop_table("scheduled_actions")
