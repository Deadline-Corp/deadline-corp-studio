"""kb_usage_stat: что бот цитирует из базы + вопросы без ответа в базе

Revision ID: 026_kb_usage_stat
Revises: 025_messages_conv_created_index
Create Date: 2026-06-22

Аналитика «мозга» для страницы «Мозг»: какие источники KB бот реально
ЦИТИРУЕТ в ответах (kind='cite', key=имя источника) и вопросы лидов, на
которые в базе НЕТ хорошего ответа (kind='gap', key=текст вопроса). Пишется
неблокирующе из пути ответа (services.kb_insights). Счётчик по (kind,key).

Аддитивно (CREATE TABLE) — безопасно при свапе контейнера деплоя.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "026_kb_usage_stat"
down_revision: Union[str, None] = "025_messages_conv_created_index"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "kb_usage_stat",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("kind", sa.String(length=8), nullable=False),
        sa.Column("key", sa.String(length=200), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("kind", "key", name="uq_kb_usage_kind_key"),
    )
    op.create_index("ix_kb_usage_kind", "kb_usage_stat", ["kind"])


def downgrade() -> None:
    op.drop_index("ix_kb_usage_kind", table_name="kb_usage_stat")
    op.drop_table("kb_usage_stat")
