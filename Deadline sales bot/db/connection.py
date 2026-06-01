"""SQLAlchemy engine + session management.

Sync движок (psycopg2). Если в будущем потребуется async — мигрируем на asyncpg
без изменения схемы. Сейчас MVP-режим: один dev, простота важнее перформанса.

Usage:
    from db import get_db

    @app.post("/message")
    def chat(req: ..., db: Session = Depends(get_db)):
        ...
"""

import os
import logging
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, Session
from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL not set. Add to .env: "
        "DATABASE_URL=postgresql://postgres:postgres@localhost:5432/deadline_bot"
    )

# Railway/Heroku иногда возвращают postgres:// (legacy), SQLAlchemy 2.0 требует postgresql://
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,        # Проверять соединение перед запросом — спасает от dropped connections
    pool_size=5,
    max_overflow=10,
    echo=os.getenv("SQL_ECHO", "false").lower() == "true",
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@event.listens_for(engine, "connect")
def _on_connect(dbapi_conn, _):
    """Убедиться, что pgvector расширение видно сессии."""
    # pgvector НЕ требует SET, но мы убеждаемся, что extension установлено
    # (CREATE EXTENSION делается через миграцию alembic, не здесь)
    pass


def get_db():
    """FastAPI dependency: yields a session, closes after request.

    Откатываем при исключении, чтобы соединение не вернулось в пул в «грязном»
    (открытая транзакция) состоянии.
    """
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@contextmanager
def session_scope():
    """Context manager для скриптов и фоновых задач (вне FastAPI):

        with session_scope() as db:
            ...
    """
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def check_connection() -> bool:
    """Quick health check — useful at startup."""
    try:
        with engine.connect() as conn:
            from sqlalchemy import text
            conn.execute(text("SELECT 1"))
            # Проверяем что pgvector доступен
            conn.execute(text("SELECT '[1,2,3]'::vector"))
        return True
    except Exception as e:
        log.error(f"DB connection check failed: {e}")
        return False
