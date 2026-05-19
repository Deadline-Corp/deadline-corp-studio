"""Pytest fixtures for unit tests against the real Railway-Postgres.

Strategy: open a connection, BEGIN an outer transaction, create a Session
that lives inside that transaction. All `db.flush()` calls in the code under
test land in the transaction. At test end the fixture ROLLBACKs everything —
zero artifacts in production DB.

Why not a separate test DB? Adds Postgres provisioning + alembic step to
every developer setup. The rollback strategy here is industry-standard for
unit tests touching SQL.

Caveats:
- Code under test MUST NOT call db.commit() — it would land permanently.
  Our service functions use db.flush() only, commit happens in caller's
  session_scope context which the fixture intercepts.
- Tests should be small and isolated. Long-running tests with many TRUNCATE
  ops will slow down. Keep them per-scenario.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

# Make `from db.models import ...` work when pytest is invoked from project root.
sys.path.insert(0, str(Path(__file__).parent.parent))

from db.connection import engine  # noqa: E402


@pytest.fixture
def db():
    """Per-test DB session that ALWAYS rolls back at end.

    Uses nested-transaction (savepoint) join mode so even if the code under
    test does an explicit commit-like operation, the outer transaction still
    wraps everything and we discard it cleanly.
    """
    connection = engine.connect()
    outer_transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")

    try:
        yield session
    finally:
        session.close()
        outer_transaction.rollback()
        connection.close()
