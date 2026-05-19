"""Quick health check: проверяет, что Postgres + pgvector работают.

Usage:
    python scripts/check_db.py
"""

import sys
from pathlib import Path

# Чтобы можно было импортировать db.* из подпапки scripts
sys.path.insert(0, str(Path(__file__).parent.parent))

from db.connection import check_connection, engine
from sqlalchemy import text


def main():
    print(f"Connecting to: {engine.url.render_as_string(hide_password=True)}")

    if not check_connection():
        print("✗ Connection failed. Проверь DATABASE_URL в .env и что Postgres запущен.")
        sys.exit(1)

    print("✓ Connection OK")
    print("✓ pgvector available")

    # Проверим, что миграции применены — есть ли наши таблицы
    with engine.connect() as conn:
        tables = conn.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' "
                "AND table_name IN ('customers', 'channel_identities', 'conversations', 'messages', 'kb_chunks') "
                "ORDER BY table_name;"
            )
        ).fetchall()

        existing = [row[0] for row in tables]
        expected = {"channel_identities", "conversations", "customers", "kb_chunks", "messages"}

        print("\nTables found:")
        for t in existing:
            print(f"  ✓ {t}")

        missing = expected - set(existing)
        if missing:
            print(f"\n✗ Missing tables: {missing}")
            print("Run: alembic upgrade head")
            sys.exit(1)
        else:
            print("\n✓ All tables present. Schema ready.")


if __name__ == "__main__":
    main()
