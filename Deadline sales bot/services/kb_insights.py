"""Аналитика «мозга»: что бот реально ЦИТИРУЕТ из базы знаний и вопросы, на
которые в базе НЕТ ответа.

Логирование (log_retrieval) вызывается из ПУТИ ОТВЕТА (main.py /chat и
services.wa_drafts) сразу после RAG-извлечения. Всё best-effort: своя короткая
сессия, любая ошибка проглатывается — аналитика НИКОГДА не должна ронять ответ
лиду. Хранилище — таблица kb_usage_stat (kind: 'cite' | 'gap').

Читается на странице «Мозг» (admin_api: /kb/usage, /kb/gaps).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger(__name__)

# Косинусная дистанция bge-m3 (lower = ближе): хороший матч ~0.3–0.5, слабый
# > ~0.62. Если ЛУЧШИЙ матч хуже порога (или матчей нет вовсе) — в базе нет
# хорошего ответа на вопрос → это «пробел».
GAP_DISTANCE = 0.62
_MIN_GAP_LEN = 15   # не логируем пробелы для «ок»/«привет» и прочей мелочи
_MAX_KEY = 200      # лимит длины ключа (= ширина колонки)


def log_retrieval(docs: Any, query: str) -> None:
    """Записать, какие источники бот процитировал, и пробел (если матч слабый).

    docs — список langchain Document с metadata {source, distance}.
    Полностью best-effort: при любой ошибке тихо выходим (debug-лог)."""
    try:
        q = (query or "").strip()
        sources: list[str] = []
        best: float | None = None
        for d in (docs or []):
            md = getattr(d, "metadata", {}) or {}
            s = md.get("source")
            if s:
                sources.append(str(s))
            dist = md.get("distance")
            if isinstance(dist, (int, float)) and not isinstance(dist, bool):
                best = float(dist) if best is None else min(best, float(dist))
        is_gap = (not sources) or (best is not None and best > GAP_DISTANCE)
        gap_key = q[:_MAX_KEY] if (is_gap and len(q) >= _MIN_GAP_LEN) else None
        if not sources and not gap_key:
            return

        from db.connection import session_scope
        now = datetime.now(timezone.utc)
        with session_scope() as db:
            for src in set(sources):       # один инкремент на уникальный источник в выдаче
                _bump(db, "cite", src[:_MAX_KEY], now)
            if gap_key:
                _bump(db, "gap", gap_key, now)
    except Exception as e:  # noqa: BLE001 — аналитика не должна ронять ответ
        log.debug("kb_insights.log_retrieval skipped: %s", e)


def _bump(db, kind: str, key: str, now: datetime) -> None:
    from db.models import KBUsageStat
    from sqlalchemy import select
    row = db.execute(
        select(KBUsageStat).where(KBUsageStat.kind == kind, KBUsageStat.key == key)
    ).scalar_one_or_none()
    if row is None:
        db.add(KBUsageStat(kind=kind, key=key, count=1, last_at=now))
    else:
        row.count = (row.count or 0) + 1
        row.last_at = now


def get_usage(limit: int = 40) -> dict:
    """Цитируемость источников + «мёртвые» источники (есть в KB, 0 цитат)."""
    from db.connection import session_scope
    from db.models import KBUsageStat, KBChunk
    from sqlalchemy import select
    try:
        with session_scope() as db:
            cites = db.execute(
                select(KBUsageStat.key, KBUsageStat.count)
                .where(KBUsageStat.kind == "cite")
                .order_by(KBUsageStat.count.desc())
            ).all()
            used = {r.key: int(r.count or 0) for r in cites}
            all_src = sorted({r[0] for r in db.execute(select(KBChunk.source).distinct()).all()})
        dead = [s for s in all_src if s not in used]
        items = [{"source": k, "count": c} for k, c in list(used.items())[:limit]]
        return {
            "items": items,
            "total_cites": sum(used.values()),
            "sources_total": len(all_src),
            "sources_used": len([s for s in all_src if s in used]),
            "dead": dead,
        }
    except Exception as e:  # noqa: BLE001
        log.debug("kb_insights.get_usage failed: %s", e)
        return {"items": [], "total_cites": 0, "sources_total": 0, "sources_used": 0, "dead": []}


def get_gaps(limit: int = 25) -> dict:
    """Вопросы лидов, на которые в базе не нашлось хорошего ответа (по убыванию частоты)."""
    from db.connection import session_scope
    from db.models import KBUsageStat
    from sqlalchemy import select
    try:
        with session_scope() as db:
            rows = db.execute(
                select(KBUsageStat.key, KBUsageStat.count, KBUsageStat.last_at)
                .where(KBUsageStat.kind == "gap")
                .order_by(KBUsageStat.count.desc(), KBUsageStat.last_at.desc())
                .limit(limit)
            ).all()
        return {"items": [
            {"q": r.key, "count": int(r.count or 0),
             "last_at": r.last_at.isoformat() if r.last_at else None}
            for r in rows
        ]}
    except Exception as e:  # noqa: BLE001
        log.debug("kb_insights.get_gaps failed: %s", e)
        return {"items": []}
