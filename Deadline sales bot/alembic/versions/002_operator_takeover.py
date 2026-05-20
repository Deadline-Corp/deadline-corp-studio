"""operator takeover + forum topic id on conversations

Revision ID: 002_operator_takeover
Revises: 001_initial_schema
Create Date: 2026-05-20

Phase B (Operator visibility via Telegram forum-supergroup):
- conversations.operator_takeover BOOLEAN NOT NULL DEFAULT FALSE
  When True, the bot stops generating replies — every assistant message
  must come from a human in the operator group. Toggled via inline button
  "👤 Возьму на себя" / "🤖 Release" in the lead's forum topic.
- conversations.forum_topic_id INTEGER NULL
  Telegram forum topic id (within TELEGRAM_OPERATOR_GROUP_ID supergroup)
  where this conversation is mirrored. NULL = no topic yet.

Both columns indexed: operator_takeover for the "skip LLM" branch in
_handle_message (cheap lookup), forum_topic_id for reverse lookup from
operator-side messages back to the conversation.

Migration is idempotent: ADD COLUMN IF NOT EXISTS is not standard, but
Alembic skips if the schema already matches via its version table.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "002_operator_takeover"
down_revision: Union[str, None] = "001_initial_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # operator_takeover — bot pauses on a per-conversation basis
    op.add_column(
        "conversations",
        sa.Column(
            "operator_takeover",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.create_index(
        "ix_conversations_operator_takeover",
        "conversations",
        ["operator_takeover"],
    )

    # forum_topic_id — Telegram supergroup forum thread id
    op.add_column(
        "conversations",
        sa.Column(
            "forum_topic_id",
            sa.Integer(),
            nullable=True,
            comment="Telegram forum topic id in the operator supergroup",
        ),
    )
    op.create_index(
        "ix_conversations_forum_topic_id",
        "conversations",
        ["forum_topic_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_conversations_forum_topic_id", table_name="conversations")
    op.drop_column("conversations", "forum_topic_id")
    op.drop_index("ix_conversations_operator_takeover", table_name="conversations")
    op.drop_column("conversations", "operator_takeover")
