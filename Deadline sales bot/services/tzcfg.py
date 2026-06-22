"""Единый часовой пояс БИЗНЕСА (панель / задачи менеджера / созвоны) — один
источник правды.

ПРОБЛЕМА (которую это чинит): пояс был ЗАХАРДКОЖЕН (UTC+7) в нескольких модулях
(manager_schedule, scheduling), а фронт брал пояс БРАУЗЕРА → время «плавало»: задачи
и календарь показывали случайные/чужие часы, если владелец заходил с устройства в
другом поясе. Теперь всё читает одну настройку digest_tz_offset.

РЕШЕНИЕ: один ключ настроек `digest_tz_offset` (int, дефолт +7 = Пхукет/Бангкок) —
пояс владельца/панели. Ленивый импорт bot_settings + fail-safe на +7, чтобы модуль
оставался лёгким и НЕ падал/не блокировал крон, если настройки недоступны.
"""
from __future__ import annotations

from datetime import timedelta, timezone

DEFAULT_OFFSET_H = 7  # Пхукет / Бангкок (UTC+7) — дефолт проекта


def biz_offset_hours() -> int:
    """Смещение пояса бизнеса от UTC в часах (из настроек, fail-safe → 7)."""
    try:
        from services import bot_settings
        v = bot_settings.get("digest_tz_offset")
        if v is not None:
            return int(v)
    except Exception:  # noqa: BLE001 — пояс никогда не должен валить крон/логику
        pass
    return DEFAULT_OFFSET_H


def biz_offset() -> timedelta:
    """Смещение пояса бизнеса как timedelta (для schedule_for_manager(tz_offset=...))."""
    return timedelta(hours=biz_offset_hours())


def biz_tz() -> timezone:
    """Пояс бизнеса как timezone (для astimezone / форматирования)."""
    return timezone(biz_offset())
