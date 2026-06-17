"""activity_log — журнал активности системы для админ-панели

Revision ID: 022_activity_log
Revises: 021_config_snapshots
Create Date: 2026-06-17

Единый журнал событий (что/как/почему/кто): смена конфигурации, ошибки, решения
бота (стадия/созвон/дожим), действия оператора (takeover, одобрение черновика),
массовые отправки, подключение каналов. Чтобы находить причины ошибок и изменений
прямо в панели (Настройки → расширенные → Логи), без копания в Railway-логах.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID


revision: str = "022_activity_log"
down_revision: Union[str, None] = "021_config_snapshots"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "activity_log",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("level", sa.String(10), nullable=False, server_default="info"),
        sa.Column("category", sa.String(24), nullable=False),
        sa.Column("actor", sa.String(60), nullable=False, server_default="system"),
        sa.Column("summary", sa.String(400), nullable=False),
        sa.Column("conversation_id", UUID(as_uuid=True), nullable=True),
        sa.Column("customer_id", UUID(as_uuid=True), nullable=True),
        sa.Column("meta", JSONB, nullable=True),
    )
    op.create_index("ix_activity_log_created", "activity_log", ["created_at"])
    op.create_index("ix_activity_log_cat_created", "activity_log", ["category", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_activity_log_cat_created", table_name="activity_log")
    op.drop_index("ix_activity_log_created", table_name="activity_log")
    op.drop_table("activity_log")
