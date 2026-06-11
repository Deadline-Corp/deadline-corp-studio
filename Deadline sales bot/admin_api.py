"""Admin UI API — слой данных для визуальной панели управления ботом.

Все эндпоинты под /admin/api/*, Bearer-токен (ADMIN_UI_TOKEN, фоллбэк
TRAINING_AUTH_TOKEN), fail-closed как _verify_training_token в main.py.

Дизайн:
- Никакой бизнес-логики здесь — только чтение БД + вызовы существующих
  сервисов (operator_actions, funnel, crm_dispatch, scheduled_actions,
  prompt_store). Один код с Telegram-форумом = нет рассинхрона.
- Доступ к настройкам/тенанту/LLM main.py — ЛЕНИВО внутри функций
  (`import main as _main`): main.py импортирует этот модуль на верхнем
  уровне, обратный top-level импорт дал бы цикл.
- /settings отдаёт ТОЛЬКО санитизированные данные (никаких токенов).
"""

from __future__ import annotations

import hmac
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func as sql_func, select as sql_select, or_
from sqlalchemy.orm import Session

from db.connection import get_db
from db.models import (
    Customer,
    Conversation,
    Message,
    KBChunk,
    TrainingCorrection,
    ScheduledAction,
    CRMEvent,
    PromptVersion,
)

log = logging.getLogger("deadline-bot.admin-api")

router = APIRouter(prefix="/admin/api", tags=["admin-ui"])


# ============================================================================
# AUTH
# ============================================================================

def _verify_admin_token(request: Request) -> None:
    """Bearer-токен на каждый /admin/api эндпоинт. Fail-closed: ни
    ADMIN_UI_TOKEN, ни TRAINING_AUTH_TOKEN не заданы → 503 (фича выключена)."""
    import os
    import main as _main
    expected = os.getenv("ADMIN_UI_TOKEN") or _main.settings.training_auth_token
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="Admin UI disabled — set ADMIN_UI_TOKEN (or TRAINING_AUTH_TOKEN) in env.",
        )
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Bearer token required")
    token = auth.split(None, 1)[1].strip()
    if not hmac.compare_digest(token.encode("utf-8"), expected.encode("utf-8")):
        raise HTTPException(status_code=403, detail="Invalid token")


@router.get("/me")
async def me(_: None = Depends(_verify_admin_token)):
    import main as _main
    return {"ok": True, "tenant": _main.tenant.slug, "display_name": _main.tenant.display_name}


# ============================================================================
# OVERVIEW — данные для канваса
# ============================================================================

CHANNELS = ("website", "telegram", "instagram", "messenger")


@router.get("/overview")
async def overview(
    _: None = Depends(_verify_admin_token),
    db: Session = Depends(get_db),
):
    import main as _main
    s = _main.settings

    # Каналы: configured из env, счётчики из БД (ORM enum-маппинг прозрачен).
    by_channel_total = dict(db.execute(
        sql_select(Conversation.channel, sql_func.count()).group_by(Conversation.channel)
    ).fetchall())
    by_channel_open = dict(db.execute(
        sql_select(Conversation.channel, sql_func.count())
        .where(Conversation.status == "open")
        .group_by(Conversation.channel)
    ).fetchall())
    last_msg_by_channel = dict(db.execute(
        sql_select(Conversation.channel, sql_func.max(Conversation.last_message_at))
        .group_by(Conversation.channel)
    ).fetchall())

    configured = {
        "website": True,
        "telegram": bool(s.telegram_bot_token),
        "instagram": bool(s.meta_page_access_token),
        "messenger": bool(s.meta_page_access_token),
    }
    channels = []
    for ch in CHANNELS:
        last = last_msg_by_channel.get(ch)
        channels.append({
            "id": ch,
            "configured": configured[ch],
            "conversations": int(by_channel_total.get(ch, 0)),
            "open": int(by_channel_open.get(ch, 0)),
            "last_message_at": last.isoformat() if last else None,
        })

    # Воронка: динамический набор стадий (funnel_store: кастомные из БД или
    # встроенные 8) + counts по lead_stage.
    from services import funnel_store
    stage_counts = dict(db.execute(
        sql_select(Conversation.lead_stage, sql_func.count()).group_by(Conversation.lead_stage)
    ).fetchall())
    all_stages = funnel_store.get_stages(db)
    funnel_stages = [
        {"stage": s["key"], "label": s["label"], "kind": s["kind"],
         "count": int(stage_counts.get(s["key"], 0))}
        for s in all_stages if s["active"]
    ]
    known = {s["key"] for s in all_stages if s["active"]}
    other = sum(int(c) for st, c in stage_counts.items() if st not in known)

    # KB / training / CRM / tasks / inbox.
    kb_chunks = db.execute(sql_select(sql_func.count()).select_from(KBChunk)).scalar() or 0
    kb_sources = db.execute(
        sql_select(sql_func.count(sql_func.distinct(KBChunk.source)))
    ).scalar() or 0
    active_rules = db.execute(
        sql_select(sql_func.count()).select_from(TrainingCorrection)
        .where(TrainingCorrection.is_active == True)  # noqa: E712
    ).scalar() or 0
    crm_pending = db.execute(
        sql_select(sql_func.count()).select_from(CRMEvent).where(CRMEvent.status == "pending")
    ).scalar() or 0
    crm_failed = db.execute(
        sql_select(sql_func.count()).select_from(CRMEvent).where(CRMEvent.status == "failed")
    ).scalar() or 0
    tasks_pending = db.execute(
        sql_select(sql_func.count()).select_from(ScheduledAction)
        .where(ScheduledAction.status == "pending")
    ).scalar() or 0
    inbox_open = db.execute(
        sql_select(sql_func.count()).select_from(Conversation)
        .where(Conversation.status == "open")
    ).scalar() or 0
    inbox_takeover = db.execute(
        sql_select(sql_func.count()).select_from(Conversation)
        .where(Conversation.operator_takeover == True)  # noqa: E712
    ).scalar() or 0
    inbox_handed_off = db.execute(
        sql_select(sql_func.count()).select_from(Conversation)
        .where(Conversation.status == "handed_off")
    ).scalar() or 0

    # «Мозг»: активная DB-версия или константа.
    prompt_source = "file"
    try:
        from services.prompt_store import get_active_system_prompt
        if get_active_system_prompt():
            prompt_source = "db"
    except Exception:  # noqa: BLE001
        pass

    return {
        "bot": {
            "model": _main._LLM_PRIMARY_MODEL,
            "fallback_model": _main._LLM_FALLBACK_MODEL,
            "provider": _main._LLM_PROVIDER,
            "tenant": _main.tenant.slug,
            "display_name": _main.tenant.display_name,
            "version": _main.app.version,
            "prompt_source": prompt_source,
        },
        "channels": channels,
        "funnel": {"stages": funnel_stages, "other": other},
        "kb": {"chunks": int(kb_chunks), "sources": int(kb_sources)},
        "training": {"active_corrections": int(active_rules)},
        "crm": {
            "enabled": s.crm_enabled,
            "provider": s.crm_provider,
            "events_pending": int(crm_pending),
            "events_failed": int(crm_failed),
        },
        "tasks": {"scheduled_pending": int(tasks_pending)},
        "inbox": {
            "open": int(inbox_open),
            "takeover": int(inbox_takeover),
            "handed_off": int(inbox_handed_off),
        },
    }


# ============================================================================
# INBOX — переписки всех каналов в одном месте
# ============================================================================

def _conv_summary_row(conv: Conversation, cust: Customer, preview: Optional[str]) -> dict:
    return {
        "id": str(conv.id),
        "channel": conv.channel,
        "status": conv.status,
        "lead_stage": conv.lead_stage,
        "lost_reason": conv.lost_reason,
        "operator_takeover": conv.operator_takeover,
        "handoff_done": conv.handoff_done,
        "last_message_at": conv.last_message_at.isoformat() if conv.last_message_at else None,
        "created_at": conv.created_at.isoformat() if conv.created_at else None,
        "customer": {
            "id": str(cust.id),
            "name": cust.name,
            "email": cust.email,
            "phone": cust.phone,
            "lead_score": cust.lead_score,
            "lead_temperature": cust.lead_temperature,
            "interaction_type": cust.interaction_type,
        },
        "preview": preview,
    }


@router.get("/conversations")
async def conversations_list(
    channel: Optional[str] = None,
    stage: Optional[str] = None,
    temperature: Optional[str] = None,
    status: Optional[str] = None,
    takeover: Optional[bool] = None,
    q: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    _: None = Depends(_verify_admin_token),
    db: Session = Depends(get_db),
):
    limit = max(1, min(limit, 200))
    query = (
        db.query(Conversation, Customer)
        .join(Customer, Conversation.customer_id == Customer.id)
    )
    if channel:
        query = query.filter(Conversation.channel == channel)
    if stage:
        query = query.filter(Conversation.lead_stage == stage)
    if status:
        query = query.filter(Conversation.status == status)
    if temperature:
        query = query.filter(Customer.lead_temperature == temperature)
    if takeover is not None:
        query = query.filter(Conversation.operator_takeover == takeover)
    if q:
        like = f"%{q.strip()}%"
        query = query.filter(or_(
            Customer.name.ilike(like),
            Customer.email.ilike(like),
            Customer.phone.ilike(like),
        ))

    total = query.count()
    rows = (
        query.order_by(Conversation.last_message_at.desc().nullslast())
        .offset(offset).limit(limit).all()
    )

    # Превью последнего сообщения одним запросом на страницу.
    conv_ids = [c.id for c, _cu in rows]
    previews: dict = {}
    if conv_ids:
        sub = (
            db.query(
                Message.conversation_id,
                Message.content,
                sql_func.row_number().over(
                    partition_by=Message.conversation_id,
                    order_by=Message.created_at.desc(),
                ).label("rn"),
            )
            .filter(Message.conversation_id.in_(conv_ids))
            .subquery()
        )
        for cid, content in db.query(sub.c.conversation_id, sub.c.content).filter(sub.c.rn == 1):
            previews[cid] = (content or "")[:120]

    return {
        "total": total,
        "items": [
            _conv_summary_row(conv, cust, previews.get(conv.id))
            for conv, cust in rows
        ],
    }


@router.get("/conversations/{conv_id}")
async def conversation_detail(
    conv_id: str,
    _: None = Depends(_verify_admin_token),
    db: Session = Depends(get_db),
):
    import main as _main
    conv, cust = _get_conv_or_404(db, conv_id)

    # HubSpot deep-links для прыжка из нашей карточки в CRM.
    portal = _main.settings.hubspot_portal_id
    hubspot = {}
    if portal and cust.crm_contact_id:
        hubspot["contact_url"] = f"https://app-na2.hubspot.com/contacts/{portal}/record/0-1/{cust.crm_contact_id}"
    if portal and conv.crm_deal_id:
        hubspot["deal_url"] = f"https://app-na2.hubspot.com/contacts/{portal}/record/0-3/{conv.crm_deal_id}"

    pending_actions = (
        db.query(ScheduledAction)
        .filter(
            ScheduledAction.conversation_id == conv.id,
            ScheduledAction.status == "pending",
        )
        .order_by(ScheduledAction.due_at.asc())
        .all()
    )

    out = _conv_summary_row(conv, cust, None)
    out.update({
        "summary": conv.summary,
        "forum_topic_id": conv.forum_topic_id,
        "crm_deal_id": conv.crm_deal_id,
        "crm_contact_id": cust.crm_contact_id,
        "hubspot": hubspot,
        "utm": {
            "source": cust.utm_source, "campaign": cust.utm_campaign,
            "medium": cust.utm_medium, "content": cust.utm_content,
        },
        "scheduled_actions": [
            {
                "id": str(a.id), "action_type": a.action_type, "executor": a.executor,
                "due_at": a.due_at.isoformat() if a.due_at else None,
                "payload": a.payload,
            }
            for a in pending_actions
        ],
    })
    return out


@router.get("/conversations/{conv_id}/messages")
async def conversation_messages(
    conv_id: str,
    after: Optional[str] = None,
    before: Optional[str] = None,
    limit: int = 50,
    _: None = Depends(_verify_admin_token),
    db: Session = Depends(get_db),
):
    conv, _cust = _get_conv_or_404(db, conv_id)
    limit = max(1, min(limit, 200))

    query = db.query(Message).filter(Message.conversation_id == conv.id)
    if after:
        query = query.filter(Message.created_at > _parse_iso(after))
        rows = query.order_by(Message.created_at.asc()).limit(limit).all()
    elif before:
        query = query.filter(Message.created_at < _parse_iso(before))
        rows = list(reversed(query.order_by(Message.created_at.desc()).limit(limit).all()))
    else:
        rows = list(reversed(query.order_by(Message.created_at.desc()).limit(limit).all()))

    return {
        "items": [
            {
                "id": str(m.id),
                "role": m.role,
                "content": m.content,
                "created_at": m.created_at.isoformat() if m.created_at else None,
                "extra_meta": m.extra_meta,
            }
            for m in rows
        ],
    }


# ============================================================================
# OPERATOR ACTIONS — reply / takeover (общий код с Telegram-форумом)
# ============================================================================

class ReplyRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=4000)


@router.post("/conversations/{conv_id}/reply")
async def conversation_reply(
    conv_id: str,
    req: ReplyRequest,
    _: None = Depends(_verify_admin_token),
    db: Session = Depends(get_db),
):
    import main as _main
    from services.operator_actions import deliver_operator_reply, mirror_to_forum
    from services.conversations import append_message

    conv, _cust = _get_conv_or_404(db, conv_id)
    text = req.text.strip()

    delivered = await deliver_operator_reply(conv, text, _main.settings)
    append_message(
        db, conv.id, role="operator", content=text,
        extra_meta={"by": "admin-ui", "delivered": delivered},
    )
    db.commit()

    # Анти-рассинхрон: операторы в Telegram-форуме видят, что из UI уже ответили.
    await mirror_to_forum(conv, f"💻 [Admin UI → лиду] {text}", _main.settings)

    return {"ok": True, "delivered": delivered, "channel": conv.channel}


class TakeoverRequest(BaseModel):
    on: bool


@router.post("/conversations/{conv_id}/takeover")
async def conversation_takeover(
    conv_id: str,
    req: TakeoverRequest,
    _: None = Depends(_verify_admin_token),
    db: Session = Depends(get_db),
):
    import main as _main
    from services.operator_actions import set_takeover_with_mirror

    conv, _cust = _get_conv_or_404(db, conv_id)
    await set_takeover_with_mirror(db, conv, req.on, _main.settings, source="admin-ui")
    return {"ok": True, "operator_takeover": req.on}


# ============================================================================
# FUNNEL — смена стадии (operator override) + зеркало в CRM
# ============================================================================

class StageRequest(BaseModel):
    to_stage: str
    lost_reason: Optional[str] = None


@router.post("/conversations/{conv_id}/stage")
async def conversation_stage(
    conv_id: str,
    req: StageRequest,
    _: None = Depends(_verify_admin_token),
    db: Session = Depends(get_db),
):
    from services.funnel import LOST_REASONS
    from services import funnel_store
    from services.conversations import append_message

    conv, cust = _get_conv_or_404(db, conv_id)
    from_stage = conv.lead_stage

    # Валидация по ЭФФЕКТИВНОМУ набору стадий (кастомные из БД или встроенные).
    allowed = funnel_store.valid_target_keys(db)
    if req.to_stage not in allowed:
        raise HTTPException(
            status_code=422,
            detail=f"Неизвестная/скрытая стадия {req.to_stage!r}. Доступные: {sorted(allowed)}",
        )
    is_lost = (req.to_stage == "lost") or (funnel_store.stage_kind(db, req.to_stage) == "lost")
    if is_lost:
        if not req.lost_reason:
            raise HTTPException(status_code=422, detail="Для «Проигран» нужна причина (lost_reason)")
        if req.lost_reason not in LOST_REASONS:
            raise HTTPException(status_code=422, detail=f"Причина {req.lost_reason!r} не из списка {sorted(LOST_REASONS)}")

    conv.lead_stage = req.to_stage
    conv.lost_reason = req.lost_reason if is_lost else None
    # Аудит-трейл бесплатно — system-сообщение в самом диалоге.
    append_message(
        db, conv.id, role="system",
        content=f"[ADMIN] стадия: {from_stage} → {req.to_stage}"
                + (f" (причина: {req.lost_reason})" if req.lost_reason else ""),
    )
    db.commit()

    # Зеркало в HubSpot через durable-очередь — только для встроенных ключей
    # (кастомные стадии живут в нашей воронке, у HubSpot их нет).
    import main as _main
    mirrored = False
    if _main.settings.crm_enabled and req.to_stage in funnel_store.BUILTIN_KEYS:
        from services.crm_dispatch import dispatch_stage_change
        dispatch_stage_change(
            customer_id=str(conv.customer_id),
            crm_deal_id=conv.crm_deal_id,
            new_stage=req.to_stage,
            lost_reason=req.lost_reason if is_lost else None,
            conversation_id=str(conv.id),
        )
        mirrored = True

    return {"ok": True, "from_stage": from_stage, "to_stage": req.to_stage, "crm_mirrored": mirrored}


# ============================================================================
# FUNNEL STAGES — редактор стадий (своя CRM)
# ============================================================================

class StageItem(BaseModel):
    key: Optional[str] = None
    label: str = Field(..., min_length=1, max_length=80)
    kind: str = Field("active", pattern="^(active|won|lost)$")
    active: bool = True


class StagesSaveRequest(BaseModel):
    items: list[StageItem] = Field(..., min_length=2, max_length=30)


@router.get("/funnel/stages")
async def funnel_stages_get(
    _: None = Depends(_verify_admin_token),
    db: Session = Depends(get_db),
):
    from services import funnel_store
    return {"items": funnel_store.get_stages(db), "custom": _stages_customized(db)}


@router.post("/funnel/stages")
async def funnel_stages_save(
    req: StagesSaveRequest,
    _: None = Depends(_verify_admin_token),
    db: Session = Depends(get_db),
):
    from services import funnel_store
    try:
        items = funnel_store.save_stages(db, [it.model_dump() for it in req.items])
    except ValueError as e:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(e))
    db.commit()
    return {"ok": True, "items": items}


@router.post("/funnel/stages/reset")
async def funnel_stages_reset(
    _: None = Depends(_verify_admin_token),
    db: Session = Depends(get_db),
):
    from services import funnel_store
    items = funnel_store.reset_to_builtin(db)
    db.commit()
    return {"ok": True, "items": items}


def _stages_customized(db) -> bool:
    from db.models import PipelineStage
    return db.query(PipelineStage.id).first() is not None


# ============================================================================
# TODAY — «Мой день»: задачи бота/человека + созвоны (3 зоны срочности)
# ============================================================================

@router.get("/today")
async def today_view(
    _: None = Depends(_verify_admin_token),
    db: Session = Depends(get_db),
):
    now = datetime.now(timezone.utc)
    eod = now.replace(hour=23, minute=59, second=59)
    week = now + timedelta(days=7)

    rows = (
        db.query(ScheduledAction, Customer)
        .join(Customer, ScheduledAction.customer_id == Customer.id)
        .filter(ScheduledAction.status.in_(("pending", "processing")))
        .filter(ScheduledAction.due_at <= week)
        .order_by(ScheduledAction.due_at.asc())
        .limit(200)
        .all()
    )

    def pack(a: ScheduledAction, c: Customer) -> dict:
        return {
            "id": str(a.id),
            "action_type": a.action_type,
            "executor": a.executor,
            "due_at": a.due_at.isoformat() if a.due_at else None,
            "channel": a.channel,
            "text": (a.payload or {}).get("text") or (a.payload or {}).get("title"),
            "conversation_id": str(a.conversation_id) if a.conversation_id else None,
            "customer": {"id": str(c.id), "name": c.name, "email": c.email},
        }

    overdue, today, upcoming = [], [], []
    for a, c in rows:
        due = a.due_at
        if due and due.tzinfo is None:
            due = due.replace(tzinfo=timezone.utc)
        item = pack(a, c)
        if due and due < now:
            overdue.append(item)
        elif due and due <= eod:
            today.append(item)
        else:
            upcoming.append(item)

    # Назначенные созвоны (profile_data.booked_call_at) на ближайшую неделю.
    calls = []
    custs = (
        db.query(Customer, Conversation)
        .join(Conversation, Conversation.customer_id == Customer.id)
        .filter(Customer.profile_data.isnot(None))
        .filter(Conversation.lead_stage == "on_call")
        .limit(100)
        .all()
    )
    seen_cust = set()
    for c, conv in custs:
        if c.id in seen_cust:
            continue
        seen_cust.add(c.id)
        booked = (c.profile_data or {}).get("booked_call_at")
        medium = (c.profile_data or {}).get("call_medium")
        calls.append({
            "customer": {"id": str(c.id), "name": c.name, "email": c.email},
            "conversation_id": str(conv.id),
            "channel": conv.channel,
            "call_at": booked,
            "medium": medium,
        })

    return {"overdue": overdue, "today": today, "upcoming": upcoming, "calls": calls}


class TaskCreateRequest(BaseModel):
    conversation_id: str
    text: str = Field(..., min_length=1, max_length=2000)
    due_at: str
    executor: str = Field("human", pattern="^(bot|human)$")


@router.post("/tasks")
async def task_create(
    req: TaskCreateRequest,
    _: None = Depends(_verify_admin_token),
    db: Session = Depends(get_db),
):
    """Ручная задача из панели. executor=bot → бот сам напишет лиду в срок
    (только Telegram — ограничение run_due_followups); executor=human →
    строка в задачнике, человек закроет кнопкой «Сделано»."""
    conv, cust = _get_conv_or_404(db, req.conversation_id)
    due = _parse_iso(req.due_at)

    if req.executor == "bot":
        if conv.channel != "telegram":
            raise HTTPException(
                status_code=409,
                detail="Бот-задачи с автоотправкой пока только для Telegram-лидов. "
                       "Для этого канала поставьте задачу на человека.",
            )
        import asyncio
        from services.scheduled_actions import write_scheduled_action
        action_id, _was_new = await asyncio.to_thread(
            lambda: write_scheduled_action(
                customer_id=str(conv.customer_id),
                conversation_id=str(conv.id),
                channel=conv.channel,
                chat_id=conv.channel_conversation_id,
                due_at=due,
                text=req.text,
            )
        )
        if action_id is None:
            raise HTTPException(status_code=500, detail="Failed to write task")
        return {"ok": True, "id": action_id, "executor": "bot"}

    row = ScheduledAction(
        customer_id=conv.customer_id,
        conversation_id=conv.id,
        channel=conv.channel,
        chat_id=conv.channel_conversation_id,
        action_type="operator_callback",
        executor="human",
        due_at=due,
        status="pending",
        payload={"text": req.text, "by": "admin-ui"},
    )
    db.add(row)
    db.commit()
    return {"ok": True, "id": str(row.id), "executor": "human"}


@router.post("/scheduled-actions/{action_id}/done")
async def scheduled_action_done(
    action_id: str,
    _: None = Depends(_verify_admin_token),
    db: Session = Depends(get_db),
):
    """«Сделано» для человеческих задач из задачника."""
    try:
        aid = UUID(action_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="action_id must be a UUID")
    row = db.get(ScheduledAction, aid)
    if row is None:
        raise HTTPException(status_code=404, detail="Action not found")
    if row.status not in ("pending", "processing"):
        raise HTTPException(status_code=409, detail=f"Уже в статусе {row.status}")
    row.status = "done"
    row.executed_at = datetime.now(timezone.utc)
    db.commit()
    return {"ok": True}


# ============================================================================
# QUICK TRAINING RULES — «лёгкий мозг»: правило одной строкой
# ============================================================================

class QuickRuleRequest(BaseModel):
    rule: str = Field(..., min_length=10, max_length=2000)
    suggested_response: Optional[str] = Field(None, max_length=2000)
    channel: Optional[str] = None


@router.post("/training-rules/quick")
async def training_rule_quick(
    req: QuickRuleRequest,
    _: None = Depends(_verify_admin_token),
    db: Session = Depends(get_db),
):
    """Быстрое правило без LLM-тренера: текст оператора («когда спрашивают X —
    отвечай Y») сохраняется как TrainingCorrection с bge-m3 эмбеддингом —
    тот же retrieval-путь, что у полных коррекций."""
    import asyncio
    from services.training import _get_embedder

    rule = req.rule.strip()
    try:
        embedder = _get_embedder()
        embedding = await asyncio.to_thread(embedder.embed_query, rule[:8000])
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Embedding failed: {e}")

    row = TrainingCorrection(
        trigger_context=rule[:8000],
        correct_guidance=rule,
        suggested_response=(req.suggested_response or None),
        channel=req.channel or None,
        embedding=embedding,
        created_by="admin-ui-quick",
        is_active=True,
    )
    db.add(row)
    db.commit()
    return {"ok": True, "id": str(row.id)}


@router.post("/training-rules/{rule_id}/deactivate")
async def training_rule_deactivate(
    rule_id: str,
    _: None = Depends(_verify_admin_token),
    db: Session = Depends(get_db),
):
    """Выключить правило (soft, версионно — как принято в training_corrections)."""
    try:
        rid = UUID(rule_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="rule_id must be a UUID")
    row = db.get(TrainingCorrection, rid)
    if row is None:
        raise HTTPException(status_code=404, detail="Rule not found")
    row.is_active = False
    db.commit()
    return {"ok": True}


# ============================================================================
# BEHAVIOR — настройки поведения бота (прогрев/нудж) без деплоя
# ============================================================================

class BehaviorSaveRequest(BaseModel):
    values: dict


@router.get("/behavior")
async def behavior_get(_: None = Depends(_verify_admin_token)):
    from services import bot_settings
    return {
        "overrides": bot_settings.get_all(),
        "defaults": {
            "nudge_enabled": True,
            "nudge_after_hours": 1,
            "nudge_max_hours": 36,
            "nudge_text": None,
            "silence_lost_days": 7,
        },
        "known_keys": sorted(bot_settings.KNOWN_KEYS.keys()),
    }


@router.post("/behavior")
async def behavior_save(
    req: BehaviorSaveRequest,
    _: None = Depends(_verify_admin_token),
):
    from services import bot_settings
    try:
        current = bot_settings.set_many(req.values)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {"ok": True, "overrides": current}


# ============================================================================
# NUDGE — пинок зависшему лиду (сейчас / по расписанию / LLM-черновик)
# ============================================================================

class NudgeRequest(BaseModel):
    mode: str = Field(..., pattern="^(now|schedule|draft)$")
    text: Optional[str] = Field(None, max_length=4000)
    due_at: Optional[str] = None  # ISO, для mode=schedule


@router.post("/conversations/{conv_id}/nudge")
async def conversation_nudge(
    conv_id: str,
    req: NudgeRequest,
    _: None = Depends(_verify_admin_token),
    db: Session = Depends(get_db),
):
    import main as _main
    conv, cust = _get_conv_or_404(db, conv_id)

    if req.mode == "draft":
        # LLM-черновик пинка по последним сообщениям — оператор правит и шлёт.
        recent = (
            db.query(Message)
            .filter(Message.conversation_id == conv.id)
            .order_by(Message.created_at.desc())
            .limit(8)
            .all()
        )
        dialog = "\n".join(
            f"{'Лид' if m.role == 'user' else 'Бот'}: {m.content[:300]}"
            for m in reversed(recent) if m.role in ("user", "assistant")
        )
        name = cust.name or "клиент"
        prompt = (
            "Ты — менеджер веб-студии Deadline. Лид замолчал. Напиши ОДНО короткое "
            "(2-3 предложения) тёплое сообщение-пинок на «вы», без давления, "
            "с лёгким вопросом, который легко вернуть в диалог. Без приветствия "
            "«здравствуйте» если диалог уже шёл. Только текст сообщения, ничего больше.\n\n"
            f"Имя лида: {name}\nПоследние сообщения:\n{dialog}"
        )
        try:
            result = await _main.primary_llm.ainvoke(prompt)
            draft = (result.content or "").strip()
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=f"LLM draft failed: {e}")
        return {"ok": True, "draft": draft}

    if not req.text or not req.text.strip():
        raise HTTPException(status_code=422, detail="text is required for mode=now/schedule")
    text = req.text.strip()

    if req.mode == "now":
        from services.operator_actions import deliver_operator_reply, mirror_to_forum
        from services.conversations import append_message
        if conv.channel == "website":
            raise HTTPException(
                status_code=409,
                detail="Website-канал без push — лид не увидит сообщение. "
                       "Дождитесь его возвращения или свяжитесь по email.",
            )
        delivered = await deliver_operator_reply(conv, text, _main.settings)
        # role=assistant: лид видит сообщение «от бота», диалог продолжается естественно.
        append_message(
            db, conv.id, role="assistant", content=text,
            extra_meta={"by": "admin-ui", "kind": "manual_nudge", "delivered": delivered},
        )
        db.commit()
        await mirror_to_forum(conv, f"💻 [Admin UI · пинок от бота] {text}", _main.settings)
        return {"ok": True, "delivered": delivered}

    # mode == "schedule"
    if not req.due_at:
        raise HTTPException(status_code=422, detail="due_at is required for mode=schedule")
    due = _parse_iso(req.due_at)
    if conv.channel != "telegram":
        # run_due_followups сейчас умеет слать только в Telegram.
        raise HTTPException(
            status_code=409,
            detail="Отложенная отправка пока работает только для Telegram-лидов. "
                   "Для этого канала используйте «Отправить сейчас».",
        )
    import asyncio
    from services.scheduled_actions import write_scheduled_action
    action_id, was_new = await asyncio.to_thread(
        lambda: write_scheduled_action(
            customer_id=str(conv.customer_id),
            conversation_id=str(conv.id),
            channel=conv.channel,
            chat_id=conv.channel_conversation_id,
            due_at=due,
            text=text,
        )
    )
    if action_id is None:
        raise HTTPException(status_code=500, detail="Failed to write scheduled action")
    warning = None
    if not _main.settings.crm_enabled:
        warning = ("Крон отложенных действий запускается вместе с CRM (crm_enabled=False) — "
                   "сообщение не уйдёт само. Включите CRM или используйте «Отправить сейчас».")
    return {"ok": True, "scheduled_action_id": action_id, "was_new": was_new, "warning": warning}


# ============================================================================
# BRAIN — системный промпт с версиями
# ============================================================================

class PromptSaveRequest(BaseModel):
    content: str = Field(..., min_length=100)
    comment: Optional[str] = Field(None, max_length=500)


@router.get("/prompt")
async def prompt_get(_: None = Depends(_verify_admin_token)):
    from services.prompt_store import get_active_system_prompt
    from prompts import SYSTEM_PROMPT
    db_prompt = None
    try:
        db_prompt = get_active_system_prompt()
    except Exception:  # noqa: BLE001
        pass
    return {
        "source": "db" if db_prompt else "file",
        "content": db_prompt or SYSTEM_PROMPT,
        "default_content": SYSTEM_PROMPT,
    }


@router.get("/prompt/versions")
async def prompt_versions(
    _: None = Depends(_verify_admin_token),
    db: Session = Depends(get_db),
):
    rows = (
        db.query(PromptVersion)
        .filter(PromptVersion.kind == "system_prompt")
        .order_by(PromptVersion.created_at.desc())
        .limit(50)
        .all()
    )
    return {
        "items": [
            {
                "id": str(r.id),
                "is_active": r.is_active,
                "comment": r.comment,
                "created_by": r.created_by,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "preview": (r.content or "")[:200],
            }
            for r in rows
        ],
    }


@router.post("/prompt")
async def prompt_save(
    req: PromptSaveRequest,
    _: None = Depends(_verify_admin_token),
):
    from services.prompt_store import validate_prompt_template, set_active_system_prompt
    problems = validate_prompt_template(req.content)
    if problems:
        raise HTTPException(status_code=422, detail={"problems": problems})
    version_id = set_active_system_prompt(req.content, created_by="admin-ui", comment=req.comment)
    return {"ok": True, "version_id": version_id}


class PromptActivateRequest(BaseModel):
    version_id: Optional[str] = None  # None → откат на заводскую константу


@router.post("/prompt/activate")
async def prompt_activate(
    req: PromptActivateRequest,
    _: None = Depends(_verify_admin_token),
):
    from services.prompt_store import activate_version, deactivate_all
    if req.version_id is None:
        deactivate_all()
        return {"ok": True, "source": "file"}
    try:
        ok = activate_version(req.version_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="version_id must be a UUID")
    if not ok:
        raise HTTPException(status_code=404, detail="Version not found")
    return {"ok": True, "source": "db"}


class PromptTestRequest(BaseModel):
    content: str
    sample_question: str = "Сколько стоит сайт?"


@router.post("/prompt/test")
async def prompt_test(
    req: PromptTestRequest,
    _: None = Depends(_verify_admin_token),
):
    """Dry-run: валидация + сборка format() с заглушками. БЕЗ LLM-вызова
    (быстро и бесплатно); LLM-проверку оператор делает в реальном чате."""
    from services.prompt_store import validate_prompt_template
    problems = validate_prompt_template(req.content)
    if problems:
        return {"ok": False, "problems": problems}
    rendered = req.content.format(
        context="[контекст KB]",
        history="[история диалога]",
        question=req.sample_question,
        corrections="[уроки коррекций]",
        handoff_block="[handoff-блок]",
    )
    return {"ok": True, "rendered_chars": len(rendered), "rendered_preview": rendered[:1500]}


# ============================================================================
# TRAINING RULES — read-only список (управление через /admin/training/*)
# ============================================================================

@router.get("/training-rules")
async def training_rules(
    _: None = Depends(_verify_admin_token),
    db: Session = Depends(get_db),
):
    rows = (
        db.query(TrainingCorrection)
        .filter(TrainingCorrection.is_active == True)  # noqa: E712
        .order_by(TrainingCorrection.created_at.desc())
        .limit(100)
        .all()
    )
    return {
        "items": [
            {
                "id": str(r.id),
                "guidance": r.correct_guidance,
                "suggested_response": r.suggested_response,
                "channel": r.channel,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "created_by": r.created_by,
            }
            for r in rows
        ],
    }


# ============================================================================
# TASKS — scheduled actions
# ============================================================================

@router.get("/scheduled-actions")
async def scheduled_actions_list(
    status: str = "pending",
    _: None = Depends(_verify_admin_token),
    db: Session = Depends(get_db),
):
    rows = (
        db.query(ScheduledAction, Customer)
        .join(Customer, ScheduledAction.customer_id == Customer.id)
        .filter(ScheduledAction.status == status)
        .order_by(ScheduledAction.due_at.asc())
        .limit(100)
        .all()
    )
    return {
        "items": [
            {
                "id": str(a.id),
                "action_type": a.action_type,
                "executor": a.executor,
                "status": a.status,
                "due_at": a.due_at.isoformat() if a.due_at else None,
                "channel": a.channel,
                "attempts": a.attempts,
                "payload": a.payload,
                "conversation_id": str(a.conversation_id) if a.conversation_id else None,
                "customer": {"id": str(c.id), "name": c.name, "email": c.email},
            }
            for a, c in rows
        ],
    }


@router.post("/scheduled-actions/{action_id}/cancel")
async def scheduled_action_cancel(
    action_id: str,
    _: None = Depends(_verify_admin_token),
    db: Session = Depends(get_db),
):
    try:
        aid = UUID(action_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="action_id must be a UUID")
    row = db.get(ScheduledAction, aid)
    if row is None:
        raise HTTPException(status_code=404, detail="Action not found")
    if row.status != "pending":
        raise HTTPException(status_code=409, detail=f"Only pending actions can be cancelled (status={row.status})")
    row.status = "cancelled"
    db.commit()
    return {"ok": True}


@router.post("/cron/sweep")
async def cron_sweep(_: None = Depends(_verify_admin_token)):
    """Кнопка «прогнать сейчас» — тот же код, что /admin/cron/sweep."""
    from services.cron import sweep_once
    from services.scheduled_actions import run_due_followups, run_due_call_reminders
    out = {}
    try:
        out["sweep"] = await sweep_once(tenant_config={})
    except Exception as e:  # noqa: BLE001
        out["sweep"] = {"error": str(e)}
    try:
        out["followups"] = await run_due_followups(tenant_config=None)
    except Exception as e:  # noqa: BLE001
        out["followups"] = {"error": str(e)}
    try:
        out["call_reminders"] = await run_due_call_reminders(tenant_config=None)
    except Exception as e:  # noqa: BLE001
        out["call_reminders"] = {"error": str(e)}
    return out


# ============================================================================
# SETTINGS / KB — read-only, санитизировано
# ============================================================================

@router.get("/settings")
async def settings_view(_: None = Depends(_verify_admin_token)):
    import main as _main
    s = _main.settings
    return {
        "llm": {
            "provider": _main._LLM_PROVIDER,
            "model": _main._LLM_PRIMARY_MODEL,
            "fallback_model": _main._LLM_FALLBACK_MODEL,
        },
        "crm": {"enabled": s.crm_enabled, "provider": s.crm_provider,
                "hubspot_portal_configured": bool(s.hubspot_portal_id)},
        "channels": {
            "telegram_configured": bool(s.telegram_bot_token),
            "meta_configured": bool(s.meta_page_access_token),
            "operator_group_configured": bool(s.telegram_operator_group_id),
            "voice_transcription": bool(s.groq_api_key),
        },
        "tenant": {
            "slug": _main.tenant.slug,
            "display_name": _main.tenant.display_name,
            "languages": _main.tenant.languages,
        },
    }


@router.get("/kb")
async def kb_view(
    _: None = Depends(_verify_admin_token),
    db: Session = Depends(get_db),
):
    rows = db.execute(
        sql_select(KBChunk.source, sql_func.count()).group_by(KBChunk.source).order_by(KBChunk.source)
    ).fetchall()
    return {"sources": [{"source": r[0], "chunks": int(r[1])} for r in rows]}


# ============================================================================
# helpers
# ============================================================================

def _get_conv_or_404(db: Session, conv_id: str):
    try:
        cid = UUID(conv_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="conversation id must be a UUID")
    row = (
        db.query(Conversation, Customer)
        .join(Customer, Conversation.customer_id == Customer.id)
        .filter(Conversation.id == cid)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return row


def _parse_iso(value: str) -> datetime:
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Bad ISO datetime: {value!r}")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt
