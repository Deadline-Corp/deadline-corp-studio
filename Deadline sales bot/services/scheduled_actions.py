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
from typing import Any, Optional

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


async def _send_by_channel(channel: Any, chat_id: str, text: str,
                           *, tg_token: Optional[str]) -> bool:
    """Отправить ПРОАКТИВНОЕ сообщение в ПРАВИЛЬНЫЙ канал лида.

    КОРЕНЬ тихой потери лидов: раньше дожимы/напоминания слались жёстко через
    Telegram независимо от канала → WhatsApp/Instagram-лиду уходило в Telegram
    Bot API с чужим chat_id → ok=False → 3 попытки → status=failed → лид молча
    выпадал из дожима и из задачника. Теперь роутим по ScheduledAction.channel.
    """
    ch = (channel.value if hasattr(channel, "value") else str(channel or "")).lower()
    if ch == "whatsapp":
        # Единый WhatsApp-отправитель: WAHA→Green-API→Cloud failover + троттл +
        # дневной потолок (антибан неофиц. WhatsApp). Сам читает настройки.
        from main import _wa_send
        return await _wa_send(str(chat_id), text)
    if ch == "telegram":
        if not tg_token:
            return False
        from channels.telegram import send_telegram_reply as _tg
        return await _tg(tg_token, str(chat_id), text)
    if ch in ("messenger", "instagram"):
        import main as _main
        page_token = getattr(_main.settings, "meta_page_access_token", "") or ""
        if not page_token:
            return False
        # Проактивная досылка (дожим/напоминание) почти всегда ВНЕ 24ч-окна Meta →
        # дефолтный RESPONSE отклонят. Шлём с HUMAN_AGENT-тегом (окно 7 дней). Если у
        # приложения нет human_agent permission — Meta отклонит, строка станет failed
        # и будет видна в «Доставка не удалась» (а не потеряется молча).
        if ch == "messenger":
            from channels.messenger import send_messenger_reply as _mg
            return await _mg(page_token, str(chat_id), text,
                             messaging_type="MESSAGE_TAG", tag="HUMAN_AGENT")
        from channels.instagram import send_instagram_reply as _ig
        return await _ig(page_token, str(chat_id), text,
                         messaging_type="MESSAGE_TAG", tag="HUMAN_AGENT")
    # website / неизвестный — проактивной досылки нет (нет транспорта). Не «успех»,
    # но и не вечный ретрай: вызывающий пометит failed → станет видно в «Затык».
    return False


async def run_due_followups(*, tenant_config: Optional[dict] = None) -> dict:
    """Крон-шаг: исполнить созревшие bot-followup'ы (в КАНАЛ лида, см. _send_by_channel).

    Изолированно. Возвращает stats. Ошибка одной строки не валит остальные.
    """
    from db.connection import session_scope
    from db.models import ScheduledAction

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
        from db.models import Message as _Msg
        for r in rows:
            # Лид уже НЕ молчит? Если после постановки этого дожима в диалоге появилось
            # сообщение лида (role=user) — дожим устарел: гасим (superseded), не шлём
            # вдогонку. Иначе бот пишет «вы просили напомнить» тому, кто уже ответил
            # или забронировал созвон. (Аудит, измерение «отложенные действия».)
            if r.conversation_id is not None and r.created_at is not None:
                _replied = (
                    s.query(_Msg.id)
                    .filter(_Msg.conversation_id == r.conversation_id,
                            _Msg.role == "user",
                            _Msg.created_at > r.created_at)
                    .first()
                )
                if _replied is not None:
                    r.status = "superseded"
                    r.claimed_at = None
                    stats["skipped_replied"] = stats.get("skipped_replied", 0) + 1
                    continue
            # ГАРД ПЕРЕХВАТА (I4): оператор взял диалог на себя → не дожимаем. Защита
            # от гонки (задача могла созреть между переключениями перехвата); основное
            # снятие — в set_operator_takeover, тут — последний рубеж.
            if r.conversation_id is not None:
                from db.models import Conversation as _Conv
                _taken = (
                    s.query(_Conv.operator_takeover)
                    .filter(_Conv.id == r.conversation_id)
                    .scalar()
                )
                if _taken:
                    r.status = "cancelled"
                    r.claimed_at = None
                    stats["skipped_takeover"] = stats.get("skipped_takeover", 0) + 1
                    continue
            r.status = "processing"
            r.claimed_at = now
            stats["due"] += 1
            payload = r.payload or {}
            todo.append({
                "id": str(r.id),
                "chat_id": r.chat_id,
                "channel": r.channel,
                "conversation_id": str(r.conversation_id) if r.conversation_id else None,
                "text": payload.get("text") or DEFAULT_FOLLOWUP_TEXT,
                "crm_task_id": r.crm_task_id,
                "customer_id": str(r.customer_id) if r.customer_id else None,
            })

    if not todo:
        return stats

    # 2) Отправляем (await) вне сессии, затем помечаем результат.
    for item in todo:
        ok = False
        # ГОНКА ПЕРЕХВАТА (I4): клеймы идут пачкой, отправки — сетевые (секунды каждая).
        # Оператор мог взять диалог МЕЖДУ клеймом и этой отправкой → перепроверяем перед
        # отправкой и гасим, не дожимая уже перехваченного лида.
        if item.get("conversation_id"):
            _now_taken = False
            try:
                from db.models import Conversation as _Conv2
                with session_scope() as _cs:
                    _now_taken = bool(_cs.query(_Conv2.operator_takeover)
                                      .filter(_Conv2.id == item["conversation_id"]).scalar())
                    if _now_taken:
                        _r2 = _cs.get(ScheduledAction, item["id"])
                        if _r2 is not None and _r2.status == "processing":
                            _r2.status = "cancelled"
                            _r2.claimed_at = None
            except Exception:  # noqa: BLE001 — перепроверка best-effort
                _now_taken = False
            if _now_taken:
                stats["skipped_takeover"] = stats.get("skipped_takeover", 0) + 1
                continue
        if not item["chat_id"]:
            stats["skipped_no_chat"] += 1
        else:
            try:
                ok = await _send_by_channel(item["channel"], item["chat_id"],
                                            item["text"], tg_token=token)
            except Exception as exc:  # noqa: BLE001
                logger.warning("[scheduled_actions] send failed row=%s ch=%s: %s",
                               item["id"], item.get("channel"), exc)
                ok = False
        # 3) Пометить статус.
        try:
            with session_scope() as s:
                row = s.get(ScheduledAction, item["id"])
                # Не перезаписываем статус, если конкурентный перехват уже пометил
                # строку 'cancelled' между отправкой и этим апдейтом (lost-update guard).
                if row is not None and row.status == "processing":
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


def cancel_future_actions(conversation_id: str) -> int:
    """Снять ВСЕ будущие отложенные действия диалога (созвон, напоминания, followup,
    задачи) — для лида, ушедшего в «Не сложилось». Его не нужно показывать в календаре/
    задачнике впредь: лид ушёл. История (done/sent) сохраняется — трогаем только
    pending/processing. Обратимо (status='cancelled', НЕ удаляем). Sync — звать через
    to_thread."""
    from db.connection import session_scope
    from db.models import ScheduledAction
    n = 0
    try:
        with session_scope() as s:
            rows = (
                s.query(ScheduledAction)
                .filter(ScheduledAction.conversation_id == conversation_id)
                .filter(ScheduledAction.status.in_(("pending", "processing")))
                .all()
            )
            for r in rows:
                r.status = "cancelled"
                n += 1
        if n:
            logger.info("[scheduled_actions] cancelled %d future actions for lost conv=%s",
                        n, conversation_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[scheduled_actions] cancel_future_actions failed: %s", exc)
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
                "channel": r.channel,
                "audience": payload.get("audience"),
                "text": payload.get("text") or "Напоминаю про наш созвон 🙂",
            })

    if not todo:
        return stats

    for item in todo:
        ok = False
        if not item["chat_id"]:
            stats["skipped_no_chat"] += 1
        else:
            try:
                if item.get("audience") == "admin":
                    # Админ-напоминание ВСЕГДА в Telegram опер-группу (chat_id = id
                    # группы), независимо от канала лида.
                    ok = bool(token) and await send_telegram_reply(
                        token, str(item["chat_id"]), item["text"])
                else:
                    # Лиду — в его канал (раньше всё уходило в Telegram → падало).
                    ok = await _send_by_channel(item["channel"], item["chat_id"],
                                                item["text"], tg_token=token)
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


async def run_due_recurring() -> dict:
    """P6 — постоянные клиенты. Для каждого клиента с активной recurrence и
    наступившим next_at ставит бот-followup (run_due_followups доставит) и сдвигает
    next_at += every_days. Хранение: customers.profile_data['recurrence'] =
    {active, every_days, note?, next_at}. Изолировано, ошибки не валят крон."""
    import json
    from datetime import datetime, timezone, timedelta
    from sqlalchemy import text as _sql
    from db.connection import session_scope
    from db.models import Conversation

    stats = {"due": 0, "queued": 0, "errors": 0}
    now = datetime.now(timezone.utc)
    RECUR_TMPL = {
        "ru": "Здравствуйте! Напоминаем про плановый визит — подтвердите удобное время, и команда приедет 🙂",
        "en": "Hello! A reminder about your scheduled visit — please confirm a convenient time and our team will come 🙂",
        "th": "สวัสดีค่ะ! แจ้งเตือนนัดหมายตามกำหนด — โปรดยืนยันเวลาที่สะดวก แล้วทีมงานจะไปให้บริการ 🙂",
    }
    due: list[dict] = []
    try:
        with session_scope() as s:
            rows = s.execute(_sql(
                "SELECT id, profile_data FROM customers "
                "WHERE profile_data->'recurrence'->>'active' = 'true' "
                "AND (profile_data->'recurrence'->>'next_at') IS NOT NULL "
                "AND (profile_data->'recurrence'->>'next_at')::timestamptz <= :now "
                "LIMIT 100"
            ), {"now": now}).fetchall()
            for cid, prof in rows:
                rec = (prof or {}).get("recurrence") or {}
                every = int(rec.get("every_days") or 0)
                if every < 1:
                    continue
                conv = (
                    s.query(Conversation)
                    .filter(Conversation.customer_id == cid)
                    .order_by(Conversation.last_message_at.desc().nullslast())
                    .first()
                )
                if conv is None:
                    continue
                due.append({
                    "cid": str(cid), "conv_id": str(conv.id), "channel": conv.channel,
                    "chat": getattr(conv, "channel_conversation_id", None),
                    "text": (rec.get("note") or "").strip()
                            or RECUR_TMPL.get((prof or {}).get("lang") or "ru", RECUR_TMPL["ru"]),
                    "prof": dict(prof or {}), "rec": dict(rec), "every": every,
                })
    except Exception as e:  # noqa: BLE001
        logger.warning("[scheduled_actions] run_due_recurring query failed: %s", e)
        return stats

    stats["due"] = len(due)
    for d in due:
        try:
            _rid, _ = write_scheduled_action(
                customer_id=d["cid"], conversation_id=d["conv_id"], channel=d["channel"],
                chat_id=str(d["chat"]) if d["chat"] else None, due_at=now, text=d["text"],
            )
            if not _rid:
                # Постановка напоминания НЕ удалась — НЕ сдвигаем next_at, иначе цикл
                # постоянного клиента молча пропустится. Повторим в следующий проход.
                stats["errors"] += 1
                continue
            d["rec"]["next_at"] = (now + timedelta(days=d["every"])).isoformat()
            d["prof"]["recurrence"] = d["rec"]
            with session_scope() as s2:
                s2.execute(
                    _sql("UPDATE customers SET profile_data = CAST(:p AS JSONB) WHERE id = :i"),
                    {"p": json.dumps(d["prof"], ensure_ascii=False), "i": d["cid"]},
                )
            stats["queued"] += 1
        except Exception as e:  # noqa: BLE001
            stats["errors"] += 1
            logger.warning("[scheduled_actions] recurring %s failed: %s", d["cid"][:8], e)

    logger.info("[scheduled_actions] run_due_recurring: due=%d queued=%d errors=%d",
                stats["due"], stats["queued"], stats["errors"])
    return stats
