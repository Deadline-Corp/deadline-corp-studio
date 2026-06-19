"""Проверка ЦЕЛОСТНОСТИ данных — авто-поиск «нарушений связи» между стадией
воронки, бронью созвона, напоминаниями и календарём. Чтобы системой можно было
пользоваться без ручного разбора: ошибки находятся и (безопасные) чинятся сами.

Идея: стадия `on_call` («Созвон назначен») ДОЛЖНА иметь конкретную бронь
(`profile_data.booked_call_at`), а бронь — соответствующую стадию и не-сироты
напоминания. Любое расхождение = рассинхрон, который раньше копился молча.

Всё DB-only, без сети/LLM. `run_diagnostics` только читает и возвращает список
проблем; `auto_heal` чинит лишь БЕЗОПАСНЫЕ/обратимые (отмена сирот-напоминаний,
откат «пустого» on_call в qualified). Ничего не удаляет.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Any

logger = logging.getLogger(__name__)

# Терминальные/«законные» стадии, где бронь может быть/уже отыграна — их не трогаем.
_BOOKING_OK_STAGES = {"on_call", "tz_approved", "proposal", "prepayment",
                      "in_work", "completed_won", "post_sale"}


def _parse(dt: Any):
    try:
        s = str(dt).replace("Z", "+00:00")
        d = datetime.fromisoformat(s)
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def run_diagnostics() -> dict:
    """Прогнать проверки целостности. Возвращает {issues:[...], checked, ok}."""
    from db.connection import session_scope
    from db.models import (
        Conversation as Conv, Customer as Cu, ScheduledAction as SA,
        ConversationStatusEnum as CS,
    )
    now = datetime.now(timezone.utc)
    issues: list = []
    checked = 0
    with session_scope() as db:
        # --- активные on_call карточки ---
        oncall = (
            db.query(Conv, Cu).join(Cu, Conv.customer_id == Cu.id)
            .filter(Conv.lead_stage == "on_call", Conv.status != CS.ARCHIVED)
            .limit(500).all()
        )
        no_booking, past_booking = [], []
        for conv, c in oncall:
            checked += 1
            booked = (c.profile_data or {}).get("booked_call_at")
            bdt = _parse(booked) if booked else None
            if not bdt:
                no_booking.append({"conversation_id": str(conv.id),
                                   "name": c.name or c.email or (("+" + c.phone) if getattr(c, "phone", None) else "Лид")})
            elif bdt < now - timedelta(hours=3):
                past_booking.append({"conversation_id": str(conv.id),
                                     "name": c.name or c.email or "Лид",
                                     "at": bdt.isoformat()})
        if no_booking:
            issues.append({
                "code": "on_call_no_booking", "severity": "high",
                "title": "«Созвон назначен» без времени",
                "detail": "Стадия говорит, что созвон назначен, но конкретной брони нет "
                          "(лид согласился, но время не задано). Воронка/календарь рассинхронены.",
                "items": no_booking, "count": len(no_booking),
                "auto_fix": "вернуть в «Квалифицирован», если тишина >48ч (бронь так и не появилась)",
            })
        if past_booking:
            issues.append({
                "code": "booking_past", "severity": "medium",
                "title": "Созвон в прошлом, не обновлён",
                "detail": "Время созвона уже прошло (>3ч), а лид всё ещё в стадии «Созвон назначен». "
                          "Нужно отметить «состоялся» или перенести.",
                "items": past_booking, "count": len(past_booking),
                "auto_fix": None,  # человек решает (состоялся/перенос)
            })

        # --- бронь есть, а стадия НЕ предполагает созвон (рассинхрон) ---
        booked_rows = (
            db.query(Conv, Cu).join(Cu, Conv.customer_id == Cu.id)
            .filter(Conv.status != CS.ARCHIVED,
                    Cu.profile_data["booked_call_at"].isnot(None))
            .limit(500).all()
        )
        stale_booking = []
        for conv, c in booked_rows:
            if (c.profile_data or {}).get("booked_call_at") and conv.lead_stage not in _BOOKING_OK_STAGES:
                stale_booking.append({"conversation_id": str(conv.id),
                                      "name": c.name or c.email or "Лид",
                                      "stage": conv.lead_stage})
        if stale_booking:
            issues.append({
                "code": "booking_wrong_stage", "severity": "medium",
                "title": "Бронь есть, а стадия не «Созвон»",
                "detail": "У лида стоит бронь созвона, но стадия другая (напр. «Проигран»). "
                          "Бронь устарела — её стоит снять.",
                "items": stale_booking, "count": len(stale_booking),
                "auto_fix": "снять устаревшую бронь + напоминания",
            })

        # --- сироты-напоминания о созвоне: pending call_reminder, а у диалога нет брони
        #     ИЛИ диалог архивный → напоминание висит в календаре зря ---
        rem_rows = (
            db.query(SA, Conv, Cu)
            .outerjoin(Conv, SA.conversation_id == Conv.id)
            .outerjoin(Cu, Conv.customer_id == Cu.id)
            .filter(SA.status.in_(("pending", "processing")),
                    SA.action_type == "call_reminder")
            .limit(1000).all()
        )
        orphan_rem = []
        for a, conv, c in rem_rows:
            booked = (c.profile_data or {}).get("booked_call_at") if c else None
            archived = bool(conv and conv.status == CS.ARCHIVED)
            if archived or not booked:
                orphan_rem.append({"action_id": str(a.id),
                                   "conversation_id": str(a.conversation_id) if a.conversation_id else None})
        if orphan_rem:
            issues.append({
                "code": "orphan_reminder", "severity": "high",
                "title": "Напоминание о созвоне без созвона",
                "detail": "В календаре висят напоминания, у которых уже нет брони "
                          "(созвон отменён/перенесён) или карточка архивна. Ложные события.",
                "items": orphan_rem, "count": len(orphan_rem),
                "auto_fix": "отменить сироты-напоминания",
            })

        # --- НЕАКТУАЛЬНЫЕ задачи «связаться лично / лид завис»: их создаёт
        #     автоматизация при «зависании» лида, но НЕ снимает, когда бот ВЗЯЛ диалог
        #     сам (wa_autonomous) ИЛИ лид снова активен → лишние события в календаре/задачнике.
        stale_cb = [x for x in _stale_callbacks(db, CS)]
        if stale_cb:
            issues.append({
                "code": "stale_callback", "severity": "high",
                "title": "Задача «связаться лично», которая больше не нужна",
                "detail": "Висит задача «лид завис — связаться лично», но бот уже ведёт этот диалог "
                          "сам ИЛИ лид снова активен. В календаре/задачнике это лишний шум.",
                "items": [{"action_id": a, "conversation_id": cid, "name": nm} for a, cid, nm in stale_cb],
                "count": len(stale_cb),
                "auto_fix": "снять неактуальную задачу",
            })

    return {"issues": issues, "checked": checked, "ok": len(issues) == 0,
            "total_problems": sum(i["count"] for i in issues)}


def _stale_callbacks(db, CS) -> list:
    """Список (action_id, conv_id, name) задач operator_callback, ставших неактуальными:
    бот ведёт диалог сам (wa_autonomous) ИЛИ лид снова активен после создания задачи
    (только «лид завис/связаться»-задачи, чтобы не трогать осознанные ручные)."""
    from db.models import Conversation as Conv, Customer as Cu, ScheduledAction as SA
    now = datetime.now(timezone.utc)  # noqa: F841 — для единообразия
    out = []
    rows = (
        db.query(SA, Conv, Cu)
        .join(Conv, SA.conversation_id == Conv.id)
        .join(Cu, Conv.customer_id == Cu.id)
        .filter(SA.status.in_(("pending", "processing")),
                SA.action_type == "operator_callback",
                Conv.status != CS.ARCHIVED)
        .limit(1000).all()
    )
    for a, conv, c in rows:
        txt = ((a.payload or {}).get("text") or "").lower()
        is_stuck = ("завис" in txt or "связаться" in txt)
        created = a.created_at
        if created and created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        lm = conv.last_message_at
        if lm and lm.tzinfo is None:
            lm = lm.replace(tzinfo=timezone.utc)
        newer_activity = bool(created and lm and lm > created + timedelta(minutes=10))
        if conv.wa_autonomous or (is_stuck and newer_activity):
            out.append((str(a.id), str(conv.id), c.name or c.email or "Лид"))
    return out


def auto_heal(*, move_empty_oncall_after_h: int = 48, noshow_grace_h: int = 3) -> dict:
    """БЕЗОПАСНОЕ авто-устранение (обратимое, ничего не удаляет):
      1) отменить сироты-напоминания (status→cancelled);
      2) снять устаревшую бронь у не-созвонной стадии (booked_call_at→pop) + её напоминания;
      3) «пустой» on_call (нет брони) и тишина > move_empty_oncall_after_h → вернуть в qualified;
      4) снять неактуальные задачи «связаться/завис» когда бот ведёт сам/лид активен;
      5) NO-SHOW: on_call с бронью в ПРОШЛОМ (> noshow_grace_h) → снять бронь + откат в
         qualified (лид пропал на созвоне — ведём дальше по воронке, НЕ в lost).
    Логирует каждое исправление в Журнал решений (actor=automation). Возвращает сводку."""
    from db.connection import session_scope
    from db.models import (
        Conversation as Conv, Customer as Cu, ScheduledAction as SA,
        ConversationStatusEnum as CS, StageTransition,
    )
    from services.bot_decisions import log_decision
    now = datetime.now(timezone.utc)
    out = {"orphan_reminders_cancelled": 0, "stale_bookings_cleared": 0,
           "empty_oncall_reverted": 0, "stale_callbacks_cancelled": 0, "noshow_healed": 0}
    with session_scope() as db:
        # 1) сироты-напоминания
        rem_rows = (
            db.query(SA, Conv, Cu)
            .outerjoin(Conv, SA.conversation_id == Conv.id)
            .outerjoin(Cu, Conv.customer_id == Cu.id)
            .filter(SA.status.in_(("pending", "processing")),
                    SA.action_type == "call_reminder")
            .limit(1000).all()
        )
        for a, conv, c in rem_rows:
            booked = (c.profile_data or {}).get("booked_call_at") if c else None
            archived = bool(conv and conv.status == CS.ARCHIVED)
            if archived or not booked:
                a.status = "cancelled"
                out["orphan_reminders_cancelled"] += 1

        # 2) устаревшая бронь у не-созвонной стадии
        booked_rows = (
            db.query(Conv, Cu).join(Cu, Conv.customer_id == Cu.id)
            .filter(Conv.status != CS.ARCHIVED,
                    Cu.profile_data["booked_call_at"].isnot(None))
            .limit(500).all()
        )
        for conv, c in booked_rows:
            if (c.profile_data or {}).get("booked_call_at") and conv.lead_stage not in _BOOKING_OK_STAGES:
                pd = dict(c.profile_data or {})
                pd.pop("booked_call_at", None)
                pd.pop("call_medium", None)
                c.profile_data = pd
                for a in db.query(SA).filter(
                        SA.conversation_id == conv.id,
                        SA.action_type == "call_reminder",
                        SA.status.in_(("pending", "processing"))).all():
                    a.status = "cancelled"
                out["stale_bookings_cleared"] += 1
                log_decision("call_cancelled",
                             f"Авто-исправление: снял устаревшую бронь — лид на стадии «{conv.lead_stage}», "
                             f"созвон туда не относится",
                             conversation_id=conv.id, customer_id=c.id, actor="automation", db=db)

        # 3) «пустой» on_call дольше N часов → откат в qualified
        oncall = (
            db.query(Conv, Cu).join(Cu, Conv.customer_id == Cu.id)
            .filter(Conv.lead_stage == "on_call", Conv.status != CS.ARCHIVED)
            .limit(500).all()
        )
        for conv, c in oncall:
            if (c.profile_data or {}).get("booked_call_at"):
                continue
            lm = conv.last_message_at
            if lm and lm.tzinfo is None:
                lm = lm.replace(tzinfo=timezone.utc)
            silent_h = (now - lm).total_seconds() / 3600 if lm else 9999
            if silent_h >= move_empty_oncall_after_h:
                _from = conv.lead_stage
                conv.lead_stage = "qualified"
                db.add(StageTransition(conversation_id=conv.id, customer_id=conv.customer_id,
                                       from_stage=_from, to_stage="qualified", by="automation"))
                out["empty_oncall_reverted"] += 1
                log_decision("stage_change",
                             "Авто-исправление: вернул «Квалифицирован» — стояла стадия «Созвон назначен», "
                             f"но конкретной брони так и не появилось, тишина {silent_h:.0f}ч",
                             conversation_id=conv.id, customer_id=c.id, actor="automation", db=db)

        # 4) неактуальные задачи «связаться лично / лид завис» — бот ведёт сам или лид активен
        cb_rows = (
            db.query(SA, Conv, Cu)
            .join(Conv, SA.conversation_id == Conv.id)
            .join(Cu, Conv.customer_id == Cu.id)
            .filter(SA.status.in_(("pending", "processing")),
                    SA.action_type == "operator_callback",
                    Conv.status != CS.ARCHIVED)
            .limit(1000).all()
        )
        for a, conv, c in cb_rows:
            txt = ((a.payload or {}).get("text") or "").lower()
            is_stuck = ("завис" in txt or "связаться" in txt)
            created = a.created_at
            if created and created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            lm = conv.last_message_at
            if lm and lm.tzinfo is None:
                lm = lm.replace(tzinfo=timezone.utc)
            newer_activity = bool(created and lm and lm > created + timedelta(minutes=10))
            if conv.wa_autonomous or (is_stuck and newer_activity):
                a.status = "cancelled"
                out["stale_callbacks_cancelled"] += 1

        # 5) NO-SHOW: on_call с бронью в ПРОШЛОМ (> noshow_grace_h) и без подтверждения →
        # снять протухшую бронь+напоминания INLINE (не вложенный session_scope) + откатить
        # on_call→qualified, чтобы лид вернулся в работу (нудж/воронка). НЕ в lost — ghost
        # на созвоне это норма в продажах (просьба владельца), ведём дальше. Грейс 3ч —
        # созвон мог идти прямо сейчас. Не трогаем будущие брони и денежные/юр стадии.
        oncall_rows = (
            db.query(Conv, Cu).join(Cu, Conv.customer_id == Cu.id)
            .filter(Conv.lead_stage == "on_call", Conv.status != CS.ARCHIVED)
            .limit(500).all()
        )
        for conv, c in oncall_rows:
            bca = (c.profile_data or {}).get("booked_call_at")
            bdt = _parse(bca) if bca else None
            if bdt is None:
                continue
            if bdt.tzinfo is None:
                bdt = bdt.replace(tzinfo=timezone.utc)
            hours_ago = (now - bdt).total_seconds() / 3600.0
            if hours_ago < noshow_grace_h:
                continue  # бронь в будущем или созвон может идти прямо сейчас
            for a in db.query(SA).filter(
                    SA.conversation_id == conv.id,
                    SA.action_type.in_(("call_booked", "call_reminder")),
                    SA.status.in_(("pending", "processing"))).all():
                a.status = "cancelled"
            pd = dict(c.profile_data or {})
            pd.pop("booked_call_at", None)
            pd.pop("call_medium", None)
            c.profile_data = pd
            _from = conv.lead_stage
            conv.lead_stage = "qualified"
            db.add(StageTransition(conversation_id=conv.id, customer_id=conv.customer_id,
                                   from_stage=_from, to_stage="qualified", by="automation"))
            out["noshow_healed"] += 1
            log_decision(
                "no_show_healed",
                f"No-show: созвон был {bdt.isoformat()} (прошло {hours_ago:.1f}ч), лид не "
                f"подтвердил — снял бронь и вернул в «Квалифицирован», ведём дальше по воронке",
                conversation_id=conv.id, customer_id=c.id, actor="automation", db=db)
    return out
