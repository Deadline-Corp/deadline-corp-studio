"""Бэкап базы — портативный логический дамп ключевых таблиц в gzip(JSON).

Без pg_dump и без новых зависимостей (только SQLAlchemy core). Хранит ВСЕ
переписки, статусы, стадии, задачи, правила — на случай потери системы/номера.
Эмбеддинги (большие векторы) исключаются — в бэкапе диалогов не нужны.

Использование:
  fname, blob = build_export()      # (имя файла, байты gzip)
Вызывать в потоке (asyncio.to_thread) — это синхронный обход БД."""

from __future__ import annotations

import gzip
import io
import json
import logging
from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import UUID

from sqlalchemy import text

from db.connection import engine

log = logging.getLogger(__name__)

# Ключевые таблицы (порядок не важен). Несуществующие тихо пропускаются.
_TABLES = [
    "customers", "conversations", "messages", "scheduled_actions",
    "stage_transitions", "bot_settings", "training_corrections",
    "workspace_members", "crm_events", "automations", "automation_runs",
    "processed_updates", "kb_chunks",
]
# Колонки-эмбеддинги (большие векторы) — не включаем в бэкап.
_SKIP_COLS = {"embedding"}


def _default(o):
    if isinstance(o, (datetime, date)):
        return o.isoformat()
    if isinstance(o, UUID):
        return str(o)
    if isinstance(o, Decimal):
        return float(o)
    if isinstance(o, (bytes, bytearray, memoryview)):
        return None
    return str(o)


def build_export() -> tuple[str, bytes]:
    """Собрать дамп ключевых таблиц → (filename, gzip-bytes)."""
    payload: dict = {
        "_meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "format": "deadline-logical-backup-v1",
            "tables": [],
        },
        "tables": {},
    }
    with engine.connect() as conn:
        for t in _TABLES:
            try:
                rows = conn.execute(text(f'SELECT * FROM "{t}"')).mappings().all()
            except Exception:  # noqa: BLE001 — таблицы может не быть
                continue
            out = [{k: v for k, v in dict(r).items() if k not in _SKIP_COLS} for r in rows]
            payload["tables"][t] = out
            payload["_meta"]["tables"].append({"name": t, "rows": len(out)})

    raw = json.dumps(payload, default=_default, ensure_ascii=False).encode("utf-8")
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", compresslevel=6) as g:
        g.write(raw)
    blob = buf.getvalue()
    fname = f"deadline-backup-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M')}.json.gz"
    log.info("[db_backup] export %s — %d таблиц, %d КБ",
             fname, len(payload["_meta"]["tables"]), len(blob) // 1024)
    return fname, blob
