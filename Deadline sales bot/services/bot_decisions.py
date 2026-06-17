"""Журнал РЕШЕНИЙ БОТА — отдельный от системного журнала (activity_log).

Каждое автономное решение бота (стадия/созвон/дожим/хэндофф/классификация/recall/
поля/win-back) → одна запись с ПРИЧИНОЙ простым языком. Цель: владелец видит логику
бота (доверие + точечный тюнинг). Читается эндпоинтом /admin/api/bot-decisions.

Вспомогательный модуль: НИКОГДА не роняет основной поток — все сбои записи глушим.
Своя короткая сессия (как activity_log) — не отравляет транзакцию вызывающего и не
держится во время LLM. Пишется ПОСЛЕ решения (не во время LLM-вызова).
"""
import logging
import uuid as _uuid
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Машинные типы решений — для фильтра/иконок в UI (свободная строка тоже допустима).
DECISION_TYPES = (
    "stage_change", "call_suggested", "call_booked", "call_rescheduled",
    "call_cancelled", "nudge_sent", "winback_task", "handoff", "classification",
    "recall_greeting", "field_filled", "alt_channel", "silence_lost", "reply_sent",
)


def _uid(v: Any) -> Optional[_uuid.UUID]:
    if v is None:
        return None
    if isinstance(v, _uuid.UUID):
        return v
    try:
        return _uuid.UUID(str(v))
    except (ValueError, AttributeError, TypeError):
        return None


def log_decision(decision_type: str, reason: str, *, conversation_id: Any = None,
                 customer_id: Any = None, detail: Optional[dict] = None,
                 actor: str = "bot", db: Any = None) -> None:
    """Записать решение бота в журнал. Тихо глотает любые сбои — журнал не должен
    влиять на основной поток.

    db — сессия вызывающего. ЕСЛИ ПЕРЕДАНА — переиспользуем её (НЕ открываем второй
    коннект из пула: критично в cron-цикле, где внешняя сессия держится весь проход —
    иначе риск исчерпания пула, инцидент-вис 06-02). Пишем через SAVEPOINT
    (begin_nested), чтобы сбой записи журнала НЕ отравил основную транзакцию; коммит
    берёт вызывающий. Если db=None — своя короткая сессия (контексты без сессии).

    decision_type — stage_change|call_suggested|call_booked|call_rescheduled|
                    call_cancelled|nudge_sent|winback_task|handoff|classification|
                    recall_greeting|field_filled|alt_channel|silence_lost|reply_sent
    reason        — человекочитаемая причина простым языком (≤600 симв.)
    detail        — структурный контекст (from/to стадии, время созвона, шаг и т.п.)
    actor         — bot | automation
    """
    try:
        from db.models import BotDecision
        row = BotDecision(
            decision_type=(decision_type or "")[:32],
            reason=(reason or "")[:600],
            conversation_id=_uid(conversation_id),
            customer_id=_uid(customer_id),
            detail=detail if isinstance(detail, dict) else None,
            actor=(actor or "bot")[:24],
        )
        if db is not None:
            # Переиспользуем сессию вызывающего через SAVEPOINT — один коннект, и сбой
            # записи журнала не отравит основную транзакцию. Коммит берёт вызывающий.
            try:
                with db.begin_nested():
                    db.add(row)
                    db.flush()
            except Exception as exc:  # noqa: BLE001
                logger.warning("[bot_decisions] write (shared session) failed: %s", exc)
        else:
            from db.connection import session_scope
            with session_scope() as s:
                s.add(row)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[bot_decisions] write failed (non-fatal): %s", exc)


def recent(conversation_id: Any = None, *, limit: int = 50,
           before: Optional[str] = None) -> list[dict]:
    """Прочитать последние решения (для эндпоинта). conversation_id=None → глобальная
    лента; иначе — по одному лиду. before (ISO) — keyset-пагинация назад по времени."""
    out: list[dict] = []
    try:
        from db.connection import session_scope
        from db.models import BotDecision
        from sqlalchemy import select, desc
        with session_scope() as s:
            q = select(BotDecision)
            cid = _uid(conversation_id)
            if cid is not None:
                q = q.where(BotDecision.conversation_id == cid)
            if before:
                from datetime import datetime as _dt
                try:
                    q = q.where(BotDecision.created_at < _dt.fromisoformat(before))
                except (ValueError, TypeError):
                    pass
            q = q.order_by(desc(BotDecision.created_at)).limit(max(1, min(int(limit), 500)))
            for r in s.execute(q).scalars().all():
                out.append({
                    "id": str(r.id),
                    "at": r.created_at.isoformat() if r.created_at else None,
                    "decision_type": r.decision_type,
                    "reason": r.reason,
                    "detail": r.detail,
                    "actor": r.actor,
                    "conversation_id": str(r.conversation_id) if r.conversation_id else None,
                })
    except Exception as exc:  # noqa: BLE001
        logger.warning("[bot_decisions] read failed: %s", exc)
    return out


def prune(days: int = 60) -> int:
    """Удалить записи старше N дней (журнал не растёт без предела). Возвращает кол-во."""
    try:
        from db.connection import session_scope
        from sqlalchemy import text as _t
        with session_scope() as s:
            r = s.execute(
                _t("DELETE FROM bot_decisions WHERE created_at < now() - make_interval(days => :d)"),
                {"d": int(days)},
            )
            return r.rowcount or 0
    except Exception as exc:  # noqa: BLE001
        logger.warning("[bot_decisions] prune failed: %s", exc)
        return 0
