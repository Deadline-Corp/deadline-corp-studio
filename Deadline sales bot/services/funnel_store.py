"""Funnel store — кастомные стадии воронки (своя CRM в Admin UI).

Источник правды: pipeline_stages. Пустая таблица → встроенный набор
(BUILTIN_STAGES = 8 стадий HubSpot-зеркала + терминальный lost). Оператор
в UI редактирует набор; первый же save сеет builtin-строки, чтобы порядок
и подписи были полностью под его контролем.

Бот авто-двигает сделки только по встроенным ключам (services/funnel.py) —
кастомные стадии двигаются руками оператора с канбана. HubSpot-зеркало
шлётся только для ключей из services/crm/hubspot.py STAGE_DEFS.
"""

from __future__ import annotations

import logging
from typing import Optional

log = logging.getLogger(__name__)


def operator_set_stage_recently(db, conversation_id, hours: int = 6) -> bool:
    """True, если стадию ЭТОГО диалога руками трогал оператор/владелец за последние N часов
    (StageTransition.by in admin/operator). Авто-писатели стадии (hot-path воронка и
    cron «молчание→lost») уважают это и НЕ перетирают ручное решение N часов —
    operator-override-wins. Fail-safe → False (никогда не блокируем из-за ошибки чтения)."""
    try:
        from datetime import datetime, timezone, timedelta
        from db.models import StageTransition
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        return db.query(StageTransition.id).filter(
            StageTransition.conversation_id == conversation_id,
            StageTransition.by.in_(("admin", "operator")),
            StageTransition.created_at >= cutoff,
        ).first() is not None
    except Exception:  # noqa: BLE001
        return False


# Встроенный набор (совпадает с HubSpot 8-стадийной воронкой).
BUILTIN_STAGES: list[dict] = [
    {"key": "new_lead", "label": "🆕 Новый лид", "kind": "active"},
    {"key": "in_dialog", "label": "💬 В диалоге", "kind": "active"},
    {"key": "qualified", "label": "✅ Квалифицирован", "kind": "active"},
    {"key": "on_call", "label": "📞 Созвон назначен", "kind": "active"},
    {"key": "proposal", "label": "📄 КП", "kind": "active"},
    {"key": "prepayment", "label": "💰 Аванс", "kind": "active"},
    {"key": "completed_won", "label": "🏁 Сдано", "kind": "won"},
    {"key": "lost", "label": "❌ Не сложилось", "kind": "lost"},
]
BUILTIN_KEYS = {s["key"] for s in BUILTIN_STAGES}

# Legacy-ключи из funnel.py (старые строки в БД могут их хранить) — валидны
# как ИСХОДНАЯ стадия, но в кастомном наборе их можно не показывать.
LEGACY_KEYS = {"nda", "tz_approved", "in_work", "post_sale"}


def get_stages(db) -> list[dict]:
    """Эффективный набор стадий: кастомные из БД или встроенные."""
    from db.models import PipelineStage
    rows = (
        db.query(PipelineStage)
        .order_by(PipelineStage.position.asc(), PipelineStage.created_at.asc())
        .all()
    )
    if not rows:
        return [
            {**s, "position": i, "active": True, "builtin": True, "id": None}
            for i, s in enumerate(BUILTIN_STAGES)
        ]
    return [
        {
            "id": str(r.id), "key": r.key, "label": r.label, "kind": r.kind,
            "position": r.position, "active": r.active, "builtin": r.builtin,
        }
        for r in rows
    ]


def valid_target_keys(db) -> set[str]:
    """Куда оператору можно перевести сделку: активные стадии набора."""
    return {s["key"] for s in get_stages(db) if s["active"]}


def stage_kind(db, key: str) -> str:
    for s in get_stages(db):
        if s["key"] == key:
            return s["kind"]
    return "active"


def save_stages(db, items: list[dict]) -> list[dict]:
    """Полная замена набора (bulk save из UI). Правила:
    - key: slug [a-z0-9_], уникален; для новых генерится из label при пустом.
    - builtin-ключи нельзя удалить (бот ссылается) — их отсутствие в payload
      = ошибка; UI шлёт их всегда (можно active=False чтобы скрыть).
    - lost обязан остаться (kind=lost) — терминал для авто-логики бота.
    Возвращает свежий набор.
    """
    import re
    from db.models import PipelineStage

    seen_keys: set[str] = set()
    cleaned: list[dict] = []
    for i, it in enumerate(items):
        key = (it.get("key") or "").strip().lower()
        label = (it.get("label") or "").strip()
        if not label:
            raise ValueError(f"Стадия #{i + 1}: пустое название")
        if not key:
            key = re.sub(r"[^a-z0-9_]+", "_", label.lower()).strip("_")[:40] or f"stage_{i}"
        if not re.fullmatch(r"[a-z0-9_]{1,40}", key):
            raise ValueError(f"Стадия «{label}»: ключ {key!r} — только [a-z0-9_]")
        if key in seen_keys:
            raise ValueError(f"Дубль ключа {key!r}")
        seen_keys.add(key)
        kind = it.get("kind") or ("lost" if key == "lost" else "active")
        if kind not in ("active", "won", "lost"):
            raise ValueError(f"Стадия «{label}»: kind {kind!r}")
        cleaned.append({
            "key": key, "label": label[:80], "kind": kind,
            "active": bool(it.get("active", True)),
            "builtin": key in BUILTIN_KEYS,
        })

    missing_builtin = BUILTIN_KEYS - seen_keys
    if missing_builtin:
        raise ValueError(
            "Встроенные стадии нельзя удалять (бот на них ссылается), "
            f"не хватает: {sorted(missing_builtin)}. Скрывайте через «глаз» (active=False)."
        )

    # Полная пересборка: проще и надёжнее, чем diff (набор маленький).
    db.query(PipelineStage).delete()
    for pos, it in enumerate(cleaned):
        db.add(PipelineStage(position=pos, **it))
    db.flush()
    log.info("funnel_store: stages saved (%d items)", len(cleaned))
    return get_stages(db)


def reset_to_builtin(db) -> list[dict]:
    """Сброс на встроенный набор: чистим таблицу → фоллбэк."""
    from db.models import PipelineStage
    db.query(PipelineStage).delete()
    db.flush()
    log.info("funnel_store: reset to builtin")
    return get_stages(db)


def migrate_orphaned_leads(db, fallback: Optional[str] = None) -> dict:
    """ГАРАНТИЯ: ни одна карточка не теряется при смене воронки/ниши. После любого
    изменения набора стадий активные диалоги, чья стадия БОЛЬШЕ НЕ СУЩЕСТВУЕТ (удалили
    кастом-стадию / сменили пресет), переносим на безопасную стадию (fallback или
    первую активную нового набора), чтобы они не «осиротели» (не пропали с канбана).
    Каждый перенос логируется в StageTransition (by='funnel-migrate'). Терминальные и
    legacy-ключи (lost/completed_won + nda/tz_approved/in_work/post_sale) НЕ трогаем —
    они валидны как исходная стадия. Возвращает {migrated, by_stage, fallback}."""
    from db.models import Conversation, StageTransition, ConversationStatusEnum

    stages = get_stages(db)
    valid = {s["key"] for s in stages}
    # не сиротим терминальные/служебные: они могут быть валидной финальной стадией
    keep = valid | {"lost", "completed_won"} | LEGACY_KEYS
    if not fallback:
        actives = [s["key"] for s in stages if s["active"] and s["kind"] == "active"]
        fallback = actives[0] if actives else "in_dialog"

    rows = (
        db.query(Conversation)
        .filter(Conversation.status != ConversationStatusEnum.ARCHIVED.value,
                Conversation.lead_stage.isnot(None),
                ~Conversation.lead_stage.in_(list(keep)))
        .all()
    )
    by_stage: dict[str, int] = {}
    for conv in rows:
        old = conv.lead_stage
        by_stage[old] = by_stage.get(old, 0) + 1
        db.add(StageTransition(
            conversation_id=conv.id, customer_id=conv.customer_id,
            from_stage=old, to_stage=fallback, by="funnel-migrate",
        ))
        conv.lead_stage = fallback
    db.flush()
    if rows:
        log.info("funnel_store: migrated %d orphaned leads → %s (%s)",
                 len(rows), fallback, by_stage)
    return {"migrated": len(rows), "by_stage": by_stage, "fallback": fallback}


def count_lost(db, older_than_days: Optional[int] = None) -> int:
    """Сколько «Не сложилось» (lost) в активном виде (для кнопки уборки)."""
    from db.models import Conversation, ConversationStatusEnum
    q = db.query(Conversation.id).filter(
        Conversation.lead_stage == "lost",
        Conversation.status != ConversationStatusEnum.ARCHIVED.value,
    )
    if older_than_days and older_than_days > 0:
        from datetime import datetime, timezone, timedelta
        q = q.filter(Conversation.last_message_at < datetime.now(timezone.utc) - timedelta(days=older_than_days))
    return q.count()


def archive_lost_leads(db, older_than_days: Optional[int] = None) -> dict:
    """Убрать «Не сложилось» (lost) из активного вида в АРХИВ — чтобы старая база
    не засоряла канбан/инбокс/задачник. ОБРАТИМО: `status=ARCHIVED`, НЕ удаляем
    (правило never-delete) — карточки сохраняются, видны в экспорте (leads/conversations
    .csv) и в «Переписках» с include_archived. Полезных vs мусорных различает
    `lost_reason` (hard_stop = отказ/ошибся). `older_than_days` — только молчащие дольше
    N дней (None = все lost). Возвращает {archived, by_reason}."""
    from db.models import Conversation, ConversationStatusEnum
    q = db.query(Conversation).filter(
        Conversation.lead_stage == "lost",
        Conversation.status != ConversationStatusEnum.ARCHIVED.value,
    )
    if older_than_days and older_than_days > 0:
        from datetime import datetime, timezone, timedelta
        q = q.filter(Conversation.last_message_at < datetime.now(timezone.utc) - timedelta(days=older_than_days))
    rows = q.all()
    by_reason: dict[str, int] = {}
    for conv in rows:
        r = conv.lost_reason or "—"
        by_reason[r] = by_reason.get(r, 0) + 1
        conv.status = ConversationStatusEnum.ARCHIVED.value
    db.flush()
    if rows:
        log.info("funnel_store: archived %d lost leads (%s)", len(rows), by_reason)
    return {"archived": len(rows), "by_reason": by_reason}
