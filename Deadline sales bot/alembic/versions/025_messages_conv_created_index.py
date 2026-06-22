"""messages: составной индекс (conversation_id, created_at) — горячий паттерн

Revision ID: 025_messages_conv_created_index
Revises: 024_bot_decisions
Create Date: 2026-06-20

Самый частый запрос в системе — история диалога: WHERE conversation_id ORDER BY
created_at DESC LIMIT (на КАЖДОМ ходе лида в hot-path и на каждой странице карточки
в админке). Был только одиночный индекс по conversation_id → Postgres сортировал
результат на каждом запросе. Составной (conversation_id, created_at) закрывает и
фильтр, и сортировку — самый дешёвый перф-выигрыш по hot-path и панели.

Идемпотентно (IF NOT EXISTS). Обычный CREATE INDEX (в транзакции миграции) — таблица
messages в этом проекте небольшая, кратковременный лок на запись приемлем при свапе
контейнера деплоя. Старый одиночный ix_messages_conversation_id не трогаем (отдельная
уборка), чтобы не рисковать.
"""

from typing import Sequence, Union

from alembic import op


revision: str = "025_messages_conv_created_index"
down_revision: Union[str, None] = "024_bot_decisions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_messages_conv_created "
        "ON messages (conversation_id, created_at)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_messages_conv_created")
