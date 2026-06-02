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
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("scheduled_actions")

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
) -> tuple[Optional[str], bool]:
    """Записать ScheduledAction (followup_message). Sync — звать через to_thread.

    executor='bot' если есть chat_id (бот сам напишет), иначе 'human'.
    Возвращает (id_строки, was_new): was_new=True если у диалога НЕ было pending
    bot-followup до этого (т.е. это первая просьба, а не повтор). По нему воркер
    решает, создавать ли НОВУЮ зеркальную CRM-задачу или это reschedule.
    При ошибке — (None, False).
    """
    from db.connection import session_scope
    from db.models import ScheduledAction
    try:
        superseded = 0
        inherited_task_id: Optional[str] = None
        with session_scope() as s:
            # ДЕДУП (latest-wins): у одного диалога — максимум ОДИН pending
            # bot-followup. dispatch_on_message_turn зовёт parse_followup_when на
            # КАЖДОМ сообщении лида, поэтому «удобно завтра» + «давайте в пятницу»
            # + «напишите завтра утром» наплодили бы 3-4 отдельных пинга на одно и
            # то же утро → спам. Последняя просьба заменяет прежние: гасим все
            # ещё-не-сработавшие bot-followup'ы диалога. task_id со старой строки
            # ПЕРЕНОСИМ на новую — чтобы при исполнении закрылась та же CRM-задача
            # (а не плодилась новая).
            if conversation_id:
                prev = (
                    s.query(ScheduledAction)
                    .filter(
                        ScheduledAction.conversation_id == conversation_id,
                        ScheduledAction.action_type == "followup_message",
                        ScheduledAction.status == "pending",
                        ScheduledAction.executor == "bot",
                    )
                    .all()
                )
                for r in prev:
                    if r.crm_task_id and not inherited_task_id:
                        inherited_task_id = r.crm_task_id
                    r.status = "superseded"
                superseded = len(prev)
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
                crm_task_id=crm_task_id or inherited_task_id,
            )
            s.add(row)
            s.flush()
            rid = str(row.id)
        logger.info(
            "[scheduled_actions] queued followup row=%s chat_id=%s due=%s superseded=%d",
            rid, chat_id, due_at, superseded,
        )
        return rid, (superseded == 0)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[scheduled_actions] write failed: %s", exc)
        return None, False


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

    # 1) КЛЕЙМ: атомарно забираем созревшие строки (FOR UPDATE SKIP LOCKED) и
    #    переводим в 'processing' + claimed_at=now. Конкурентный свип/инстанс
    #    пропустит залоченные → одно напоминание не уйдёт дважды. Протухший
    #    'processing' (>15 мин — процесс упал между клеймом и отправкой) перезабираем.
    from datetime import timedelta as _td
    from sqlalchemy import or_ as _or, and_ as _and
    _stale = now - _td(minutes=15)
    todo: list[dict] = []
    with session_scope() as s:
        rows = (
            s.query(ScheduledAction)
            .filter(ScheduledAction.executor == "bot")
            .filter(ScheduledAction.action_type == "followup_message")
            .filter(ScheduledAction.due_at <= now)
            .filter(_or(
                ScheduledAction.status == "pending",
                _and(ScheduledAction.status == "processing",
                     _or(ScheduledAction.claimed_at.is_(None),
                         ScheduledAction.claimed_at < _stale)),
            ))
            .order_by(ScheduledAction.due_at.asc())
            .limit(MAX_PER_SWEEP)
            .with_for_update(skip_locked=True)
            .all()
        )
        for r in rows:
            r.status = "processing"
            r.claimed_at = now
            stats["due"] += 1
            payload = r.payload or {}
            todo.append({
                "id": str(r.id),
                "chat_id": r.chat_id,
                "text": payload.get("text") or DEFAULT_FOLLOWUP_TEXT,
                "crm_task_id": r.crm_task_id,
                "customer_id": str(r.customer_id) if r.customer_id else None,
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
                    row.claimed_at = None
                    if ok:
                        row.status = "done"
                        row.executed_at = now
                        stats["sent"] += 1
                    else:
                        row.attempts = (row.attempts or 0) + 1
                        # <3 неудач — обратно в pending (ретрай на след. свипе);
                        # после 3 — failed, чтобы не долбить вечно.
                        row.status = "failed" if row.attempts >= 3 else "pending"
                        stats["failed"] += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("[scheduled_actions] status update failed row=%s: %s", item["id"], exc)

        # 4) Взаимосвязь с CRM: followup исполнен → закрываем зеркальную задачу в
        #    CRM (через очередь — адаптер живёт в её воркере). Best-effort: сбой
        #    закрытия не валит отправку (она уже состоялась).
        if ok and item.get("crm_task_id") and item.get("customer_id"):
            try:
                from services.crm_queue import enqueue, make_complete_task_event
                enqueue(make_complete_task_event(
                    customer_id=item["customer_id"],
                    task_id=str(item["crm_task_id"]),
                ))
                logger.info("[scheduled_actions] enqueued complete_task crm_task_id=%s (followup done)",
                            item["crm_task_id"])
            except Exception as exc:  # noqa: BLE001
                logger.warning("[scheduled_actions] complete_task enqueue failed: %s", exc)

    logger.info(
        "[scheduled_actions] run_due: due=%d sent=%d failed=%d skipped=%d",
        stats["due"], stats["sent"], stats["failed"], stats["skipped_no_chat"],
    )
    return stats


# =============================================================================
# СОЗВОНЫ (call booking) — записи созвонов + напоминания лиду и админу
# =============================================================================
# Действия:
#   action_type="call_booked"   — сам факт созвона (due_at = время созвона),
#                                  executor="human"; нужен для (а) анти-дабл-брони
#                                  и (б) истории. Бот его НЕ «исполняет».
#   action_type="call_reminder" — напоминание (бот шлёт), payload.audience =
#                                  "lead" | "admin", chat_id = куда слать.

def write_call_booking(
    *,
    customer_id: str,
    conversation_id: Optional[str],
    channel: str,
    chat_id: Optional[str],
    call_at: datetime,
    medium: Optional[str] = None,
) -> Optional[str]:
    """Записать факт назначенного созвона (call_booked). Sync — звать через to_thread."""
    from db.connection import session_scope
    from db.models import ScheduledAction
    try:
        with session_scope() as s:
            row = ScheduledAction(
                customer_id=customer_id,
                conversation_id=conversation_id,
                channel=channel,
                chat_id=chat_id,
                action_type="call_booked",
                executor="human",
                due_at=call_at,
                status="pending",
                payload={"medium": medium},
            )
            s.add(row)
            s.flush()
            rid = str(row.id)
        logger.info("[scheduled_actions] call booked row=%s at=%s medium=%s", rid, call_at, medium)
        return rid
    except Exception as exc:  # noqa: BLE001
        logger.warning("[scheduled_actions] write_call_booking failed: %s", exc)
        return None


def write_call_reminder(
    *,
    customer_id: str,
    conversation_id: Optional[str],
    channel: str,
    chat_id: Optional[str],
    due_at: datetime,
    text: str,
    audience: str,  # "lead" | "admin"
) -> Optional[str]:
    """Записать одно напоминание о созвоне (бот отправит в due_at)."""
    from db.connection import session_scope
    from db.models import ScheduledAction
    try:
        with session_scope() as s:
            row = ScheduledAction(
                customer_id=customer_id,
                conversation_id=conversation_id,
                channel=channel,
                chat_id=chat_id,
                action_type="call_reminder",
                executor="bot",
                due_at=due_at,
                status="pending",
                payload={"text": text, "audience": audience},
            )
            s.add(row)
            s.flush()
            rid = str(row.id)
        return rid
    except Exception as exc:  # noqa: BLE001
        logger.warning("[scheduled_actions] write_call_reminder failed: %s", exc)
        return None


def cancel_call_actions(conversation_id: str) -> int:
    """Отменить будущий созвон и его напоминания у диалога (лид отказался/переносит).

    НЕ удаляет строки — переводит pending → cancelled (обратимо, для истории).
    Возвращает число отменённых строк. Sync — звать через to_thread.
    """
    from db.connection import session_scope
    from db.models import ScheduledAction
    n = 0
    try:
        with session_scope() as s:
            rows = (
                s.query(ScheduledAction)
                .filter(ScheduledAction.conversation_id == conversation_id)
                .filter(ScheduledAction.status == "pending")
                .filter(ScheduledAction.action_type.in_(("call_booked", "call_reminder")))
                .all()
            )
            for r in rows:
                r.status = "cancelled"
                n += 1
        logger.info("[scheduled_actions] cancelled %d call rows for conv=%s", n, conversation_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[scheduled_actions] cancel_call_actions failed: %s", exc)
    return n


def get_taken_call_slots(now_utc: datetime) -> list[datetime]:
    """Времена будущих назначенных созвонов (для анти-дабл-брони)."""
    from db.connection import session_scope
    from db.models import ScheduledAction
    out: list[datetime] = []
    try:
        with session_scope() as s:
            rows = (
                s.query(ScheduledAction.due_at)
                .filter(ScheduledAction.action_type == "call_booked")
                .filter(ScheduledAction.status == "pending")
                .filter(ScheduledAction.due_at >= now_utc)
                .all()
            )
            out = [r[0] for r in rows if r[0] is not None]
    except Exception as exc:  # noqa: BLE001
        logger.warning("[scheduled_actions] get_taken_call_slots failed: %s", exc)
    return out


async def run_due_call_reminders(*, tenant_config: Optional[dict] = None) -> dict:
    """Крон-шаг: разослать созревшие напоминания о созвонах (лиду и админу).

    Лиду — в его chat_id; админу — в chat_id напоминания (обычно опер-группа).
    Изолированно, ошибка одной строки не валит остальные.
    """
    from db.connection import session_scope
    from db.models import ScheduledAction
    from channels.telegram import send_telegram_reply

    stats = {"due": 0, "sent": 0, "failed": 0, "skipped_no_chat": 0}
    token = os.getenv("TELEGRAM_BOT_TOKEN") or (tenant_config or {}).get("telegram_bot_token")
    now = datetime.now(timezone.utc)

    from datetime import timedelta as _td
    from sqlalchemy import or_ as _or, and_ as _and
    _stale = now - _td(minutes=15)
    todo: list[dict] = []
    with session_scope() as s:
        rows = (
            s.query(ScheduledAction)
            .filter(ScheduledAction.action_type == "call_reminder")
            .filter(ScheduledAction.due_at <= now)
            .filter(_or(
                ScheduledAction.status == "pending",
                _and(ScheduledAction.status == "processing",
                     _or(ScheduledAction.claimed_at.is_(None),
                         ScheduledAction.claimed_at < _stale)),
            ))
            .order_by(ScheduledAction.due_at.asc())
            .limit(MAX_PER_SWEEP)
            .with_for_update(skip_locked=True)
            .all()
        )
        for r in rows:
            r.status = "processing"
            r.claimed_at = now
            stats["due"] += 1
            payload = r.payload or {}
            todo.append({
                "id": str(r.id),
                "chat_id": r.chat_id,
                "text": payload.get("text") or "Напоминаю про наш созвон 🙂",
            })

    if not todo:
        return stats

    for item in todo:
        ok = False
        if not item["chat_id"] or not token:
            stats["skipped_no_chat"] += 1
        else:
            try:
                ok = await send_telegram_reply(token, str(item["chat_id"]), item["text"])
            except Exception as exc:  # noqa: BLE001
                logger.warning("[scheduled_actions] call-reminder send failed row=%s: %s", item["id"], exc)
                ok = False
        try:
            with session_scope() as s:
                row = s.get(ScheduledAction, item["id"])
                if row is not None:
                    row.claimed_at = None
                    if ok:
                        row.status = "done"
                        row.executed_at = now
                        stats["sent"] += 1
                    else:
                        row.attempts = (row.attempts or 0) + 1
                        row.status = "failed" if row.attempts >= 3 else "pending"
                        stats["failed"] += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("[scheduled_actions] call-reminder status update failed row=%s: %s", item["id"], exc)

    logger.info(
        "[scheduled_actions] run_due_call_reminders: due=%d sent=%d failed=%d skipped=%d",
        stats["due"], stats["sent"], stats["failed"], stats["skipped_no_chat"],
    )
    return stats
