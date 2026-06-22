"""Prompt store — DB-backed override системного промпта («мозг» бота).

Источник правды: таблица prompt_versions (alembic 010). Активная версия
(kind='system_prompt', is_active=True) подменяет константу prompts.SYSTEM_PROMPT
при сборке chat-промпта. Нет активной версии → None → caller использует
константу (фоллбэк = поведение 1:1 как до этой фичи).

Hot-reload без рестарта: module-level кэш с TTL 60 секунд. Бот подхватывает
новую версию максимум через минуту после сохранения в Admin UI; немедленная
инвалидация в set_active/activate покрывает тот же процесс (Railway = один
инстанс, так что фактически мгновенно).

Safety: сломанный .format() в SYSTEM_PROMPT = упавший _handle_message на
КАЖДОМ сообщении = молчащий бот. Поэтому validate_prompt_template() проверяет
наличие всех обязательных плейсхолдеров и отсутствие лишних/опечатанных —
admin_api возвращает 422 ДО сохранения. Плюс dry-run через .format() с
заглушками — ловит кривые одиночные '{'.
"""

from __future__ import annotations

import logging
import re
import time
import threading
from typing import Optional

log = logging.getLogger(__name__)

# Плейсхолдеры, которые build_chat_prompt подставляет через .format().
# Отсутствие любого из них = format() упадёт KeyError не упадёт, но блок
# просто исчезнет из промпта (молча сломанный бот); лишний неизвестный
# {placeholder} = KeyError на каждом сообщении. Проверяем оба направления.
REQUIRED_PLACEHOLDERS = ("{context}", "{history}", "{question}", "{corrections}", "{handoff_block}")
_KNOWN_NAMES = {"context", "history", "question", "corrections", "handoff_block"}

_CACHE_TTL_SEC = 60.0
_cache_lock = threading.Lock()
_cached_content: Optional[str] = None
_cached_at: float = 0.0
_cache_valid: bool = False


def validate_prompt_template(content: str) -> list[str]:
    """Вернуть список проблем (пусто = валидно). Не бросает исключений."""
    problems: list[str] = []
    if not content or not content.strip():
        return ["Промпт пустой."]
    for ph in REQUIRED_PLACEHOLDERS:
        if ph not in content:
            problems.append(f"Отсутствует обязательный плейсхолдер {ph}")
    # Неизвестные {имена} — KeyError на каждом сообщении.
    for name in set(re.findall(r"(?<!\{)\{([a-zA-Z_][a-zA-Z0-9_]*)\}(?!\})", content)):
        if name not in _KNOWN_NAMES:
            problems.append(
                f"Неизвестный плейсхолдер {{{name}}} — format() упадёт. "
                f"Допустимые: {sorted(_KNOWN_NAMES)}. Литеральные скобки пишите как {{{{...}}}}."
            )
    # Dry-run: ловит непарные '{' и прочие ValueError у str.format.
    if not problems:
        try:
            content.format(
                context="-", history="-", question="-", corrections="-", handoff_block="-",
            )
        except (KeyError, ValueError, IndexError) as e:
            problems.append(f"format() падает: {type(e).__name__}: {e}")
    return problems


def _load_active_from_db() -> Optional[str]:
    from db.connection import session_scope
    from db.models import PromptVersion
    with session_scope() as s:
        row = (
            s.query(PromptVersion)
            .filter(PromptVersion.kind == "system_prompt", PromptVersion.is_active == True)  # noqa: E712
            .order_by(PromptVersion.created_at.desc())
            .first()
        )
        return row.content if row else None


def get_active_system_prompt() -> Optional[str]:
    """Активный DB-промпт или None (→ caller использует константу).

    TTL-кэш: одна SELECT-ходка в минуту, не на каждое сообщение. Ошибка БД
    → возвращаем прошлое закэшированное значение (или None) — бот не должен
    замолчать из-за моргнувшего Postgres.
    """
    global _cached_content, _cached_at, _cache_valid
    now = time.monotonic()
    with _cache_lock:
        if _cache_valid and (now - _cached_at) < _CACHE_TTL_SEC:
            return _cached_content
    try:
        content = _load_active_from_db()
    except Exception as e:  # noqa: BLE001 — деградируем на кэш/константу
        log.warning(f"prompt_store: DB read failed, falling back: {e}")
        with _cache_lock:
            return _cached_content if _cache_valid else None
    with _cache_lock:
        _cached_content = content
        _cached_at = now
        _cache_valid = True
    return content


def invalidate_cache() -> None:
    global _cache_valid
    with _cache_lock:
        _cache_valid = False


def set_active_system_prompt(content: str, created_by: str = "admin-ui",
                             comment: Optional[str] = None) -> str:
    """Сохранить новую версию и сделать её активной. Возвращает id строки.

    Валидация — обязанность вызывающего (admin_api зовёт
    validate_prompt_template и отдаёт 422 до этого вызова); здесь — последний
    рубеж: ValueError при невалидном шаблоне.
    """
    problems = validate_prompt_template(content)
    if problems:
        raise ValueError("; ".join(problems))
    from db.connection import session_scope
    from db.models import PromptVersion
    with session_scope() as s:
        s.query(PromptVersion).filter(
            PromptVersion.kind == "system_prompt",
            PromptVersion.is_active == True,  # noqa: E712
        ).update({"is_active": False})
        row = PromptVersion(
            kind="system_prompt", content=content, is_active=True,
            created_by=created_by, comment=comment,
        )
        s.add(row)
        s.flush()
        new_id = str(row.id)
    invalidate_cache()
    log.info(f"prompt_store: new active system_prompt version {new_id[:8]} by {created_by}")
    return new_id


def activate_version(version_id: str) -> bool:
    """Откат: сделать активной существующую версию. False если id не найден."""
    from uuid import UUID
    from db.connection import session_scope
    from db.models import PromptVersion
    with session_scope() as s:
        row = s.get(PromptVersion, UUID(version_id))
        if row is None or row.kind != "system_prompt":
            return False
        s.query(PromptVersion).filter(
            PromptVersion.kind == "system_prompt",
            PromptVersion.is_active == True,  # noqa: E712
        ).update({"is_active": False})
        row.is_active = True
    invalidate_cache()
    log.info(f"prompt_store: activated version {version_id[:8]}")
    return True


def deactivate_all() -> None:
    """«Вернуться на заводской» — деактивировать все DB-версии (бот снова
    использует константу prompts.SYSTEM_PROMPT)."""
    from db.connection import session_scope
    from db.models import PromptVersion
    with session_scope() as s:
        s.query(PromptVersion).filter(
            PromptVersion.kind == "system_prompt",
            PromptVersion.is_active == True,  # noqa: E712
        ).update({"is_active": False})
    invalidate_cache()
    log.info("prompt_store: all versions deactivated (fallback to constant)")
