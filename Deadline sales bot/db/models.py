"""SQLAlchemy ORM модели для Deadline Sales Bot.

Дизайн:
- UUID PK везде (легче для будущей миграции в multi-tenant)
- Все таблицы в схеме public (default)
- `embedding` — pgvector тип, размерность 1024 (bge-m3)
- HNSW индексы на embedding создаются через миграцию alembic, не здесь
- Все timestamps в UTC (TIMESTAMPTZ)

См. также: docs/multi-channel-roadmap.md → "Identity Resolution" для бизнес-смысла полей.
"""

import enum
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    String,
    Text,
    Integer,
    DateTime,
    ForeignKey,
    UniqueConstraint,
    Index,
    Enum as SQLEnum,
    JSON,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.sql import func
from pgvector.sqlalchemy import Vector


EMBEDDING_DIM = 1024  # bge-m3


class Base(DeclarativeBase):
    pass


# ============================================================================
# ENUMS
# ============================================================================
#
# KNOWN QUIRK — Enum storage case (deferred fix, see drawer 2026-05-20):
#   SQLAlchemy with `native_enum=False` stores Enum.NAME (uppercase, e.g.
#   'TELEGRAM') in the underlying VARCHAR column, NOT Enum.value (lowercase
#   'telegram'). This means:
#     - ORM queries work transparently (e.g. ConvRow.channel == 'telegram'
#       still matches because SQLAlchemy maps the enum on read/write).
#     - Raw SQL like `SELECT * FROM conversations WHERE channel='telegram'`
#       returns ZERO rows — must use uppercase: `... WHERE channel='TELEGRAM'`.
#
#   To switch to lowercase storage we'd need: (1) `values_callable=lambda x:
#   [e.value for e in x]` on every SQLEnum constructor, (2) an Alembic data
#   migration `UPDATE conversations SET channel = LOWER(channel)` for all
#   three tables that use these enums. Deferred because mixed-format storage
#   during deploy gaps is worse than the current consistent-uppercase state.


class ChannelEnum(str, enum.Enum):
    """Каналы, через которые лид может связаться."""
    WEBSITE = "website"
    TELEGRAM = "telegram"
    INSTAGRAM = "instagram"
    MESSENGER = "messenger"      # FB Messenger
    WHATSAPP = "whatsapp"        # Phase 2
    EMAIL = "email"              # Phase 2
    TIKTOK = "tiktok"            # Phase 4+
    LINE = "line"                # Phase 4+


class RoleEnum(str, enum.Enum):
    """Кто написал сообщение."""
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    OPERATOR = "operator"        # Когда живой человек ответил вместо бота


class ConversationStatusEnum(str, enum.Enum):
    """Состояние диалога."""
    OPEN = "open"
    HANDED_OFF = "handed_off"    # Передан команде, бот молчит
    RESOLVED = "resolved"        # Диалог закрыт
    ABANDONED = "abandoned"      # Клиент пропал, не реактивируется


# ============================================================================
# CUSTOMER — главный идентификатор лида
# ============================================================================


class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[Optional[str]] = mapped_column(String(320), unique=True, nullable=True, index=True)
    name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    phone: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, index=True)
    first_channel: Mapped[Optional[str]] = mapped_column(SQLEnum(ChannelEnum, native_enum=False), nullable=True)

    # UTM-параметры для отслеживания источника трафика (ad campaigns)
    utm_source: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    utm_campaign: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    utm_medium: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    utm_content: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)

    # Произвольные данные о клиенте (компания, размер, индустрия, ...) — заполняется по ходу диалога
    profile_data: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    # ---- CRM integration + Notion-spec lead axes (Phase 1, 2026-05-26) ----
    # ID of this lead's contact in the external CRM (HubSpot / Bitrix24).
    # NULL until first sync. Adapter writes this on upsert_contact.
    crm_contact_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, index=True)
    # Notion §5 — base(interaction_type) + content(keywords) + source(canal). Decays on silence.
    lead_score: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    # Notion §7 — cold / warm / hot / ready / client / frozen. Dynamic, decays.
    lead_temperature: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="cold", index=True
    )
    # Notion §4 — P1 / P2 / P3 / P4 / P5 / P6 / HardStop. Set once at first touch.
    interaction_type: Mapped[str] = mapped_column(
        String(10), nullable=False, server_default="P2"
    )
    # Notion §3 — extra dedup hooks beyond email/phone (e.g. {tg_handle, ig_username}).
    # Used by CRMAdapter.upsert_contact to merge same person across channels.
    identity_keys: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    identities: Mapped[list["ChannelIdentity"]] = relationship(
        back_populates="customer", cascade="all, delete-orphan"
    )
    conversations: Mapped[list["Conversation"]] = relationship(
        back_populates="customer", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Customer id={self.id} email={self.email}>"


# ============================================================================
# CHANNEL IDENTITY — маппинг внешнего ID → внутренний customer
# ============================================================================


class ChannelIdentity(Base):
    __tablename__ = "channel_identities"
    __table_args__ = (
        UniqueConstraint("channel", "external_id", name="uq_channel_external_id"),
        Index("ix_channel_identity_lookup", "channel", "external_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    customer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("customers.id", ondelete="CASCADE"), nullable=False
    )
    channel: Mapped[str] = mapped_column(SQLEnum(ChannelEnum, native_enum=False), nullable=False)
    external_id: Mapped[str] = mapped_column(String(200), nullable=False)  # tg_user_id, ig_psid, и т.д.
    username: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)  # @ivan_petrov для удобства
    linked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    customer: Mapped["Customer"] = relationship(back_populates="identities")

    def __repr__(self) -> str:
        return f"<ChannelIdentity {self.channel}:{self.external_id} → customer={self.customer_id}>"


# ============================================================================
# CONVERSATION — отдельный диалог (один customer может иметь много диалогов)
# ============================================================================


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    customer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("customers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    channel: Mapped[str] = mapped_column(SQLEnum(ChannelEnum, native_enum=False), nullable=False, index=True)
    # ID диалога на платформе (для IG/FB — thread_id, для Telegram — chat_id, для сайта — session_id)
    channel_conversation_id: Mapped[Optional[str]] = mapped_column(String(200), nullable=True, index=True)

    status: Mapped[str] = mapped_column(
        SQLEnum(ConversationStatusEnum, native_enum=False),
        default=ConversationStatusEnum.OPEN,
        nullable=False,
        index=True,
    )
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # rolling LLM summary
    handoff_done: Mapped[bool] = mapped_column(default=False, nullable=False)

    # Operator takeover (Phase B): when True, the bot stops replying to the
    # lead on its own — every assistant reply must come from a human in the
    # operator forum-supergroup. Toggled via the "👤 Возьму на себя" inline
    # button under each bot reply in the lead's topic.
    operator_takeover: Mapped[bool] = mapped_column(default=False, nullable=False, index=True)
    # Telegram forum topic id (within TELEGRAM_OPERATOR_GROUP_ID supergroup).
    # Lazily created on the first message of each conversation; all subsequent
    # lead messages and bot replies are mirrored there for the team to read
    # and to take over from. NULL = no topic yet (lazy-init not run, group
    # not configured, or topic creation failed).
    forum_topic_id: Mapped[Optional[int]] = mapped_column(
        nullable=True, index=True,
        comment="Telegram forum topic id in the operator supergroup",
    )

    last_message_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True, index=True)

    # ---- CRM integration + Notion §20 funnel (Phase 1, 2026-05-26) ----
    # ID of this conversation's deal in the external CRM. NULL until first sync.
    crm_deal_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, index=True)
    # Notion §20 active funnel stage. Default new_lead so existing rows are valid post-migration.
    # Values: new_lead / in_dialog / qualified / nda / on_call / tz_approved /
    #         proposal / prepayment / in_work / completed_won / post_sale / lost
    lead_stage: Mapped[str] = mapped_column(
        String(30), nullable=False, server_default="new_lead", index=True
    )
    # Required iff lead_stage == 'lost'. Notion §20 split:
    # price / not_our_format / competitor / delayed / no_budget / hard_stop
    lost_reason: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    # When customer.lead_temperature was last recalculated — used by the decay cron.
    last_temperature_update_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Phase 10d (2026-05-27) — when cron last dispatched a warming task for
    # this conversation. NULL = never warmed. plan_warming honours per-bucket
    # cadence (hot=1d, warm=7d, cold=21d, frozen=90d) against this timestamp.
    # Without this column, cron would create a duplicate warming task every
    # hour for every silent lead — fast way to spam the operator inbox.
    last_warmed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    customer: Mapped["Customer"] = relationship(back_populates="conversations")
    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan", order_by="Message.created_at"
    )

    def __repr__(self) -> str:
        return f"<Conversation {self.id} channel={self.channel} status={self.status}>"


# ============================================================================
# MESSAGE — отдельная реплика
# ============================================================================


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role: Mapped[str] = mapped_column(SQLEnum(RoleEnum, native_enum=False), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # Метаданные сообщения: channel_msg_id, attachments, voice_duration_sec, ...
    extra_meta: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    embedding: Mapped[Optional[list[float]]] = mapped_column(Vector(EMBEDDING_DIM), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)

    conversation: Mapped["Conversation"] = relationship(back_populates="messages")

    def __repr__(self) -> str:
        return f"<Message {self.role}: {self.content[:50]}>"


# ============================================================================
# KB CHUNKS — заменяет Chroma vector store
# ============================================================================


class KBChunk(Base):
    __tablename__ = "kb_chunks"
    __table_args__ = (
        Index("ix_kb_chunks_source", "source"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source: Mapped[str] = mapped_column(String(200), nullable=False)  # имя файла KB (e.g. "05_case_vrp.md")
    chunk_index: Mapped[int] = mapped_column(default=0, nullable=False)  # порядковый номер чанка в файле
    content: Mapped[str] = mapped_column(Text, nullable=False)
    extra_meta: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    embedding: Mapped[Optional[list[float]]] = mapped_column(Vector(EMBEDDING_DIM), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    def __repr__(self) -> str:
        return f"<KBChunk source={self.source}#{self.chunk_index}>"


class TrainingCorrection(Base):
    """Operator-supplied corrections that adjust how the bot should respond
    in similar future situations. Populated through the /admin/training UI
    when a human reviews a past conversation, marks a bot reply as wrong,
    and approves a better alternative.

    How it's used at inference time (see services/training.retrieve and
    main._handle_message): embed the lead's current message with bge-m3,
    similarity-search the top-K active corrections, inject them into the
    SYSTEM_PROMPT as a "LESSONS FROM PAST CORRECTIONS" block. Lessons take
    precedence over generic KB style and few-shot examples because they
    come from a real reviewer pointing at a real failure mode.

    Versioning: corrections are never hard-deleted. To replace a rule, set
    `is_active=False` on the old one and link `superseded_by_id` to the
    new one. This keeps an audit trail and avoids race conditions where a
    stale conversation might still depend on the old rule mid-flight.
    """
    __tablename__ = "training_corrections"
    __table_args__ = (
        # Filter active corrections quickly (hot path in similarity_search)
        Index("ix_training_corrections_active", "is_active"),
        # Per-channel tuning: a website lead might need different reply
        # style than a Telegram lead, so a correction can be channel-scoped
        Index("ix_training_corrections_channel", "channel"),
        # HNSW index on embedding is created in the alembic migration
        # (Vector type needs the index_type='hnsw' clause in raw SQL)
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # The situation that triggered the correction — typically the last 3-6
    # messages from the original conversation, formatted as plain text:
    #   user: ...
    #   assistant: ...
    #   user: ...
    trigger_context: Mapped[str] = mapped_column(Text, nullable=False)

    # Human-readable explanation of what should change. Used both for
    # operator review (when listing rules) and for the LLM at inference
    # time as a directive (e.g. "When the lead asks about price, never
    # quote a range; always redirect to the discovery call.")
    correct_guidance: Mapped[str] = mapped_column(Text, nullable=False)

    # Optional concrete sample of the better response. Useful when the
    # guidance is best expressed as "say something like this" rather
    # than as an abstract rule. The model uses it as a few-shot anchor.
    suggested_response: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Optional channel scope. NULL = applies to every channel. Otherwise
    # one of "telegram", "instagram", "messenger", "website", "comment".
    channel: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)

    # bge-m3 embedding of trigger_context (1024 dims). pgvector HNSW index
    # makes top-K cosine-similarity search ~10ms even at 10K+ corrections.
    embedding: Mapped[Optional[list[float]]] = mapped_column(Vector(EMBEDDING_DIM), nullable=True)

    # Provenance — who approved this and when, optional link back to the
    # original conversation that prompted the correction.
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_by: Mapped[str] = mapped_column(String(100), nullable=False, default="admin")
    source_conversation_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Versioning: soft-delete via is_active=False, optional chain via
    # superseded_by_id. Lets us roll back rules cleanly without hard-deleting
    # rows that audit logs reference.
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
    superseded_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("training_corrections.id", ondelete="SET NULL"),
        nullable=True,
    )

    def __repr__(self) -> str:
        return f"<TrainingCorrection {str(self.id)[:8]} channel={self.channel} active={self.is_active}>"
