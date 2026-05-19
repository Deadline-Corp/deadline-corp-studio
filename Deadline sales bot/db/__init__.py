"""Database layer for Deadline Sales Bot.

Содержит:
- connection: engine + session factory
- models: SQLAlchemy ORM модели (Customer, Conversation, Message, ...)
- vector: обёртка над pgvector для семантического поиска
"""

from .connection import engine, SessionLocal, get_db
from .models import (
    Base,
    Customer,
    ChannelIdentity,
    Conversation,
    Message,
    KBChunk,
    ChannelEnum,
    RoleEnum,
    ConversationStatusEnum,
)

__all__ = [
    "engine",
    "SessionLocal",
    "get_db",
    "Base",
    "Customer",
    "ChannelIdentity",
    "Conversation",
    "Message",
    "KBChunk",
    "ChannelEnum",
    "RoleEnum",
    "ConversationStatusEnum",
]
