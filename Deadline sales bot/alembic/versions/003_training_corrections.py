"""training_corrections — operator-supplied learning rules

Revision ID: 003_training_corrections
Revises: 002_operator_takeover
Create Date: 2026-05-26

T-1 (Training loop feature): new table `training_corrections` for
storing operator-approved adjustments to bot behaviour. Populated via
the /admin/training UI when a human reviews a past conversation, marks
a bot reply as wrong, and approves a better alternative.

At inference time the bot does a vector similarity search across active
corrections (top-K by bge-m3 embedding of the lead's current message)
and injects the matching guidance as a "LESSONS FROM PAST CORRECTIONS"
block in the SYSTEM_PROMPT. Lessons take priority over generic KB style.

Schema highlights:
- UUID PK with default uuid_generate_v4() server-side
- HNSW index on embedding for sub-10ms top-K cosine search
- Soft-delete via is_active + audit trail via superseded_by_id
- Optional FK to conversations for "this rule came from talking to lead X"

The HNSW index is created via raw SQL because pgvector's index_type
clause isn't directly representable in Alembic's CreateIndex op.

Migration is idempotent: ALTER TABLE / CREATE INDEX commands rely on
Alembic's version tracking, not IF NOT EXISTS at SQL level.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID
from pgvector.sqlalchemy import Vector


revision: str = "003_training_corrections"
down_revision: Union[str, None] = "002_operator_takeover"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


EMBEDDING_DIM = 1024  # bge-m3


def upgrade() -> None:
    op.create_table(
        "training_corrections",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("trigger_context", sa.Text(), nullable=False,
                  comment="Last 3-6 messages from the original conversation that triggered the correction"),
        sa.Column("correct_guidance", sa.Text(), nullable=False,
                  comment="Human-readable instruction on what the bot should change in similar situations"),
        sa.Column("suggested_response", sa.Text(), nullable=True,
                  comment="Optional concrete sample of the better response (used as a few-shot anchor)"),
        sa.Column("channel", sa.String(32), nullable=True,
                  comment="Optional channel scope: NULL = applies everywhere"),
        sa.Column("embedding", Vector(EMBEDDING_DIM), nullable=True,
                  comment="bge-m3 embedding of trigger_context for similarity retrieval"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_by", sa.String(100), nullable=False, server_default=sa.text("'admin'")),
        sa.Column("source_conversation_id", UUID(as_uuid=True),
                  sa.ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true(),
                  comment="Soft-delete flag — inactive rules are kept for audit but not retrieved at inference"),
        sa.Column("superseded_by_id", UUID(as_uuid=True),
                  sa.ForeignKey("training_corrections.id", ondelete="SET NULL"), nullable=True,
                  comment="Versioning chain — points to the rule that replaced this one"),
    )

    # B-tree indexes for filtering. The `is_active` filter is hit on every
    # retrieval (we only want live rules), and `channel` for per-channel
    # tuning ("telegram"-specific rule vs global).
    op.create_index(
        "ix_training_corrections_active",
        "training_corrections",
        ["is_active"],
    )
    op.create_index(
        "ix_training_corrections_channel",
        "training_corrections",
        ["channel"],
    )

    # HNSW index for similarity search. Same params as kb_chunks
    # (m=16, ef_construction=64) since the workload is identical:
    # 1024-dim bge-m3 vectors, cosine distance. ~10ms top-K up to 10K rows.
    op.execute(
        "CREATE INDEX ix_training_corrections_embedding_hnsw "
        "ON training_corrections "
        "USING hnsw (embedding vector_cosine_ops) "
        "WITH (m = 16, ef_construction = 64);"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_training_corrections_embedding_hnsw;")
    op.drop_index("ix_training_corrections_channel", table_name="training_corrections")
    op.drop_index("ix_training_corrections_active", table_name="training_corrections")
    op.drop_table("training_corrections")
