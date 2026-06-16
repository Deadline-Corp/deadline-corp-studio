"""Снимки конфигурации — откат на любую точку (2026-06-16).

Снимок = бандл конфиг-таблиц в одной JSONB-строке:
  • воронка (стадии)        — funnel_store
  • кастом-поля лида        — CustomFieldDef
  • автоматизации           — AutomationRule
  • bot_settings            — поведенческие оверрайды
  • активный системный промпт — prompt_store

Восстановление применяет ТОЛЬКО эти таблицы — данные лидов (переписки/клиенты/
сообщения) НЕ трогаются. Авто-снимок берётся ПЕРЕД каждым конфиг-меняющим
действием (reason='auto:...'), плюс ручной чекпойнт из UI. Идемпотентно по смыслу:
повторный снимок одной и той же конфигурации просто создаёт ещё одну строку-версию.

Точки входа:
  capture(db, label, by, reason)  — в рамках запроса (общая транзакция)
  snapshot_now(label, by, reason) — своя сессия (крон/скрипты)
  restore(db, snapshot_id)        — применить снимок (по секциям, с отчётом)
  list_snapshots(db, limit)       — список версий с краткой статистикой
"""

from __future__ import annotations

import logging
from typing import Any, Optional
from uuid import UUID

log = logging.getLogger(__name__)


def _gather_config(db) -> dict:
    """Собрать текущую конфигурацию в payload-бандл."""
    from db.models import CustomFieldDef, AutomationRule
    from services import funnel_store, bot_settings, prompt_store

    stages = funnel_store.get_stages(db)
    funnel = [
        {"key": s["key"], "label": s["label"], "kind": s["kind"],
         "active": s["active"], "position": s["position"]}
        for s in stages
    ]
    fields = [
        {"key": f.key, "label": f.label, "field_type": f.field_type,
         "options": f.options, "position": f.position, "active": f.active}
        for f in db.query(CustomFieldDef).order_by(CustomFieldDef.position.asc()).all()
    ]
    autos = [
        {"name": a.name, "enabled": a.enabled, "trigger": a.trigger,
         "conditions": a.conditions, "actions": a.actions,
         "cooldown_hours": a.cooldown_hours, "position": a.position}
        for a in db.query(AutomationRule).order_by(AutomationRule.position.asc()).all()
    ]
    try:
        settings = bot_settings.get_all()
    except Exception as e:  # noqa: BLE001
        log.warning("config_snapshot: bot_settings read failed: %s", e)
        settings = {}
    try:
        prompt = prompt_store.get_active_system_prompt()
    except Exception as e:  # noqa: BLE001
        log.warning("config_snapshot: prompt read failed: %s", e)
        prompt = None
    return {
        "funnel_stages": funnel,
        "custom_fields": fields,
        "automations": autos,
        "bot_settings": settings,
        "system_prompt": prompt,
    }


def capture(db, label: str, created_by: str = "admin-ui", reason: Optional[str] = None):
    """Снять текущую конфигурацию в ConfigSnapshot (в рамках переданной сессии)."""
    from db.models import ConfigSnapshot
    payload = _gather_config(db)
    snap = ConfigSnapshot(
        label=(label or "checkpoint")[:120],
        created_by=(created_by or "admin-ui")[:100],
        reason=(reason[:200] if reason else None),
        payload=payload,
    )
    db.add(snap)
    db.flush()
    log.info("config_snapshot: captured %s (%s)", str(snap.id)[:8], reason or label)
    return snap


def snapshot_now(label: str, created_by: str = "system", reason: Optional[str] = None) -> Optional[str]:
    """Снять снимок в собственной короткой сессии (для крона/скриптов/авто-хуков
    вне запроса). Возвращает id строки или None при ошибке (НЕ роняем вызывающего —
    снимок не должен мешать самой операции)."""
    try:
        from db.connection import session_scope
        with session_scope() as db:
            snap = capture(db, label, created_by, reason)
            return str(snap.id)
    except Exception as e:  # noqa: BLE001
        log.warning("config_snapshot: snapshot_now failed (%s): %s", reason or label, e)
        return None


def restore(db, snapshot_id: Any) -> dict:
    """Применить снимок конфигурации. По секциям, каждая в try/except → частичный
    сбой одной не отменяет остальные; возвращаем отчёт что восстановлено.

    Восстанавливаются ТОЛЬКО конфиг-таблицы. Данные лидов не трогаются.
    Примечание: автоматизации заменяются полностью (история срабатываний
    automation_runs обнуляется каскадом — правила могут сработать заново)."""
    from db.models import ConfigSnapshot, CustomFieldDef, AutomationRule
    from services import funnel_store, bot_settings, prompt_store

    sid = snapshot_id if isinstance(snapshot_id, UUID) else UUID(str(snapshot_id))
    snap = db.get(ConfigSnapshot, sid)
    if snap is None:
        raise ValueError("snapshot not found")
    p = snap.payload or {}
    out: dict = {"id": str(sid), "label": snap.label, "restored": {}, "errors": {}}

    # 1) Воронка (полная замена; builtin-ключи в снимке всегда есть → save_stages ОК)
    try:
        if p.get("funnel_stages"):
            funnel_store.save_stages(db, p["funnel_stages"])
            out["restored"]["funnel_stages"] = len(p["funnel_stages"])
    except Exception as e:  # noqa: BLE001
        out["errors"]["funnel_stages"] = str(e)

    # 2) Кастом-поля (полная замена)
    try:
        db.query(CustomFieldDef).delete()
        for f in p.get("custom_fields", []):
            db.add(CustomFieldDef(
                key=f["key"], label=f["label"],
                field_type=f.get("field_type", "text"), options=f.get("options"),
                position=f.get("position", 0), active=f.get("active", True),
            ))
        db.flush()
        out["restored"]["custom_fields"] = len(p.get("custom_fields", []))
    except Exception as e:  # noqa: BLE001
        out["errors"]["custom_fields"] = str(e)

    # 3) Автоматизации (полная замена; automation_runs каскадно обнуляются)
    try:
        db.query(AutomationRule).delete()
        for a in p.get("automations", []):
            db.add(AutomationRule(
                name=a["name"], enabled=a.get("enabled", True),
                trigger=a["trigger"], conditions=a.get("conditions"),
                actions=a["actions"], cooldown_hours=a.get("cooldown_hours", 0),
                position=a.get("position", 0),
            ))
        db.flush()
        out["restored"]["automations"] = len(p.get("automations", []))
    except Exception as e:  # noqa: BLE001
        out["errors"]["automations"] = str(e)

    # 4) bot_settings — задать ключи снимка, остальные known-ключи стереть (→ дефолт)
    try:
        snap_settings = p.get("bot_settings", {}) or {}
        target = {k: snap_settings.get(k) for k in bot_settings.KNOWN_KEYS}  # None → удалить
        bot_settings.set_many(target)  # своя сессия + commit + invalidate
        out["restored"]["bot_settings"] = len([k for k, v in target.items() if v is not None])
    except Exception as e:  # noqa: BLE001
        out["errors"]["bot_settings"] = str(e)

    # 5) Системный промпт — активировать контент снимка (или вернуться на константу)
    try:
        sp = p.get("system_prompt")
        if sp:
            prompt_store.set_active_system_prompt(
                sp, created_by="config-restore", comment=f"restore {str(sid)[:8]}",
            )
        else:
            prompt_store.deactivate_all()
        out["restored"]["system_prompt"] = bool(sp)
    except Exception as e:  # noqa: BLE001
        out["errors"]["system_prompt"] = str(e)

    log.info("config_snapshot: restored %s → %s (errors: %s)",
             str(sid)[:8], out["restored"], out["errors"] or "none")
    return out


def list_snapshots(db, limit: int = 50) -> list[dict]:
    """Список версий (новые сверху) с краткой статистикой по секциям."""
    from db.models import ConfigSnapshot
    rows = (
        db.query(ConfigSnapshot)
        .order_by(ConfigSnapshot.created_at.desc())
        .limit(max(1, min(limit, 300)))
        .all()
    )
    out = []
    for r in rows:
        p = r.payload or {}
        out.append({
            "id": str(r.id),
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "label": r.label,
            "created_by": r.created_by,
            "reason": r.reason,
            "counts": {
                "stages": len(p.get("funnel_stages", [])),
                "fields": len(p.get("custom_fields", [])),
                "automations": len(p.get("automations", [])),
                "settings": len(p.get("bot_settings", {}) or {}),
                "has_prompt": bool(p.get("system_prompt")),
            },
        })
    return out
