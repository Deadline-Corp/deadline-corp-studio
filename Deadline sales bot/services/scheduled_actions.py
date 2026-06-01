# -*- coding: utf-8 -*-
"""Task Engine B2 — само-исполнение отложенных действий бота.

Своя очередь (таблица scheduled_actions) — источник правды для крона.
- write_scheduled_action(...) — записать строку (вызывается из CRM-воркера,
  off event loop — через asyncio.to_thread, как остальной DB в воркере).
- run_due_followups(...) — крон-шаг: взять созревшие bot-действия, отправить
  сообщение лиду в Telegram, пометить done. Изолирован (свой try/except в кроне).

Само-отправка возможна ТОЛЬКО лидам в мессенджере (есть chat_id). На сайте
chat_id нет → строка не пишется (там работает только задача-напоминание B1).
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger("scheduled_actions")

# Два followup'а с почти одинаковым сроком (лид дважды сказал «через неделю»)
# схлопываются в один — не дёргаем лида дважды. Окно ± этих часов.
DEDUP_WINDOW_HOURS = 12

DEFAULT_FOLLOWUP_TEXT = (
    "Привет! Вы просили напомнить — мы на связи 🙂 Готовы обсудить ваш проект, "
    "когда вам удобно? Если уже неактуально — просто скажите."
)
MAX_PER_SWEEP = 25


def write_scheduled_action(
    *,
    customer_id: str,
    conversation_id: Optional[str],
    channel: str,
    chat_id: Optional[str],
    due_at: datetime,
    text: Optional[str] = None,
    crm_task_id: Optional[str] = None,
) -> Optional[str]:
    """Записать ScheduledAction (followup_message). Sync — звать через to_thread.

    executor='bot' если есть chat_id (бот сам напишет), иначе 'human'.
    Возвращает id строки или None при ошибке.
    """
    from db.connection import session_scope
    from db.models import ScheduledAction
    from uuid import UUID
    try:
        # customer_id приходит строкой — приводим к UUID для сравнения с колонкой
        # (как в crm_queue._resolve_pending_*). Невалидный → оставляем как есть.
        try:
            _cid = UUID(str(customer_id))
        except (ValueError, TypeError, AttributeError):
            _cid = customer_id
        with session_scope() as s:
            # Дедуп: уже есть pending followup для этого лида с близким сроком?
            # Тогда не плодим вторую строку — при необходимости дольём crm_task_id.
            existing = (
                s.query(ScheduledAction)
                .filter(ScheduledAction.customer_id == _cid)
                .filter(ScheduledAction.action_type == "followup_message")
                .filter(ScheduledAction.status == "pending")
                .filter(ScheduledAction.due_at >= due_at - timedelta(hours=DEDUP_WINDOW_HOURS))
                .filter(ScheduledAction.due_at <= due_at + timedelta(hours=DEDUP_WINDOW_HOURS))
                .order_by(ScheduledAction.due_at.asc())
                .first()
            )
            if existing is not None:
                if crm_task_id and not existing.crm_task_id:
                    existing.crm_task_id = crm_task_id
                rid = str(existing.id)
                logger.info(
                    "[scheduled_actions] dedup: pending followup row=%s near due=%s — skip new",
                    rid, due_at,
                )
                return rid
            row = ScheduledAction(
                customer_id=customer_id,
                conversation_id=conversation_id,
                channel=channel,
                chat_id=chat_id,
                action_type="followup_message",
                executor="bot" if chat_id else "human",
                due_at=due_at,
                status="pending",
                payload={"text": text or DEFAULT_FOLLOWUP_TEXT},
                crm_task_id=crm_task_id,
            )
            s.add(row)
            s.flush()
            rid = str(row.id)
        logger.info(
            "[scheduled_actions] queued followup row=%s chat_id=%s due=%s",
            rid, chat_id, due_at,
        )
        return rid
    except Exception as exc:  # noqa: BLE001
        logger.warning("[scheduled_actions] write failed: %s", exc)
        return None


async def run_due_followups(*, tenant_config: Optional[dict] = None) -> dict:
    """Крон-шаг: исполнить созревшие bot-followup'ы (отправить в Telegram).

    Изолированно. Возвращает stats. Ошибка одной строки не валит остальные.
    """
    from db.connection import session_scope
    from db.models import ScheduledAction
    from channels.telegram import send_telegram_reply

    stats = {"due": 0, "sent": 0, "failed": 0, "skipped_no_chat": 0}
    token = os.getenv("TELEGRAM_BOT_TOKEN") or (tenant_config or {}).get("telegram_bot_token")
    now = datetime.now(timezone.utc)

    # 1) Забираем созревшие строки + материализуем нужные поля (чтобы не держать
    #    ORM-объекты вне сессии).
    todo: list[dict] = []
    with session_scope() as s:
        rows = (
            s.query(ScheduledAction)
            .filter(ScheduledAction.status == "pending")
            .filter(ScheduledAction.executor == "bot")
            .filter(ScheduledAction.action_type == "followup_message")
            .filter(ScheduledAction.due_at <= now)
            .order_by(ScheduledAction.due_at.asc())
            .limit(MAX_PER_SWEEP)
            .all()
        )
        for r in rows:
            stats["due"] += 1
            payload = r.payload or {}
            todo.append({
                "id": str(r.id),
                "chat_id": r.chat_id,
                "text": payload.get("text") or DEFAULT_FOLLOWUP_TEXT,
                "crm_task_id": r.crm_task_id,
                "customer_id": str(r.customer_id),
            })

    if not todo:
        return stats

    # 2) Отправляем (await) вне сессии, затем помечаем результат.
    for item in todo:
        ok = False
        if not item["chat_id"] or not token:
            stats["skipped_no_chat"] += 1
        else:
            try:
                ok = await send_telegram_reply(token, str(item["chat_id"]), item["text"])
            except Exception as exc:  # noqa: BLE001
                logger.warning("[scheduled_actions] send failed row=%s: %s", item["id"], exc)
                ok = False
        # 3) Пометить статус.
        try:
            with session_scope() as s:
                row = s.get(ScheduledAction, item["id"])
                if row is not None:
                    if ok:
                        row.status = "done"
                        row.executed_at = now
                        stats["sent"] += 1
                        # Закрыть зеркальную HubSpot-задачу — бот сам отработал
                        # followup. Через очередь (крон на главном лупе, enqueue
                        # безопасен). Нет crm_task_id → нечего закрывать.
                        if item.get("crm_task_id"):
                            try:
                                from services.crm_queue import (
                                    enqueue,
                                    make_complete_task_event,
                                )
                                enqueue(make_complete_task_event(
                                    customer_id=item["customer_id"],
                                    task_id=item["crm_task_id"],
                                ))
                            except Exception as exc:  # noqa: BLE001
                                logger.warning(
                                    "[scheduled_actions] complete_task enqueue failed row=%s: %s",
                                    item["id"], exc,
                                )
                    else:
                        row.attempts = (row.attempts or 0) + 1
                        # после 3 неудач — фейл, чтобы не долбить вечно
                        if row.attempts >= 3:
                            row.status = "failed"
                        stats["failed"] += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("[scheduled_actions] status update failed row=%s: %s", item["id"], exc)

    logger.info(
        "[scheduled_actions] run_due: due=%d sent=%d failed=%d skipped=%d",
        stats["due"], stats["sent"], stats["failed"], stats["skipped_no_chat"],
    )
    return stats
