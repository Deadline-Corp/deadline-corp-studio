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
    Boolean,
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
    ARCHIVED = "archived"        # Сидлайн в пользу нового диалога с тем же клиентом (Phase 13)


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

    # ---- Phase 13: returning lead memory ----
    # When this conversation was sidelined (i.e. user started a new project on
    # this customer). NULL = still in normal lifecycle. Set together with
    # status = ARCHIVED when topic_classifier returns NEW.
    archived_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    # If this conversation was spawned from an earlier one (Phase 13 NEW
    # branch), points back. NULL for top-level conversations.
    parent_conversation_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="SET NULL"),
        nullable=True, index=True,
    )
    # ORM relationship — lets Phase 13 services traverse via `conv.parent`
    # instead of an explicit session.get(). `remote_side` tells SQLAlchemy
    # that the FK target is the same table's id (self-referential).
    parent: Mapped[Optional["Conversation"]] = relationship(
        "Conversation",
        remote_side="Conversation.id",
        foreign_keys=[parent_conversation_id],
        post_update=True,
    )

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


# ============================================================================
# SCHEDULED ACTIONS — движок «бот сам отрабатывает задачи» (Task Engine, Фаза B)
# ============================================================================
#
# Своя очередь отложенных действий в Postgres — источник правды для крона.
# Бот по ней работает (шлёт сообщение лиду в срок), а в HubSpot держит
# зеркальную задачу (crm_task_id) для видимости менеджеру.
#
#   executor='bot'   → крон выполняет сам (followup_message → send в Telegram),
#                      потом status='done' + закрывает зеркальную HubSpot-задачу.
#   executor='human' → выполняет менеджер в CRM; бот только эскалирует/напоминает.

class ScheduledAction(Base):
    __tablename__ = "scheduled_actions"
    __table_args__ = (
        # Горячий запрос крона: выбрать созревшие pending bot-действия.
        Index("ix_scheduled_actions_due", "status", "executor", "due_at"),
        Index("ix_scheduled_actions_conv", "conversation_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    customer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("customers.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    conversation_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Канал и chat_id (для проактивной отправки в мессенджер).
    channel: Mapped[str] = mapped_column(
        SQLEnum(ChannelEnum, native_enum=False), nullable=False
    )
    chat_id: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)

    # followup_message | warming_touch | operator_callback | escalation
    action_type: Mapped[str] = mapped_column(String(32), nullable=False)
    # bot | human
    executor: Mapped[str] = mapped_column(String(16), nullable=False, server_default="bot")

    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    # pending | done | cancelled | failed
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="pending")

    # Черновик/контекст для исполнения (текст сообщения, причина и т.д.).
    payload: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    # id зеркальной HubSpot-задачи (чтобы закрыть её при исполнении).
    crm_task_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    executed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    # Когда строку «забрал» крон (status→processing). Защита от двойной отправки
    # при конкурентных свипах; протухший claim (>15 мин) можно перезабрать.
    claimed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:
        return (
            f"<ScheduledAction {str(self.id)[:8]} {self.action_type} "
            f"executor={self.executor} status={self.status} due={self.due_at}>"
        )


class CRMEvent(Base):
    """Durable-запись события CRM-очереди (для recovery после рестарта).

    Воркер пишет строку (status=pending) когда берёт событие, помечает done после
    успешной отправки в CRM или failed после исчерпания ретраев. На старте
    pending-строки переигрываются (services.crm_queue.recover_pending_events).
    payload — JSON-safe снимок (без closures-колбэков; dataclasses/datetime
    закодированы, см. crm_queue._serialize_payload)."""
    __tablename__ = "crm_events"
    __table_args__ = (
        Index("ix_crm_events_status_created", "status", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    # Без FK намеренно — reset/удаление клиента не должно ломать recovery.
    customer_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    conversation_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    payload: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    processed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    def __repr__(self) -> str:
        return f"<CRMEvent {str(self.id)[:8]} {self.event_type} status={self.status}>"


class PromptVersion(Base):
    """Редактируемые версии системного промпта («мозг» бота) — Admin UI.

    Боевой промпт исторически — константа prompts.SYSTEM_PROMPT. Эта таблица
    позволяет менять его БЕЗ деплоя: build_chat_prompt берёт активную строку
    отсюда (через services.prompt_store, TTL-кэш 60с), а если активных строк
    нет — падает обратно на константу. Версионирование как у
    training_corrections: новая строка is_active=True, прежняя деактивируется;
    откат = активировать любую старую версию. Никогда не hard-delete.
    """
    __tablename__ = "prompt_versions"
    __table_args__ = (
        Index("ix_prompt_versions_active", "kind", "is_active"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # Пока только 'system_prompt'; задел под greeting/handoff-шаблоны.
    kind: Mapped[str] = mapped_column(String(32), nullable=False, server_default="system_prompt")
    content: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(default=False, nullable=False)
    comment: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    created_by: Mapped[str] = mapped_column(String(100), nullable=False, server_default="admin-ui")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    def __repr__(self) -> str:
        return f"<PromptVersion {str(self.id)[:8]} kind={self.kind} active={self.is_active}>"


class ProcessedUpdate(Base):
    """Дедуп входящих апдейтов вебхуков, переживающий рестарт процесса.

    event_key = '<channel>:<update_id>' (напр. 'telegram:12345'). Вставка
    ON CONFLICT DO NOTHING: если ключ уже есть — апдейт уже обработан, пропускаем.
    Раньше дедуп был in-memory (OrderedDict) → после деплоя Railway Telegram
    ретраил последние апдейты, и новый процесс обрабатывал их повторно (дубли).
    """
    __tablename__ = "processed_updates"

    event_key: Mapped[str] = mapped_column(String(120), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<ProcessedUpdate {self.event_key}>"


class LeadSubmission(Base):
    """Каждая отправка лид-формы (deadlinecorp.com/lead-form/) — сырой
    исторический след.

    Раньше POST /lead-submit был fire-and-forget (только сообщение в Telegram):
    опечатка в поле «контакт» = безвозвратно потерянный лид (мы напоролись на
    «@saswee21», которого не существует — восстановить было нечем). Теперь каждая
    заявка пишется сюда: поля формы + вердикт проверки контакта + ip/ua/referer.

    contact_exists: True=проверен и существует, False=проверен и НЕ существует
    (подозрение на опечатку), NULL=не проверяли (телефон/email) или не смогли.
    Без FK на customers намеренно (как crm_events) — reset/удаление клиента не
    должен ломать историю заявок.
    """
    __tablename__ = "lead_submissions"
    __table_args__ = (
        Index("ix_lead_submissions_created", "created_at"),
        Index("ix_lead_submissions_customer", "customer_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    contact: Mapped[str] = mapped_column(String(200), nullable=False)
    contact_type: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    contact_exists: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)

    need: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    business: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    task: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # 'when' is a SQL reserved word — store under 'timeframe'.
    timeframe: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    source: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    campaign: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    lang: Mapped[Optional[str]] = mapped_column(String(5), nullable=True)

    # Контекст запроса — отпечаток лида даже при кривом контакте.
    ip: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    referer: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Customer, созданный для CRM-апсёрта (если crm_enabled). Без FK.
    customer_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    telegram_delivered: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    crm_enqueued: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return (
            f"<LeadSubmission {str(self.id)[:8]} {self.name!r} "
            f"type={self.contact_type} exists={self.contact_exists}>"
        )
