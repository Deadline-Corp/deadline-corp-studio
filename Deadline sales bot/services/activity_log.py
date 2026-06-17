"""Журнал активности системы — запись событий для админ-панели (что/как/почему/кто).

Вспомогательный модуль: НИКОГДА не роняет бизнес-логику — все сбои записи глушим.
Пишется из ключевых точек (смена конфигурации, ошибки, решения бота, действия
оператора, массовые отправки, каналы). Читается эндпоинтом /admin/api/logs.
"""
import logging
import uuid as _uuid
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Известные категории — для фильтра в UI (свободная строка тоже допустима).
CATEGORIES = ("config", "channel", "stage", "bot", "send", "task", "auth", "error", "lead", "system")
LEVELS = ("info", "warn", "error")


def _uid(v: Any) -> Optional[_uuid.UUID]:
    if v is None:
        return None
    if isinstance(v, _uuid.UUID):
        return v
    try:
        return _uuid.UUID(str(v))
    except (ValueError, AttributeError, TypeError):
        return None


def log_event(category: str, summary: str, *, level: str = "info", actor: str = "system",
              conversation_id: Any = None, customer_id: Any = None,
              meta: Optional[dict] = None) -> None:
    """Записать событие в журнал активности. Короткая своя сессия. Тихо глотает любые
    сбои — журнал не должен влиять на основной поток.

    category — config|channel|stage|bot|send|task|auth|error|lead|system
    level    — info|warn|error
    actor    — bot | admin:<имя> | operator | automation | system | lead
    """
    try:
        from db.connection import session_scope
        from db.models import ActivityLog
        with session_scope() as s:
            s.add(ActivityLog(
                level=(level or "info")[:10],
                category=(category or "system")[:24],
                actor=(actor or "system")[:60],
                summary=(summary or "")[:400],
                conversation_id=_uid(conversation_id),
                customer_id=_uid(customer_id),
                meta=meta if isinstance(meta, dict) else None,
            ))
    except Exception as exc:  # noqa: BLE001
        logger.debug("[activity_log] write failed (non-fatal): %s", exc)


def prune(days: int = 30) -> int:
    """Удалить записи старше N дней (журнал не растёт без предела). Возвращает кол-во."""
    try:
        from db.connection import session_scope
        from sqlalchemy import text as _t
        with session_scope() as s:
            r = s.execute(
                _t("DELETE FROM activity_log WHERE created_at < now() - make_interval(days => :d)"),
                {"d": int(days)},
            )
            return r.rowcount or 0
    except Exception as exc:  # noqa: BLE001
        logger.warning("[activity_log] prune failed: %s", exc)
        return 0
