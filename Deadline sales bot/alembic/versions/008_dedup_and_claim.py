"""processed_updates (дедуп вебхуков) + scheduled_actions.claimed_at (клейм крона)

Revision ID: 008_dedup_and_claim
Revises: 007_scheduled_actions
Create Date: 2026-06-01

Аддитивная миграция — безопасна, ничего существующего не удаляет/не меняет:
  1. Таблица processed_updates — переживающий рестарт дедуп входящих апдейтов
     (раньше был in-memory → после деплоя Railway возможны дубли ответов).
  2. Столбец scheduled_actions.claimed_at (nullable) — атомарный клейм строк
     кроном (FOR UPDATE SKIP LOCKED + status='processing'), защита от двойной
     отправки напоминаний/followup при нескольких инстансах.
Идемпотентна на уровне alembic (version table).
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "008_dedup_and_claim"
down_revision: Union[str, None] = "007_scheduled_actions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "processed_updates",
        sa.Column("event_key", sa.String(length=120), primary_key=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
    )
    op.add_column(
        "scheduled_actions",
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("scheduled_actions", "claimed_at")
    op.drop_table("processed_updates")
