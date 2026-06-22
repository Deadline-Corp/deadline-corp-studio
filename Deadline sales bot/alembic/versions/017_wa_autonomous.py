"""wa_autonomous — per-conversation «бот ведёт диалог сам» (WhatsApp)

Revision ID: 017_wa_autonomous
Revises: 016_pending_wa_draft
Create Date: 2026-06-14

Аддитивная миграция (один boolean со server_default false) — прод не трогает,
поведение 1:1. conversations.wa_autonomous: админ разрешил боту вести этот
конкретный диалог автономно (override над глобальным режимом наблюдения/черновика).
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "017_wa_autonomous"
down_revision: Union[str, None] = "016_pending_wa_draft"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column("wa_autonomous", sa.Boolean(), nullable=False, server_default="false"),
    )


def downgrade() -> None:
    op.drop_column("conversations", "wa_autonomous")
