"""Bot settings — поведенческие оверрайды tenant config из Admin UI.

Key-value (bot_settings, JSONB {"v": ...}) с TTL-кэшем 60с — крон и логика
читают без деплоя/рестарта. Пустая таблица = дефолты config.yaml (1:1).

Известные ключи (whitelist — чтобы UI не мог записать мусор, который потом
молча игнорируется):
  nudge_enabled        bool   — пинговать ли молчуна-вовлечённого (cron bot-nudge)
  nudge_after_hours    float  — через сколько часов тишины пинговать (деф. 1)
  nudge_max_hours      float  — после скольких часов уже не пинговать (деф. 36)
  nudge_text           str    — текст пинка (деф. зашит в cron.py)
  silence_lost_days    int    — дней тишины до авто-lost(delayed) (деф. 7)
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Optional

log = logging.getLogger(__name__)

KNOWN_KEYS: dict[str, type] = {
    "nudge_enabled": bool,
    "nudge_after_hours": float,
    "nudge_max_hours": float,
    "nudge_text": str,
    "silence_lost_days": int,
    # Онбординг/брендинг рабочего пространства (Admin UI, 2026-06-12)
    "onboarding_done": bool,
    "business_name": str,
    "niche_key": str,
    "logo_url": str,
    "accent_color": str,
    # Цель бота: call (созвон, дефолт) | collect_lead | consult | sale —
    # overlay-блок в системный промпт (prompts.GOAL_OVERLAYS)
    "bot_goal": str,
    # Утренний AI-дайджест владельцу в Telegram
    "digest_enabled": bool,
    "digest_hour": int,        # час отправки по локальному времени (см. offset)
    "digest_tz_offset": int,   # смещение от UTC, дефолт +7 (Бангкок)
    "digest_last_date": str,   # служебное: дата последней отправки YYYY-MM-DD
    # P2 — планировщик: сколько заявок (бригад) может идти ПАРАЛЛЕЛЬНО в один слот.
    # 1 (дефолт) = прежнее поведение «один в час». Напр. 3 = три клининга одновременно.
    "sched_capacity_per_slot": int,
    # P2b — рабочее окно (локальное время тенанта). Дефолты = Пн–Пт 11:00–19:00.
    "sched_work_start": int,   # час первого слота (напр. 9)
    "sched_work_end": int,     # час последнего старта (напр. 20)
    "sched_work_days": str,    # дни недели через запятую, Пн=0…Вс=6 (напр. "0,1,2,3,4,5,6" — без выходных)
    # P3 — эскалация: Telegram chat_id руководителя/главы отдела для сигналов о
    # крупных/сложных лидах («хочу человека», большой бюджет, юр.вопрос). Пусто =
    # только операторская группа (прежнее поведение).
    "manager_chat_id": str,
    # P3 — Telegram chat_id бригады/отдела для уведомлений о новых визитах
    # (адрес + время + ссылка на карту). Пусто = не уведомляем.
    "crew_chat_id": str,
    # P5 — поддерживаемые языки через запятую (напр. "ru,en,th,my"). Пусто =
    # языки из config.yaml. Первый — основной (язык recall-приветствия). Живой
    # диалог LLM отвечает на языке клиента независимо от списка.
    "languages": str,
}

_TTL = 60.0
_lock = threading.Lock()
_cache: Optional[dict[str, Any]] = None
_cached_at = 0.0


def _load() -> dict[str, Any]:
    from db.connection import session_scope
    from db.models import BotSetting
    out: dict[str, Any] = {}
    with session_scope() as s:
        for row in s.query(BotSetting).all():
            if row.key in KNOWN_KEYS and isinstance(row.value, dict) and "v" in row.value:
                out[row.key] = row.value["v"]
    return out


def get_all() -> dict[str, Any]:
    """Все оверрайды (только заданные). TTL-кэш; ошибка БД → прошлый кэш/пусто."""
    global _cache, _cached_at
    now = time.monotonic()
    with _lock:
        if _cache is not None and (now - _cached_at) < _TTL:
            return dict(_cache)
    try:
        data = _load()
    except Exception as e:  # noqa: BLE001 — настройки не должны валить крон
        log.warning("bot_settings: load failed, using stale/defaults: %s", e)
        with _lock:
            return dict(_cache) if _cache is not None else {}
    with _lock:
        _cache = data
        _cached_at = now
    return dict(data)


def get(key: str, default: Any = None) -> Any:
    return get_all().get(key, default)


def invalidate() -> None:
    global _cache
    with _lock:
        _cache = None


def set_many(values: dict[str, Any]) -> dict[str, Any]:
    """Записать оверрайды (None = удалить ключ → вернуться к дефолту).
    Валидация по whitelist; возвращает актуальный полный набор."""
    from db.connection import session_scope
    from db.models import BotSetting

    for k, v in values.items():
        if k not in KNOWN_KEYS:
            raise ValueError(f"Неизвестная настройка: {k!r}. Допустимые: {sorted(KNOWN_KEYS)}")
        if v is None:
            continue
        want = KNOWN_KEYS[k]
        if want is float and isinstance(v, (int, float)) and not isinstance(v, bool):
            continue
        if want is int and isinstance(v, int) and not isinstance(v, bool):
            continue
        if want is bool and isinstance(v, bool):
            continue
        if want is str and isinstance(v, str):
            continue
        raise ValueError(f"{k}: ожидается {want.__name__}, получено {type(v).__name__}")

    with session_scope() as s:
        for k, v in values.items():
            row = s.get(BotSetting, k)
            if v is None:
                if row is not None:
                    s.delete(row)
                continue
            if row is None:
                s.add(BotSetting(key=k, value={"v": v}))
            else:
                row.value = {"v": v}
    invalidate()
    log.info("bot_settings: updated %s", sorted(values.keys()))
    return get_all()
