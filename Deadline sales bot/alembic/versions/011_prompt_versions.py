"""prompt_versions — редактируемый системный промпт («мозг») из Admin UI

Revision ID: 011_prompt_versions
Revises: 010_lead_submissions
Create Date: 2026-06-11

Аддитивная миграция (новая таблица) — безопасна, прод не трогает.
Боевой промпт был только константой prompts.SYSTEM_PROMPT (деплой ради правки
тона). Теперь Admin UI пишет версии сюда; build_chat_prompt берёт активную
(services.prompt_store, TTL-кэш 60с) с фоллбэком на константу. Пустая таблица
= поведение 1:1 как раньше.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision: str = "011_prompt_versions"
down_revision: Union[str, None] = "010_lead_submissions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "prompt_versions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("kind", sa.String(length=32), nullable=False, server_default="system_prompt"),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("comment", sa.String(length=500), nullable=True),
        sa.Column("created_by", sa.String(length=100), nullable=False, server_default="admin-ui"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
    )
    # Горячий запрос prompt_store: активная версия данного kind.
    op.create_index(
        "ix_prompt_versions_active", "prompt_versions", ["kind", "is_active"],
    )


def downgrade() -> None:
    op.drop_index("ix_prompt_versions_active", table_name="prompt_versions")
    op.drop_table("prompt_versions")
