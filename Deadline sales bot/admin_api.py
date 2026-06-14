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
    AutomationRule,
    AutomationRun,
    CustomFieldDef,
    StageTransition,
    WorkspaceMember,
)

log = logging.getLogger("deadline-bot.admin-api")

router = APIRouter(prefix="/admin/api", tags=["admin-ui"])


# ============================================================================
# AUTH
# ============================================================================

def _extract_bearer(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Bearer token required")
    return auth.split(None, 1)[1].strip()


def _verify_member(request: Request, db: Session = Depends(get_db)) -> dict:
    """Роли (2026-06-12): owner — главный токен из env (ADMIN_UI_TOKEN /
    TRAINING_AUTH_TOKEN, fail-closed 503 если не заданы); менеджер — именной
    токен из workspace_members (sha256-хэш, active=True). Возвращает
    {"role", "name"} для эндпоинта."""
    import hashlib
    import os
    import main as _main

    expected = os.getenv("ADMIN_UI_TOKEN") or _main.settings.training_auth_token
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="Admin UI disabled — set ADMIN_UI_TOKEN (or TRAINING_AUTH_TOKEN) in env.",
        )
    token = _extract_bearer(request)
    if hmac.compare_digest(token.encode("utf-8"), expected.encode("utf-8")):
        return {"role": "owner", "name": "owner"}

    th = hashlib.sha256(token.encode("utf-8")).hexdigest()
    row = (
        db.query(WorkspaceMember)
        .filter(WorkspaceMember.token_hash == th, WorkspaceMember.active == True)  # noqa: E712
        .first()
    )
    if row is None:
        raise HTTPException(status_code=403, detail="Invalid token")
    try:
        row.last_seen_at = datetime.now(timezone.utc)
        db.commit()
    except Exception:  # noqa: BLE001 — метка посещения не критична
        db.rollback()
    return {"role": row.role or "manager", "name": row.name}


def _verify_owner(request: Request, db: Session = Depends(get_db)) -> dict:
    """Owner-only эндпоинты (Мозг, Автоматизации-правка, Настройки, Команда…)."""
    member = _verify_member(request, db)
    if member["role"] != "owner":
        raise HTTPException(status_code=403, detail="Доступно только владельцу")
    return member


@router.get("/me")
async def me(member: dict = Depends(_verify_member)):
    import main as _main
    from services import bot_settings
    ws = bot_settings.get_all()
    return {
        "ok": True,
        "tenant": _main.tenant.slug,
        "display_name": ws.get("business_name") or _main.tenant.display_name,
        "onboarding_done": bool(ws.get("onboarding_done", False)),
        "logo_url": ws.get("logo_url"),
        "accent_color": ws.get("accent_color"),
        "role": member["role"],
        "member_name": member["name"],
    }


# ============================================================================
# TEAM — команда (owner-only): именные токены менеджеров
# ============================================================================

class TeamCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    department: Optional[str] = Field(None, max_length=40)
    telegram_chat_id: Optional[str] = Field(None, max_length=40)


@router.get("/team")
async def team_list(
    _: dict = Depends(_verify_owner),
    db: Session = Depends(get_db),
):
    rows = db.query(WorkspaceMember).order_by(WorkspaceMember.created_at.asc()).all()
    return {
        "items": [
            {
                "id": str(r.id), "name": r.name, "role": r.role, "active": r.active,
                "department": r.department, "telegram_chat_id": r.telegram_chat_id,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "last_seen_at": r.last_seen_at.isoformat() if r.last_seen_at else None,
            }
            for r in rows
        ],
    }


@router.post("/team")
async def team_create(
    req: TeamCreateRequest,
    _: dict = Depends(_verify_owner),
    db: Session = Depends(get_db),
):
    """Создать менеджера. Токен возвращается ОДИН раз — храним только хэш."""
    import hashlib
    import secrets
    token = "mgr_" + secrets.token_urlsafe(24)
    row = WorkspaceMember(
        name=req.name.strip(), role="manager",
        token_hash=hashlib.sha256(token.encode("utf-8")).hexdigest(),
        active=True,
        department=(req.department or "").strip() or None,
        telegram_chat_id=(req.telegram_chat_id or "").strip() or None,
    )
    db.add(row)
    db.commit()
    return {"ok": True, "id": str(row.id), "token": token,
            "note": "Передайте токен менеджеру — он вводит его на экране входа. Повторно показать нельзя."}


@router.post("/team/{member_id}/toggle")
async def team_toggle(
    member_id: str,
    _: dict = Depends(_verify_owner),
    db: Session = Depends(get_db),
):
    row = db.get(WorkspaceMember, _uuid_or_422(member_id))
    if row is None:
        raise HTTPException(status_code=404, detail="Member not found")
    row.active = not row.active
    db.commit()
    return {"ok": True, "active": row.active}


class TeamUpdateRequest(BaseModel):
    department: Optional[str] = Field(None, max_length=40)
    telegram_chat_id: Optional[str] = Field(None, max_length=40)


@router.post("/team/{member_id}/update")
async def team_update(
    member_id: str,
    req: TeamUpdateRequest,
    _: dict = Depends(_verify_owner),
    db: Session = Depends(get_db),
):
    """Задать отдел и/или личный Telegram chat сотрудника (для назначений/уведомлений)."""
    row = db.get(WorkspaceMember, _uuid_or_422(member_id))
    if row is None:
        raise HTTPException(status_code=404, detail="Member not found")
    if req.department is not None:
        row.department = req.department.strip() or None
    if req.telegram_chat_id is not None:
        row.telegram_chat_id = req.telegram_chat_id.strip() or None
    db.commit()
    return {"ok": True}


# ============================================================================
# OVERVIEW — данные для канваса
# ============================================================================

CHANNELS = ("website", "telegram", "instagram", "messenger")


@router.get("/overview")
async def overview(
    _: None = Depends(_verify_member),
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
        # ID диалога на канале (для WhatsApp — номер/скрытый @lid). Чтобы карточка
        # показывала контакт, даже если customer.phone ещё не заполнен.
        "channel_conversation_id": conv.channel_conversation_id,
        # WhatsApp-триаж: лид/не-лид + причина (NULL если не классифицирован).
        "wa_classification": getattr(conv, "wa_classification", None),
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
    _: None = Depends(_verify_member),
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
    _: None = Depends(_verify_member),
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

    # Кастомные поля: определения + значения из profile_data['fields'].
    field_defs = (
        db.query(CustomFieldDef)
        .filter(CustomFieldDef.active == True)  # noqa: E712
        .order_by(CustomFieldDef.position.asc())
        .all()
    )
    field_values = ((cust.profile_data or {}).get("fields") or {})

    out = _conv_summary_row(conv, cust, None)
    out.update({
        "fields": [
            {
                "key": f.key, "label": f.label, "field_type": f.field_type,
                "options": f.options, "value": field_values.get(f.key),
            }
            for f in field_defs
        ],
        "summary": conv.summary,
        "forum_topic_id": conv.forum_topic_id,
        "crm_deal_id": conv.crm_deal_id,
        "crm_contact_id": cust.crm_contact_id,
        # WhatsApp: предложенный ботом ответ на одобрение (режим наблюдения/черновика)
        # + флаг «бот ведёт этот диалог сам».
        "pending_wa_draft": conv.pending_wa_draft,
        "wa_autonomous": bool(getattr(conv, "wa_autonomous", False)),
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
    _: None = Depends(_verify_member),
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
    _: None = Depends(_verify_member),
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
    _: None = Depends(_verify_member),
    db: Session = Depends(get_db),
):
    import main as _main
    from services.operator_actions import set_takeover_with_mirror

    conv, _cust = _get_conv_or_404(db, conv_id)
    await set_takeover_with_mirror(db, conv, req.on, _main.settings, source="admin-ui")
    return {"ok": True, "operator_takeover": req.on}


# ============================================================================
# WhatsApp draft approval — одобрить/отклонить предложенный ботом ответ прямо в
# карточке диалога; «разрешить боту вести диалог сам» (per-conversation auto).
# ============================================================================

class WaDraftActionRequest(BaseModel):
    action: str = Field(..., pattern="^(send|reject)$")
    text: Optional[str] = Field(None, max_length=4000)  # переопределённый текст при send


@router.post("/conversations/{conv_id}/wa-draft")
async def conversation_wa_draft(
    conv_id: str,
    req: WaDraftActionRequest,
    _: None = Depends(_verify_member),
    db: Session = Depends(get_db),
):
    import main as _main
    from channels.whatsapp import send_whatsapp_reply
    from services.conversations import append_message

    conv, _cust = _get_conv_or_404(db, conv_id)
    pending = conv.pending_wa_draft
    if not pending:
        raise HTTPException(status_code=404, detail="Нет предложенного ответа для этого диалога")

    if req.action == "reject":
        conv.pending_wa_draft = None
        db.commit()
        return {"ok": True, "sent": False}

    text = (req.text or pending.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Пустой текст ответа")
    delivered = await _main._wa_send(
        pending.get("to_wa_id") or conv.channel_conversation_id or "",
        text,
        pending.get("phone_number_id") or "",
    )
    append_message(db, conv.id, role="assistant", content=text,
                   extra_meta={"approved_via": "admin-ui", "delivered": delivered})
    conv.pending_wa_draft = None
    db.commit()
    return {"ok": True, "sent": True, "delivered": delivered}


class WaAutonomousRequest(BaseModel):
    on: bool


@router.post("/conversations/{conv_id}/wa-autonomous")
async def conversation_wa_autonomous(
    conv_id: str,
    req: WaAutonomousRequest,
    _: None = Depends(_verify_member),
    db: Session = Depends(get_db),
):
    """Разрешить/запретить боту вести ЭТОТ диалог сам (override над глобальным
    режимом). При включении, если есть ожидающий черновик — сразу отправляем его
    («бот начинает общение»)."""
    import main as _main
    from channels.whatsapp import send_whatsapp_reply
    from services.conversations import append_message

    conv, _cust = _get_conv_or_404(db, conv_id)
    conv.wa_autonomous = bool(req.on)
    sent = False
    delivered = False
    if req.on and conv.pending_wa_draft:
        pending = conv.pending_wa_draft
        text = (pending.get("text") or "").strip()
        if text:
            delivered = await _main._wa_send(
                pending.get("to_wa_id") or conv.channel_conversation_id or "",
                text,
                pending.get("phone_number_id") or "",
            )
            append_message(db, conv.id, role="assistant", content=text,
                           extra_meta={"approved_via": "admin-ui-autonomous", "delivered": delivered})
            sent = True
        conv.pending_wa_draft = None
    db.commit()
    return {"ok": True, "wa_autonomous": conv.wa_autonomous, "sent": sent, "delivered": delivered}


# ============================================================================
# WHATSAPP HISTORY SYNC — подтянуть ВСЕ существующие переписки из WAHA-стора
# в БД + классифицировать лид/не-лид. Фоновая задача (импорт может идти минуты),
# прогресс отдаётся через GET /whatsapp/status.
# ============================================================================

# Состояние последней/текущей синхронизации (in-memory, на процесс).
_WA_SYNC_STATE: dict = {
    "running": False, "started_at": None, "finished_at": None,
    "stats": None, "error": None,
}


async def _run_wa_sync_bg(max_chats: int, per_chat: int, classify: bool,
                          reconcile: bool = False) -> None:
    """Фоновый прогон: своя DB-сессия (не request-scoped), пишет прогресс в
    _WA_SYNC_STATE. Любая ошибка ловится — состояние не зависает в running."""
    import main as _main
    from datetime import datetime, timezone
    from db.connection import session_scope
    from services.whatsapp_sync import sync_waha_history

    _WA_SYNC_STATE.update({
        "running": True, "started_at": datetime.now(timezone.utc).isoformat(),
        "finished_at": None, "stats": None, "error": None,
    })
    try:
        with session_scope() as db:
            stats = await sync_waha_history(
                db, _main.settings, llm=_main.handoff_llm,
                max_chats=max_chats, per_chat_messages=per_chat, classify=classify,
                reconcile=reconcile,
            )
        _WA_SYNC_STATE["stats"] = stats
    except Exception as e:  # noqa: BLE001
        log.error(f"[wa-sync] background run failed: {e}")
        _WA_SYNC_STATE["error"] = str(e)
    finally:
        from datetime import datetime as _dt, timezone as _tz
        _WA_SYNC_STATE["running"] = False
        _WA_SYNC_STATE["finished_at"] = _dt.now(_tz.utc).isoformat()


class WaSyncRequest(BaseModel):
    max_chats: int = 300
    per_chat_messages: int = 80
    classify: bool = True
    reconcile: bool = False


@router.post("/whatsapp/sync")
async def whatsapp_sync(
    req: WaSyncRequest,
    _: None = Depends(_verify_member),
):
    """Запустить импорт всех WhatsApp-переписок из WAHA (в фоне). Идемпотентно:
    уже импортированные сообщения пропускаются (дедуп по waha_id)."""
    import asyncio
    if _WA_SYNC_STATE.get("running"):
        return {"ok": True, "already_running": True, "state": _WA_SYNC_STATE}
    asyncio.create_task(_run_wa_sync_bg(
        max(1, min(req.max_chats, 1000)),
        max(1, min(req.per_chat_messages, 300)),
        bool(req.classify),
        bool(req.reconcile),
    ))
    return {"ok": True, "started": True}


@router.get("/whatsapp/status")
async def whatsapp_status(
    _: None = Depends(_verify_member),
):
    """Статус WhatsApp-подключения (WAHA-сессия) + прогресс последней синхронизации.
    Сообщает, готов ли стор истории (можно ли тянуть существующие чаты)."""
    import main as _main
    from channels.waha import fetch_waha_session_status, fetch_waha_chats, WahaHistoryUnavailable

    s = _main.settings
    base = getattr(s, "waha_base_url", None)
    out = {
        "configured": bool(base),
        "session": None,
        "session_status": None,
        "me": None,
        "history_ready": False,
        "history_hint": None,
        "sync": _WA_SYNC_STATE,
    }
    if not base:
        out["history_hint"] = "WAHA не настроен (нет WAHA_BASE_URL)."
        return out

    sess = await fetch_waha_session_status(base, s.waha_api_key or "", s.waha_session or "default")
    out["session"] = s.waha_session or "default"
    out["session_status"] = sess.get("status")
    out["me"] = sess.get("me")

    # Пробуем тронуть history-эндпоинт — определяем, включён ли стор.
    if sess.get("status") == "WORKING":
        try:
            chats = await fetch_waha_chats(base, s.waha_api_key or "", s.waha_session or "default", limit=1)
            out["history_ready"] = True
            out["history_hint"] = f"Стор истории доступен (видно чатов в выборке: {len(chats)})."
        except WahaHistoryUnavailable as e:
            out["history_ready"] = False
            out["history_hint"] = (
                "Сессия подключена, но стор истории WAHA выключен — существующие "
                "чаты пока не подтянуть (нужно включить NOWEB-стор и пересканировать QR). "
                "Новые входящие сообщения уже сохраняются."
            )
            log.info(f"[wa-status] history unavailable: {e}")
    else:
        out["history_hint"] = "Сессия не подключена (нужен QR)."
    return out


# ============================================================================
# WHATSAPP — импорт уже разобранных лидов (из прошлой сессии-анализа переписок).
# Каждый лид → карточка бота: стадия + бейдж лид + ПРЕДЛОЖЕННЫЙ ответ (всплывает
# с кнопкой ✅) + сводка (что нужно / что делаем / демо). Ключ — телефон (цифры):
# будущие живые сообщения из WAHA приклеятся к ТОЙ ЖЕ карточке (channel_conversation_id).
# Идемпотентно: повторный импорт обновляет ту же карточку, не плодит дубли.
# ============================================================================

_WA_STAGES = {
    "new_lead", "in_dialog", "qualified", "nda", "on_call", "tz_approved",
    "proposal", "prepayment", "in_work", "completed_won", "post_sale", "lost",
}
_WA_TEMPS = {"cold", "warm", "hot", "ready", "client", "frozen"}


class WaLeadIn(BaseModel):
    phone: str
    name: Optional[str] = None
    need: Optional[str] = None            # «Нужно»
    stage: Optional[str] = None           # lead_stage бота
    temperature: Optional[str] = None     # cold/warm/hot/ready/...
    category: Optional[str] = None
    note: Optional[str] = None            # «Делаем»
    demo_url: Optional[str] = None
    suggested_reply: Optional[str] = None # «✍️ Ответ» — ляжет в pending_wa_draft


class WaImportRequest(BaseModel):
    leads: list[WaLeadIn]
    source: str = "fable-session-2026-06-14"


@router.post("/whatsapp/import-leads")
async def whatsapp_import_leads(
    req: WaImportRequest,
    _: None = Depends(_verify_member),
    db: Session = Depends(get_db),
):
    import re as _re
    from datetime import datetime, timezone
    from services.identity import resolve_or_create_customer
    from services.conversations import get_or_create_conversation

    imported, updated, skipped = 0, 0, 0
    items = []
    for lead in req.leads:
        digits = _re.sub(r"\D", "", lead.phone or "")
        if not digits:
            skipped += 1
            continue
        existed = db.query(Conversation).filter(
            Conversation.channel == "whatsapp",
            Conversation.channel_conversation_id == digits,
        ).first() is not None

        customer = resolve_or_create_customer(
            db, channel="whatsapp", external_id=digits, username=lead.name,
        )
        if lead.name and not (customer.name or "").strip():
            customer.name = lead.name[:200]
        if not (customer.phone or "").strip():
            customer.phone = ("+" + digits)[:50]
        if lead.temperature in _WA_TEMPS:
            customer.lead_temperature = lead.temperature
        prof = dict(customer.profile_data or {})
        prof.update({k: v for k, v in {
            "wa_need": lead.need, "wa_demo_url": lead.demo_url,
            "wa_category": lead.category, "import_source": req.source,
        }.items() if v})
        customer.profile_data = prof
        db.flush()

        conv = get_or_create_conversation(
            db, customer_id=customer.id, channel="whatsapp",
            channel_conversation_id=digits,
        )
        if lead.stage in _WA_STAGES:
            conv.lead_stage = lead.stage
        # Сводка для карточки (видно в детали диалога).
        summary_bits = []
        if lead.need: summary_bits.append(f"Нужно: {lead.need}")
        if lead.note: summary_bits.append(f"Делаем: {lead.note}")
        if lead.demo_url: summary_bits.append(f"Демо: {lead.demo_url}")
        if summary_bits:
            conv.summary = " · ".join(summary_bits)[:2000]
        conv.wa_classification = {
            "is_lead": True, "confidence": 1.0,
            "category": lead.category or "service_inquiry",
            "reason": lead.need or "Импорт разбора переписок",
            "temperature": lead.temperature or (customer.lead_temperature or "warm"),
            "demo_url": lead.demo_url, "note": lead.note,
            "by": "fable-import", "source": req.source,
            "classified_at": datetime.now(timezone.utc).isoformat(),
        }
        # Предложенный ответ — всплывёт в карточке с кнопкой ✅ Отправить.
        if lead.suggested_reply:
            conv.pending_wa_draft = {
                "text": lead.suggested_reply,
                "phone_number_id": "",
                "to_wa_id": digits,
                "client_msg": (lead.need or "")[:500],
                "source": req.source,
                "ts": datetime.now(timezone.utc).isoformat(),
            }
        db.flush()
        if existed:
            updated += 1
        else:
            imported += 1
        items.append({"phone": digits, "name": customer.name, "stage": conv.lead_stage,
                      "conversation_id": str(conv.id), "had_reply": bool(lead.suggested_reply)})

    db.commit()
    return {"ok": True, "imported": imported, "updated": updated,
            "skipped": skipped, "items": items}


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
    _: None = Depends(_verify_member),
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
    # История воронки (для конверсионной аналитики) + аудит в самом диалоге.
    db.add(StageTransition(
        conversation_id=conv.id, customer_id=conv.customer_id,
        from_stage=from_stage, to_stage=req.to_stage, by="admin",
    ))
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
    _: None = Depends(_verify_member),
    db: Session = Depends(get_db),
):
    from services import funnel_store
    return {"items": funnel_store.get_stages(db), "custom": _stages_customized(db)}


@router.post("/funnel/stages")
async def funnel_stages_save(
    req: StagesSaveRequest,
    _: None = Depends(_verify_owner),
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
    _: None = Depends(_verify_owner),
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
    _: None = Depends(_verify_member),
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
    _: None = Depends(_verify_member),
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
    _: None = Depends(_verify_member),
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
    _: None = Depends(_verify_owner),
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


class LearnFromMessageRequest(BaseModel):
    conversation_id: str
    message_id: str


@router.post("/training-rules/from-message")
async def training_rule_from_message(
    req: LearnFromMessageRequest,
    _: dict = Depends(_verify_owner),
    db: Session = Depends(get_db),
):
    """«Бот, учись у меня»: оператор перехватил и ответил по-своему → один клик
    превращает его ответ в правило. trigger_context = последние реплики диалога
    ДО ответа (по ним retrieval найдёт похожую ситуацию), suggested_response =
    ответ оператора. Без LLM — мгновенно и предсказуемо."""
    import asyncio
    from services.training import _get_embedder

    conv, _cust = _get_conv_or_404(db, req.conversation_id)
    msg = db.get(Message, _uuid_or_422(req.message_id))
    if msg is None or msg.conversation_id != conv.id:
        raise HTTPException(status_code=404, detail="Message not found in this conversation")
    if msg.role != "operator":
        raise HTTPException(status_code=422, detail="Учимся только на ответах оператора")

    # Контекст: до 6 реплик user/assistant ПЕРЕД ответом оператора.
    prior = (
        db.query(Message)
        .filter(Message.conversation_id == conv.id,
                Message.created_at < msg.created_at,
                Message.role.in_(("user", "assistant")))
        .order_by(Message.created_at.desc())
        .limit(6)
        .all()
    )
    dialog = "\n".join(
        f"{'user' if m.role == 'user' else 'assistant'}: {m.content[:400]}"
        for m in reversed(prior)
    ) or f"user: {msg.content[:200]}"

    try:
        embedder = _get_embedder()
        embedding = await asyncio.to_thread(embedder.embed_query, dialog[:8000])
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Embedding failed: {e}")

    row = TrainingCorrection(
        trigger_context=dialog[:8000],
        correct_guidance=("В похожей ситуации отвечай в духе ответа оператора "
                          "(тон и суть, не дословно)."),
        suggested_response=msg.content[:2000],
        channel=conv.channel,
        embedding=embedding,
        created_by="learn-from-operator",
        source_conversation_id=conv.id,
        is_active=True,
    )
    db.add(row)
    db.commit()
    return {"ok": True, "id": str(row.id)}


@router.post("/training-rules/{rule_id}/deactivate")
async def training_rule_deactivate(
    rule_id: str,
    _: None = Depends(_verify_owner),
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
async def behavior_get(_: None = Depends(_verify_owner)):
    from services import bot_settings
    return {
        "overrides": bot_settings.get_all(),
        "defaults": {
            "nudge_enabled": True,
            "nudge_after_hours": 1,
            "nudge_max_hours": 36,
            "nudge_text": None,
            "silence_lost_days": 7,
            "bot_goal": "call",
            "digest_enabled": True,
            "digest_hour": 8,
            "digest_tz_offset": 7,
        },
        "known_keys": sorted(bot_settings.KNOWN_KEYS.keys()),
    }


@router.post("/behavior")
async def behavior_save(
    req: BehaviorSaveRequest,
    _: None = Depends(_verify_owner),
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

@router.post("/conversations/{conv_id}/advise")
async def conversation_advise(
    conv_id: str,
    _: None = Depends(_verify_member),
    db: Session = Depends(get_db),
):
    """AI-копилот оператора (агент-слой): по состоянию лида (стадия / score /
    температура / канал) + переписке рекомендует ЛУЧШЕЕ следующее действие и даёт
    готовый черновик ответа. Только подсказка — ничего не отправляет и не меняет."""
    import main as _main
    conv, cust = _get_conv_or_404(db, conv_id)
    recent = (
        db.query(Message)
        .filter(Message.conversation_id == conv.id)
        .order_by(Message.created_at.desc())
        .limit(12)
        .all()
    )
    dialog = "\n".join(
        f"{'Лид' if m.role == 'user' else 'Бот'}: {m.content[:300]}"
        for m in reversed(recent) if m.role in ("user", "assistant")
    )
    name = cust.name or "клиент"
    state = (
        f"Имя: {name}; стадия воронки: {conv.lead_stage}; "
        f"score: {getattr(cust, 'lead_score', 0)}; "
        f"температура: {getattr(cust, 'lead_temperature', 'cold')}; "
        f"канал: {conv.channel}"
    )
    prompt = (
        "Ты — старший менеджер по продажам веб-студии Deadline и наставник оператора. "
        "По состоянию лида и переписке дай КОРОТКО и по делу:\n"
        "1) РЕКОМЕНДАЦИЯ — одно лучшее следующее действие (1-2 предложения: напр. "
        "«предложить 2 слота на созвон», «назвать цену от $X и звать на звонок», "
        "«лид холодный — мягкий прогрев», «передать менеджеру»).\n"
        "2) ЧЕРНОВИК — готовое сообщение лиду на «вы» (2-4 предложения), реализующее "
        "рекомендацию.\n"
        "Ответь СТРОГО в формате:\nРЕКОМЕНДАЦИЯ: <...>\nЧЕРНОВИК: <...>\n\n"
        f"СОСТОЯНИЕ ЛИДА: {state}\nПЕРЕПИСКА:\n{dialog}"
    )
    try:
        result = await _main.primary_llm.ainvoke(prompt)
        raw = (result.content or "").strip()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"LLM advise failed: {e}")
    action, draft = raw, ""
    if "ЧЕРНОВИК" in raw:
        parts = raw.split("ЧЕРНОВИК", 1)
        action = parts[0].replace("РЕКОМЕНДАЦИЯ:", "").replace("РЕКОМЕНДАЦИЯ", "").strip(" :\n")
        draft = parts[1].lstrip(" :\n").strip()
    return {"ok": True, "action": action, "draft": draft}


class AssignRequest(BaseModel):
    member_id: Optional[str] = None  # None / пусто = снять назначение


@router.post("/conversations/{conv_id}/assign")
async def assign_conversation(
    conv_id: str,
    req: AssignRequest,
    _: None = Depends(_verify_member),
    db: Session = Depends(get_db),
):
    """P3b — назначить лид на сотрудника (или снять). Уведомляет сотрудника лично
    в Telegram, если у него задан telegram_chat_id."""
    import logging as _lg
    conv, cust = _get_conv_or_404(db, conv_id)
    if not req.member_id:
        conv.assigned_member_id = None
        db.commit()
        return {"ok": True, "assigned": None}
    member = db.get(WorkspaceMember, _uuid_or_422(req.member_id))
    if member is None:
        raise HTTPException(status_code=404, detail="Сотрудник не найден")
    conv.assigned_member_id = member.id
    db.commit()
    try:
        if member.telegram_chat_id:
            import main as _main
            from channels.telegram import send_telegram_reply
            if _main.settings.telegram_bot_token:
                nm = cust.name or cust.email or "лид"
                txt = (f"📋 Вам назначен лид: {nm}\n"
                       f"Стадия: {conv.lead_stage} · канал: {conv.channel}")
                await send_telegram_reply(_main.settings.telegram_bot_token, member.telegram_chat_id, txt)
    except Exception as e:  # noqa: BLE001
        _lg.getLogger("admin").warning("assign notify failed: %s", e)
    return {"ok": True, "assigned": {"id": str(member.id), "name": member.name}}


class RecurrenceRequest(BaseModel):
    every_days: Optional[int] = None
    note: Optional[str] = Field(None, max_length=2000)
    active: bool = True


@router.post("/conversations/{conv_id}/recurrence")
async def set_recurrence(
    conv_id: str,
    req: RecurrenceRequest,
    _: None = Depends(_verify_member),
    db: Session = Depends(get_db),
):
    """P6 — пометить клиента регулярным (постоянный клининг / ТО). Хранится в
    profile_data['recurrence']; крон (run_due_recurring) шлёт плановое напоминание
    каждые every_days. active=False или пустой every_days → снять регулярность."""
    from datetime import datetime, timezone, timedelta
    conv, cust = _get_conv_or_404(db, conv_id)
    prof = dict(cust.profile_data or {})
    rec = None
    if req.active and req.every_days and int(req.every_days) >= 1:
        rec = {
            "active": True,
            "every_days": int(req.every_days),
            "note": (req.note or "").strip() or None,
            "next_at": (datetime.now(timezone.utc) + timedelta(days=int(req.every_days))).isoformat(),
        }
        prof["recurrence"] = rec
    else:
        prof.pop("recurrence", None)
    cust.profile_data = prof
    db.commit()
    return {"ok": True, "recurrence": rec}


class NudgeRequest(BaseModel):
    mode: str = Field(..., pattern="^(now|schedule|draft)$")
    text: Optional[str] = Field(None, max_length=4000)
    due_at: Optional[str] = None  # ISO, для mode=schedule


@router.post("/conversations/{conv_id}/nudge")
async def conversation_nudge(
    conv_id: str,
    req: NudgeRequest,
    _: None = Depends(_verify_member),
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
async def prompt_get(_: None = Depends(_verify_owner)):
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
    _: None = Depends(_verify_owner),
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
    _: None = Depends(_verify_owner),
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
    _: None = Depends(_verify_owner),
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
    _: None = Depends(_verify_owner),
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
    _: None = Depends(_verify_owner),
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
    _: None = Depends(_verify_member),
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
    _: None = Depends(_verify_member),
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
async def cron_sweep(_: None = Depends(_verify_owner)):
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
async def settings_view(_: None = Depends(_verify_member)):
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
    _: None = Depends(_verify_member),
    db: Session = Depends(get_db),
):
    rows = db.execute(
        sql_select(KBChunk.source, sql_func.count()).group_by(KBChunk.source).order_by(KBChunk.source)
    ).fetchall()
    return {"sources": [{"source": r[0], "chunks": int(r[1])} for r in rows]}


class KbUploadRequest(BaseModel):
    source: str = Field(..., max_length=120)
    content: str = Field(..., max_length=200_000)


@router.post("/kb/upload")
async def kb_upload(req: KbUploadRequest, _: None = Depends(_verify_owner)):
    """Рантайм-загрузка одного документа в базу знаний (аддитивно, без переингеста
    всей базы и без деплоя). Заменяет прежние чанки этого source."""
    import asyncio
    from services.kb_ingest import ingest_text
    src = (req.source or "").strip() or "upload"
    if not src.endswith(".md"):
        src += ".md"
    try:
        n = await asyncio.to_thread(ingest_text, src, req.content)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"KB ingest failed: {e}")
    return {"ok": True, "source": src, "chunks": n}


class OnboardingGenerateRequest(BaseModel):
    dump: str = Field("", max_length=200_000)
    url: Optional[str] = Field(None, max_length=500)


@router.post("/onboarding/generate")
async def onboarding_generate(req: OnboardingGenerateRequest, _: None = Depends(_verify_owner)):
    """Конфиг-агент (P4): по «дампу» о компании (текст / опц. ссылка на сайт) LLM
    собирает ЧЕРНОВИК конфигурации — тон (Мозг), факты (KB), ближайший пресет ниши,
    цель бота, языки. Ничего не применяет (только возвращает на ревью)."""
    import json
    import logging as _lg
    import main as _main
    dump = (req.dump or "").strip()
    if req.url:
        try:
            import httpx
            async with httpx.AsyncClient(timeout=15, follow_redirects=True) as cli:
                r = await cli.get(req.url)
            if r.status_code == 200:
                dump += f"\n\n[Сайт {req.url}]\n{r.text[:8000]}"
        except Exception as e:  # noqa: BLE001
            _lg.getLogger("admin").warning("onboarding fetch url failed: %s", e)
    if not dump:
        raise HTTPException(status_code=422, detail="Пустой дамп — вставьте текст о компании или ссылку.")

    preset_keys = list(NICHE_PRESETS.keys())
    prompt = (
        "Ты — конфигуратор AI-системы продаж под нишу. По «дампу» о компании (текст сайта, "
        "регламенты, прайс, рассказ) собери конфигурацию бота-ассистента. Верни СТРОГО JSON "
        "без пояснений и без markdown-ограждения, поля:\n"
        '{"summary": "что за бизнес, 1-2 предложения",\n'
        ' "system_prompt": "тон + позиционирование + что боту можно/нельзя, обращение на «вы», кратко",\n'
        ' "kb_md": "ФАКТЫ для бота (markdown): услуги, цены «от ...», FAQ, политики, график",\n'
        f' "preset_key": "ближайший из {preset_keys}",\n'
        ' "bot_goal": "одно из: call | collect_lead | consult | sale",\n'
        ' "languages": ["ru" и др. если применимо]}\n\n'
        "ДАМП:\n" + dump[:30000]
    )
    try:
        res = await _main.primary_llm.ainvoke(prompt)
        raw = (res.content or "").strip()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"LLM generate failed: {e}")

    draft: dict = {}
    try:
        i, j = raw.find("{"), raw.rfind("}")
        if i != -1 and j != -1:
            draft = json.loads(raw[i:j + 1])
    except Exception:  # noqa: BLE001
        draft = {}
    if not draft:
        draft = {"raw": raw[:4000], "_parse_failed": True}
    return {"ok": True, "draft": draft}


class OnboardingApplyRequest(BaseModel):
    system_prompt: Optional[str] = Field(None, max_length=20_000)
    kb_md: Optional[str] = Field(None, max_length=200_000)
    preset_key: Optional[str] = None
    bot_goal: Optional[str] = None


@router.post("/onboarding/apply")
async def onboarding_apply(
    req: OnboardingApplyRequest,
    _: None = Depends(_verify_owner),
    db: Session = Depends(get_db),
):
    """Применить (отревьюенный) черновик конфиг-агента: мозг + KB + пресет + цель.
    Каждое поле опционально — применяется только заданное."""
    import asyncio
    from services import bot_settings, funnel_store, prompt_store
    applied: dict = {}

    # 1. Мозг — оборачиваем сгенерённый тон в валидный шаблон с плейсхолдерами.
    if req.system_prompt and req.system_prompt.strip():
        wrapped = (
            req.system_prompt.strip()
            + "\n\n# УРОКИ ИЗ ИСПРАВЛЕНИЙ (приоритет над KB)\n{corrections}"
            + "\n\n# КОНТЕКСТ ИЗ KNOWLEDGE BASE\n{context}"
            + "\n\n{handoff_block}"
            + "\n\n# ИСТОРИЯ ДИАЛОГА\n{history}"
            + "\n\n# ТЕКУЩИЙ ВОПРОС КЛИЕНТА\n{question}"
            + "\n\nОтвет (на «вы», кратко, на языке клиента):"
        )
        try:
            prompt_store.set_active_system_prompt(wrapped, created_by="onboarding")
            applied["system_prompt"] = True
        except ValueError as e:
            raise HTTPException(status_code=422, detail=f"Промпт: {e}")

    # 2. База знаний
    if req.kb_md and req.kb_md.strip():
        from services.kb_ingest import ingest_text
        applied["kb_chunks"] = await asyncio.to_thread(ingest_text, "company_knowledge.md", req.kb_md)

    # 3. Пресет ниши (стадии + поля + 📦-автоматизации + текст пинка)
    if req.preset_key:
        preset = NICHE_PRESETS.get(req.preset_key)
        if preset is None:
            raise HTTPException(status_code=404, detail=f"Нет пресета {req.preset_key!r}")
        try:
            if preset["stages"] is None:
                funnel_store.reset_to_builtin(db)
            else:
                funnel_store.save_stages(db, preset["stages"])
        except ValueError as e:
            db.rollback()
            raise HTTPException(status_code=422, detail=f"Стадии пресета: {e}")
        db.query(CustomFieldDef).delete()
        for pos, f in enumerate(preset["fields"]):
            db.add(CustomFieldDef(position=pos, key=f["key"], label=f["label"],
                                  field_type=f["field_type"], options=f.get("options"), active=True))
        db.query(AutomationRule).filter(AutomationRule.name.like("📦%")).delete(synchronize_session=False)
        base_pos = db.query(AutomationRule).count()
        for i, r in enumerate(preset["automations"]):
            db.add(AutomationRule(
                name=r["name"], enabled=True, trigger=r["trigger"],
                conditions=r.get("conditions"), actions=r["actions"],
                cooldown_hours=int(r.get("cooldown_hours", 0)), position=base_pos + i,
            ))
        db.commit()
        applied["preset"] = req.preset_key
        if preset.get("nudge_text"):
            try:
                bot_settings.set_many({"nudge_text": preset["nudge_text"]})
            except Exception:  # noqa: BLE001
                pass

    # 4. Цель бота
    if req.bot_goal in ("call", "collect_lead", "consult", "sale"):
        bot_settings.set_many({"bot_goal": req.bot_goal})
        applied["bot_goal"] = req.bot_goal

    return {"ok": True, "applied": applied}


# ============================================================================
# AUTOMATIONS — конструктор «Когда → Если → То»
# ============================================================================

class AutomationSaveRequest(BaseModel):
    id: Optional[str] = None
    name: str = Field(..., min_length=1, max_length=120)
    enabled: bool = True
    trigger: dict
    conditions: Optional[dict] = None
    actions: list
    cooldown_hours: int = Field(0, ge=0, le=24 * 30)


@router.get("/automations")
async def automations_list(
    _: None = Depends(_verify_member),
    db: Session = Depends(get_db),
):
    rules = db.query(AutomationRule).order_by(AutomationRule.position.asc(), AutomationRule.created_at.asc()).all()
    fired = dict(db.execute(
        sql_select(AutomationRun.rule_id, sql_func.count()).group_by(AutomationRun.rule_id)
    ).fetchall())
    return {
        "items": [
            {
                "id": str(r.id), "name": r.name, "enabled": r.enabled,
                "trigger": r.trigger, "conditions": r.conditions, "actions": r.actions,
                "cooldown_hours": r.cooldown_hours,
                "fired_count": int(fired.get(r.id, 0)),
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rules
        ],
    }


@router.post("/automations")
async def automation_save(
    req: AutomationSaveRequest,
    _: None = Depends(_verify_owner),
    db: Session = Depends(get_db),
):
    from services.automation import validate_rule
    problems = validate_rule(req.trigger, req.conditions, req.actions)
    if problems:
        raise HTTPException(status_code=422, detail={"problems": problems})

    if req.id:
        try:
            row = db.get(AutomationRule, UUID(req.id))
        except ValueError:
            raise HTTPException(status_code=422, detail="id must be a UUID")
        if row is None:
            raise HTTPException(status_code=404, detail="Rule not found")
    else:
        row = AutomationRule(position=db.query(AutomationRule).count())
        db.add(row)
    row.name = req.name.strip()
    row.enabled = req.enabled
    row.trigger = req.trigger
    row.conditions = req.conditions
    row.actions = req.actions
    row.cooldown_hours = req.cooldown_hours
    db.commit()
    return {"ok": True, "id": str(row.id)}


@router.post("/automations/{rule_id}/toggle")
async def automation_toggle(
    rule_id: str,
    _: None = Depends(_verify_owner),
    db: Session = Depends(get_db),
):
    row = db.get(AutomationRule, _uuid_or_422(rule_id))
    if row is None:
        raise HTTPException(status_code=404, detail="Rule not found")
    row.enabled = not row.enabled
    db.commit()
    return {"ok": True, "enabled": row.enabled}


@router.post("/automations/{rule_id}/delete")
async def automation_delete(
    rule_id: str,
    _: None = Depends(_verify_owner),
    db: Session = Depends(get_db),
):
    """Удаление правила — явное действие пользователя в UI (с подтверждением).
    История срабатываний уходит каскадом (FK CASCADE)."""
    row = db.get(AutomationRule, _uuid_or_422(rule_id))
    if row is None:
        raise HTTPException(status_code=404, detail="Rule not found")
    db.delete(row)
    db.commit()
    return {"ok": True}


# ============================================================================
# CUSTOM FIELDS — поля лида под нишу
# ============================================================================

class FieldDefItem(BaseModel):
    key: Optional[str] = None
    label: str = Field(..., min_length=1, max_length=80)
    field_type: str = Field("text", pattern="^(text|number|select)$")
    options: Optional[list[str]] = None
    active: bool = True


class FieldDefsSaveRequest(BaseModel):
    items: list[FieldDefItem] = Field(..., max_length=30)


@router.get("/custom-fields")
async def custom_fields_get(
    _: None = Depends(_verify_member),
    db: Session = Depends(get_db),
):
    rows = db.query(CustomFieldDef).order_by(CustomFieldDef.position.asc()).all()
    return {
        "items": [
            {"id": str(r.id), "key": r.key, "label": r.label, "field_type": r.field_type,
             "options": r.options, "active": r.active}
            for r in rows
        ],
    }


@router.post("/custom-fields")
async def custom_fields_save(
    req: FieldDefsSaveRequest,
    _: None = Depends(_verify_owner),
    db: Session = Depends(get_db),
):
    """Bulk save (как стадии): полная замена определений. Значения у лидов
    (profile_data['fields']) не трогаются — вернёте поле с тем же key,
    значения снова видны."""
    import re
    seen: set[str] = set()
    cleaned = []
    for i, it in enumerate(req.items):
        key = (it.key or "").strip().lower()
        if not key:
            key = re.sub(r"[^a-z0-9_]+", "_", it.label.lower()).strip("_")[:40] or f"field_{i}"
        if not re.fullmatch(r"[a-z0-9_]{1,40}", key):
            raise HTTPException(status_code=422, detail=f"Поле «{it.label}»: ключ {key!r} — только [a-z0-9_]")
        if key in seen:
            raise HTTPException(status_code=422, detail=f"Дубль ключа {key!r}")
        seen.add(key)
        if it.field_type == "select" and not (it.options or []):
            raise HTTPException(status_code=422, detail=f"Поле «{it.label}»: для списка нужны варианты")
        cleaned.append({"key": key, "label": it.label.strip()[:80], "field_type": it.field_type,
                        "options": it.options, "active": it.active})

    db.query(CustomFieldDef).delete()
    for pos, it in enumerate(cleaned):
        db.add(CustomFieldDef(position=pos, **it))
    db.commit()
    return {"ok": True, "count": len(cleaned)}


class FieldValuesRequest(BaseModel):
    values: dict


@router.post("/conversations/{conv_id}/fields")
async def conversation_fields_save(
    conv_id: str,
    req: FieldValuesRequest,
    _: None = Depends(_verify_member),
    db: Session = Depends(get_db),
):
    conv, cust = _get_conv_or_404(db, conv_id)
    known = {r.key for r in db.query(CustomFieldDef).all()}
    bad = [k for k in req.values if k not in known]
    if bad:
        raise HTTPException(status_code=422, detail=f"Неизвестные поля: {bad}")
    pd = dict(cust.profile_data or {})
    fields = dict(pd.get("fields") or {})
    for k, v in req.values.items():
        if v is None or v == "":
            fields.pop(k, None)
        else:
            fields[k] = v
    pd["fields"] = fields
    cust.profile_data = pd
    db.commit()
    return {"ok": True, "fields": fields}


# ============================================================================
# ANALYTICS — цифры воронки и каналов
# ============================================================================

@router.get("/analytics")
async def analytics(
    days: int = 30,
    _: None = Depends(_verify_member),
    db: Session = Depends(get_db),
):
    days = max(1, min(days, 365))
    since = datetime.now(timezone.utc) - timedelta(days=days)

    new_leads = db.execute(
        sql_select(sql_func.count()).select_from(Customer).where(Customer.created_at >= since)
    ).scalar() or 0
    leads_by_channel = dict(db.execute(
        sql_select(Conversation.channel, sql_func.count(sql_func.distinct(Conversation.customer_id)))
        .where(Conversation.created_at >= since)
        .group_by(Conversation.channel)
    ).fetchall())
    by_day_rows = db.execute(
        sql_select(sql_func.date_trunc("day", Customer.created_at).label("d"), sql_func.count())
        .where(Customer.created_at >= since)
        .group_by("d").order_by("d")
    ).fetchall()
    stage_dist = dict(db.execute(
        sql_select(Conversation.lead_stage, sql_func.count()).group_by(Conversation.lead_stage)
    ).fetchall())
    lost_reasons = dict(db.execute(
        sql_select(Conversation.lost_reason, sql_func.count())
        .where(Conversation.lead_stage == "lost", Conversation.lost_reason.isnot(None))
        .group_by(Conversation.lost_reason)
    ).fetchall())
    temp_dist = dict(db.execute(
        sql_select(Customer.lead_temperature, sql_func.count()).group_by(Customer.lead_temperature)
    ).fetchall())
    handoffs = db.execute(
        sql_select(sql_func.count()).select_from(Conversation)
        .where(Conversation.handoff_done == True)  # noqa: E712
    ).scalar() or 0
    on_call = int(stage_dist.get("on_call", 0))
    msgs_period = db.execute(
        sql_select(Message.role, sql_func.count())
        .where(Message.created_at >= since)
        .group_by(Message.role)
    ).fetchall()
    transitions = db.execute(
        sql_select(StageTransition.to_stage, sql_func.count())
        .where(StageTransition.created_at >= since)
        .group_by(StageTransition.to_stage)
    ).fetchall()
    # Конверсия: потоки from→to (история копится с 2026-06-11).
    flows = db.execute(
        sql_select(StageTransition.from_stage, StageTransition.to_stage,
                   StageTransition.by, sql_func.count())
        .where(StageTransition.created_at >= since)
        .group_by(StageTransition.from_stage, StageTransition.to_stage, StageTransition.by)
        .order_by(sql_func.count().desc())
    ).fetchall()
    automation_fires = db.execute(
        sql_select(sql_func.count()).select_from(AutomationRun)
        .where(AutomationRun.fired_at >= since)
    ).scalar() or 0

    from services import funnel_store
    stages = funnel_store.get_stages(db)

    return {
        "days": days,
        "totals": {
            "new_leads": int(new_leads),
            "handoffs": int(handoffs),
            "booked_calls": on_call,
            "automation_fires": int(automation_fires),
        },
        "leads_by_channel": {k: int(v) for k, v in leads_by_channel.items()},
        "leads_by_day": [
            {"day": r[0].date().isoformat(), "count": int(r[1])} for r in by_day_rows
        ],
        "funnel": [
            {"stage": s["key"], "label": s["label"], "count": int(stage_dist.get(s["key"], 0))}
            for s in stages if s["active"]
        ],
        "lost_reasons": {k: int(v) for k, v in lost_reasons.items()},
        "temperatures": {k: int(v) for k, v in temp_dist.items()},
        "messages_by_role": {str(r[0]).lower(): int(r[1]) for r in msgs_period},
        "stage_moves": {k: int(v) for k, v in transitions},
        "stage_flows": [
            {"from": r[0], "to": r[1], "by": r[2], "count": int(r[3])} for r in flows
        ],
    }


# ============================================================================
# NICHE PRESETS — конфигурация под нишу в 1 клик (паттерн GHL Snapshots)
# ============================================================================
# Пресет = переименованные встроенные стадии (бот-логика остаётся!) + свои
# стадии + кастомные поля + правила автоматизации (имена с 📦 — при повторном
# применении пресет-правила заменяются, ручные не трогаются) + текст пинка.

NICHE_PRESETS: dict = {
    "web_studio": {
        "title": "Веб-студия / диджитал-агентство", "emoji": "💻",
        "desc": "Сайты, боты, автоматизации. Наша родная конфигурация.",
        "stages": None,  # = заводские
        "fields": [
            {"label": "Тип проекта", "key": "project_type", "field_type": "select",
             "options": ["Лендинг", "Сайт/магазин", "Бот/AI", "Автоматизация", "Другое"]},
            {"label": "Бюджет", "key": "budget", "field_type": "text"},
            {"label": "Срок", "key": "deadline", "field_type": "text"},
        ],
        "automations": [
            {"name": "📦 Молчит сутки — мягкий пинг", "trigger": {"type": "lead_silent", "hours": 24},
             "conditions": {"stages": ["new_lead", "in_dialog"], "channels": ["telegram"]},
             "actions": [{"type": "bot_message", "text": "Добрый день! Возвращаюсь к вашему проекту — подскажите, актуально? Если удобнее позже, просто скажите когда 🙂"}],
             "cooldown_hours": 0},
            {"name": "📦 Созвон назначен, лид пропал 2 дня — задача менеджеру",
             "trigger": {"type": "lead_silent", "hours": 48},
             "conditions": {"stages": ["on_call", "qualified"]},
             "actions": [{"type": "create_task", "text": "Лид завис после квалификации — связаться лично", "due_in_hours": 2},
                         {"type": "notify_admin", "text": "Тёплый лид завис — поставлена задача"}],
             "cooldown_hours": 0},
        ],
        "nudge_text": None,
    },
    "dentistry": {
        "title": "Стоматология / клиника", "emoji": "🦷",
        "desc": "Заявка → консультация → запись на приём → лечение.",
        "stages": [
            {"key": "new_lead", "label": "🆕 Новая заявка", "kind": "active", "active": True},
            {"key": "in_dialog", "label": "💬 Уточняем запрос", "kind": "active", "active": True},
            {"key": "qualified", "label": "✅ Готов записаться", "kind": "active", "active": True},
            {"key": "on_call", "label": "📅 Запись на приём", "kind": "active", "active": True},
            {"key": "proposal", "label": "🦷 План лечения", "kind": "active", "active": True},
            {"key": "prepayment", "label": "💰 Предоплата", "kind": "active", "active": True},
            {"key": "completed_won", "label": "🏁 Лечение завершено", "kind": "won", "active": True},
            {"key": "repeat_visit", "label": "🔁 Повторный приём", "kind": "active", "active": True},
            {"key": "lost", "label": "❌ Не дошёл", "kind": "lost", "active": True},
        ],
        "fields": [
            {"label": "Услуга", "key": "service", "field_type": "select",
             "options": ["Лечение", "Имплантация", "Брекеты/элайнеры", "Чистка/гигиена", "Протезирование", "Другое"]},
            {"label": "Жалоба / что беспокоит", "key": "complaint", "field_type": "text"},
            {"label": "Удобное время", "key": "preferred_time", "field_type": "text"},
        ],
        "automations": [
            {"name": "📦 Не записался за 24ч — напомнить", "trigger": {"type": "lead_silent", "hours": 24},
             "conditions": {"stages": ["new_lead", "in_dialog", "qualified"], "channels": ["telegram"]},
             "actions": [{"type": "bot_message", "text": "Здравствуйте! Напомню про запись — есть удобные окна на этой неделе. Подсказать время? 🙂"}],
             "cooldown_hours": 48},
            {"name": "📦 Записан, тишина 3 дня — задача администратору",
             "trigger": {"type": "lead_silent", "hours": 72},
             "conditions": {"stages": ["on_call"]},
             "actions": [{"type": "create_task", "text": "Подтвердить запись пациента звонком", "due_in_hours": 3}],
             "cooldown_hours": 0},
        ],
        "nudge_text": "Здравствуйте! Вы интересовались записью — актуально ещё? Подберу удобное время 🙂",
    },
    "fitness": {
        "title": "Фитнес / спортзал / студия", "emoji": "🏋️",
        "desc": "Лид → пробное занятие → абонемент → продление.",
        "stages": [
            {"key": "new_lead", "label": "🆕 Новый лид", "kind": "active", "active": True},
            {"key": "in_dialog", "label": "💬 В диалоге", "kind": "active", "active": True},
            {"key": "qualified", "label": "✅ Хочет пробное", "kind": "active", "active": True},
            {"key": "on_call", "label": "📅 Записан на пробное", "kind": "active", "active": True},
            {"key": "proposal", "label": "🎟 Предложен абонемент", "kind": "active", "active": True},
            {"key": "prepayment", "label": "💰 Оплата", "kind": "active", "active": True},
            {"key": "completed_won", "label": "🏁 Клиент", "kind": "won", "active": True},
            {"key": "renewal", "label": "🔁 Продление", "kind": "active", "active": True},
            {"key": "lost", "label": "❌ Потерян", "kind": "lost", "active": True},
        ],
        "fields": [
            {"label": "Цель", "key": "goal", "field_type": "select",
             "options": ["Похудение", "Набор массы", "Тонус/здоровье", "Групповые", "Персональные"]},
            {"label": "Опыт тренировок", "key": "experience", "field_type": "select",
             "options": ["Новичок", "Занимался раньше", "Регулярно тренируюсь"]},
            {"label": "Удобное время", "key": "preferred_time", "field_type": "text"},
        ],
        "automations": [
            {"name": "📦 Не дошёл до пробного — пинг через сутки", "trigger": {"type": "lead_silent", "hours": 24},
             "conditions": {"stages": ["new_lead", "in_dialog", "qualified"], "channels": ["telegram"]},
             "actions": [{"type": "bot_message", "text": "Привет! Пробное занятие ещё в силе 💪 Записать вас на этой неделе?"}],
             "cooldown_hours": 72},
        ],
        "nudge_text": "Привет! Вы спрашивали про занятия — актуально? Могу записать на бесплатное пробное 💪",
    },
    "realty": {
        "title": "Недвижимость / риелтор", "emoji": "🏠",
        "desc": "Заявка → квалификация → показ → бронь → сделка.",
        "stages": [
            {"key": "new_lead", "label": "🆕 Новая заявка", "kind": "active", "active": True},
            {"key": "in_dialog", "label": "💬 Выясняем запрос", "kind": "active", "active": True},
            {"key": "qualified", "label": "✅ Квалифицирован", "kind": "active", "active": True},
            {"key": "on_call", "label": "📅 Назначен показ", "kind": "active", "active": True},
            {"key": "proposal", "label": "📄 Предложены варианты", "kind": "active", "active": True},
            {"key": "prepayment", "label": "💰 Бронь/аванс", "kind": "active", "active": True},
            {"key": "completed_won", "label": "🏁 Сделка", "kind": "won", "active": True},
            {"key": "lost", "label": "❌ Потерян", "kind": "lost", "active": True},
        ],
        "fields": [
            {"label": "Тип", "key": "deal_type", "field_type": "select",
             "options": ["Купить", "Снять", "Продать", "Сдать"]},
            {"label": "Бюджет", "key": "budget", "field_type": "text"},
            {"label": "Район / локация", "key": "location", "field_type": "text"},
            {"label": "Срочность", "key": "urgency", "field_type": "select",
             "options": ["Срочно (до месяца)", "1-3 месяца", "Просто смотрю"]},
        ],
        "automations": [
            {"name": "📦 Лид остыл за 48ч — подборка-пинг", "trigger": {"type": "lead_silent", "hours": 48},
             "conditions": {"stages": ["new_lead", "in_dialog"], "channels": ["telegram"]},
             "actions": [{"type": "bot_message", "text": "Добрый день! По вашему запросу появились новые варианты — прислать подборку? 🙂"}],
             "cooldown_hours": 96},
            {"name": "📦 После показа тишина 2 дня — задача риелтору",
             "trigger": {"type": "lead_silent", "hours": 48},
             "conditions": {"stages": ["on_call", "proposal"]},
             "actions": [{"type": "create_task", "text": "Взять обратную связь после показа, дожать", "due_in_hours": 4}],
             "cooldown_hours": 0},
        ],
        "nudge_text": "Добрый день! Вы искали недвижимость — запрос ещё актуален? Есть свежие варианты 🙂",
    },
    "online_school": {
        "title": "Онлайн-школа / курсы", "emoji": "🎓",
        "desc": "Лид → диагностика → пробный урок → оплата курса.",
        "stages": [
            {"key": "new_lead", "label": "🆕 Новый лид", "kind": "active", "active": True},
            {"key": "in_dialog", "label": "💬 В диалоге", "kind": "active", "active": True},
            {"key": "qualified", "label": "✅ Прошёл диагностику", "kind": "active", "active": True},
            {"key": "on_call", "label": "📅 Пробный урок", "kind": "active", "active": True},
            {"key": "proposal", "label": "📄 Предложен тариф", "kind": "active", "active": True},
            {"key": "prepayment", "label": "💰 Оплата", "kind": "active", "active": True},
            {"key": "completed_won", "label": "🏁 Ученик", "kind": "won", "active": True},
            {"key": "lost", "label": "❌ Потерян", "kind": "lost", "active": True},
        ],
        "fields": [
            {"label": "Направление", "key": "course", "field_type": "text"},
            {"label": "Уровень", "key": "level", "field_type": "select",
             "options": ["С нуля", "Базовый", "Продвинутый"]},
            {"label": "Для кого", "key": "for_whom", "field_type": "select",
             "options": ["Себе", "Ребёнку", "Сотрудникам"]},
        ],
        "automations": [
            {"name": "📦 Не дошёл до пробного — пинг", "trigger": {"type": "lead_silent", "hours": 24},
             "conditions": {"stages": ["new_lead", "in_dialog", "qualified"], "channels": ["telegram"]},
             "actions": [{"type": "bot_message", "text": "Привет! Бесплатный пробный урок ещё доступен — выбрать удобное время? 🙂"}],
             "cooldown_hours": 72},
        ],
        "nudge_text": "Привет! Вы интересовались обучением — актуально? Могу предложить бесплатный пробный урок 🙂",
    },
    "beauty": {
        "title": "Салон красоты / мастер", "emoji": "💅",
        "desc": "Заявка → запись → визит → повторный визит.",
        "stages": [
            {"key": "new_lead", "label": "🆕 Новая заявка", "kind": "active", "active": True},
            {"key": "in_dialog", "label": "💬 Уточняем", "kind": "active", "active": True},
            {"key": "qualified", "label": "✅ Готов записаться", "kind": "active", "active": True},
            {"key": "on_call", "label": "📅 Записан", "kind": "active", "active": True},
            {"key": "proposal", "label": "📄 Доп. услуги", "kind": "active", "active": False},
            {"key": "prepayment", "label": "💰 Предоплата", "kind": "active", "active": False},
            {"key": "completed_won", "label": "🏁 Пришёл", "kind": "won", "active": True},
            {"key": "repeat_visit", "label": "🔁 Повторная запись", "kind": "active", "active": True},
            {"key": "lost", "label": "❌ Не дошёл", "kind": "lost", "active": True},
        ],
        "fields": [
            {"label": "Услуга", "key": "service", "field_type": "text"},
            {"label": "Мастер", "key": "master", "field_type": "text"},
            {"label": "Удобное время", "key": "preferred_time", "field_type": "text"},
        ],
        "automations": [
            {"name": "📦 Не записался за день — напомнить", "trigger": {"type": "lead_silent", "hours": 24},
             "conditions": {"stages": ["new_lead", "in_dialog", "qualified"], "channels": ["telegram"]},
             "actions": [{"type": "bot_message", "text": "Здравствуйте! Есть свободные окошки на этой неделе — записать вас? 💅"}],
             "cooldown_hours": 72},
        ],
        "nudge_text": "Здравствуйте! Вы хотели записаться — актуально ещё? Подберу удобное окошко 🙂",
    },
    "cleaning_repair": {
        "title": "Клининг + Ремонт (выездные услуги)", "emoji": "🧹",
        "desc": "Заявка → выявление → [клининг: расчёт→визит] / [ремонт: замер→передача специалисту] → выполнение → постоянный клиент.",
        "stages": [
            {"key": "new_lead", "label": "🆕 Новая заявка", "kind": "active", "active": True},
            {"key": "in_dialog", "label": "💬 Выявляем потребность", "kind": "active", "active": True},
            {"key": "qualified", "label": "✅ Квалифицирован", "kind": "active", "active": True},
            {"key": "on_call", "label": "📅 Назначен визит/замер", "kind": "active", "active": True},
            {"key": "proposal", "label": "📄 Расчёт/смета", "kind": "active", "active": True},
            {"key": "prepayment", "label": "💰 Аванс/подтверждение", "kind": "active", "active": True},
            {"key": "in_work", "label": "🧹 В работе (на объекте)", "kind": "active", "active": True},
            {"key": "completed_won", "label": "🏁 Выполнено", "kind": "won", "active": True},
            {"key": "recurring", "label": "🔁 Постоянный / ТО", "kind": "active", "active": True},
            {"key": "lost", "label": "❌ Потерян", "kind": "lost", "active": True},
        ],
        "fields": [
            {"label": "Услуга", "key": "service", "field_type": "select",
             "options": ["Клининг", "Ремонт/реновация", "Другое"]},
            {"label": "Тип объекта", "key": "object_type", "field_type": "select",
             "options": ["Квартира", "Дом/вилла", "Офис", "Коммерческое"]},
            {"label": "Адрес", "key": "address", "field_type": "text"},
            {"label": "Желаемая дата/время", "key": "preferred_time", "field_type": "text"},
            {"label": "Объём / бюджет (для ремонта)", "key": "budget", "field_type": "text"},
            {"label": "Регулярность", "key": "recurrence", "field_type": "select",
             "options": ["Разовый", "Регулярный / ТО"]},
        ],
        "automations": [
            {"name": "📦 Молчит сутки — мягкий пинг", "trigger": {"type": "lead_silent", "hours": 24},
             "conditions": {"stages": ["new_lead", "in_dialog", "qualified"], "channels": ["telegram", "whatsapp"]},
             "actions": [{"type": "bot_message", "text": "Здравствуйте! Возвращаюсь к вашей заявке — актуально? Подберём удобное время для выезда 🙂"}],
             "cooldown_hours": 48},
            {"name": "📦 После замера/расчёта тишина 2 дня — задача менеджеру",
             "trigger": {"type": "lead_silent", "hours": 48},
             "conditions": {"stages": ["on_call", "proposal"]},
             "actions": [{"type": "create_task", "text": "Взять обратную связь после визита/расчёта, дожать", "due_in_hours": 3},
                         {"type": "notify_admin", "text": "Лид завис после визита/расчёта — поставлена задача"}],
             "cooldown_hours": 0},
            {"name": "📦 Крупный/ремонт — передать человеку",
             "trigger": {"type": "stage_changed", "to_stage": "qualified"},
             "conditions": {},
             "actions": [{"type": "create_task", "text": "Квалифицирован — проверить, нужен ли выезд специалиста (ремонт/крупный объект)", "due_in_hours": 1}],
             "cooldown_hours": 0},
        ],
        "nudge_text": "Здравствуйте! Вы оставляли заявку — актуально ещё? Подберём удобное время для выезда команды 🙂",
    },
}


@router.get("/presets")
async def presets_list(_: None = Depends(_verify_owner)):
    return {
        "items": [
            {
                "key": k, "title": p["title"], "emoji": p["emoji"], "desc": p["desc"],
                "stages_count": len(p["stages"]) if p["stages"] else 8,
                "fields_count": len(p["fields"]),
                "automations_count": len(p["automations"]),
            }
            for k, p in NICHE_PRESETS.items()
        ],
    }


class PresetApplyRequest(BaseModel):
    key: str


@router.post("/presets/apply")
async def preset_apply(
    req: PresetApplyRequest,
    _: None = Depends(_verify_owner),
    db: Session = Depends(get_db),
):
    """Применить нишевый пресет: стадии + поля + пресет-автоматизации (📦) +
    текст пинка. Ручные правила (без 📦) и значения полей у лидов не трогаются.
    Промпт бота НЕ меняется — тон под нишу правится во вкладке «Мозг»."""
    preset = NICHE_PRESETS.get(req.key)
    if preset is None:
        raise HTTPException(status_code=404, detail=f"Нет пресета {req.key!r}")

    from services import funnel_store, bot_settings
    applied = {"stages": 0, "fields": 0, "automations": 0}

    # 1. Стадии (None = сброс на заводские).
    if preset["stages"] is None:
        funnel_store.reset_to_builtin(db)
        applied["stages"] = len(funnel_store.BUILTIN_STAGES)
    else:
        try:
            items = funnel_store.save_stages(db, preset["stages"])
            applied["stages"] = len(items)
        except ValueError as e:
            db.rollback()
            raise HTTPException(status_code=422, detail=f"Стадии пресета: {e}")

    # 2. Поля: полная замена определений (значения у лидов остаются в profile_data).
    db.query(CustomFieldDef).delete()
    for pos, f in enumerate(preset["fields"]):
        db.add(CustomFieldDef(position=pos, key=f["key"], label=f["label"],
                              field_type=f["field_type"], options=f.get("options"), active=True))
        applied["fields"] += 1

    # 3. Автоматизации: заменяем только пресетные (📦), ручные не трогаем.
    db.query(AutomationRule).filter(AutomationRule.name.like("📦%")).delete(synchronize_session=False)
    base_pos = db.query(AutomationRule).count()
    for i, r in enumerate(preset["automations"]):
        db.add(AutomationRule(
            name=r["name"], enabled=True, trigger=r["trigger"],
            conditions=r.get("conditions"), actions=r["actions"],
            cooldown_hours=int(r.get("cooldown_hours", 0)), position=base_pos + i,
        ))
        applied["automations"] += 1

    db.commit()

    # 4. Текст пинка (поведение) — через bot_settings.
    if preset.get("nudge_text"):
        try:
            bot_settings.set_many({"nudge_text": preset["nudge_text"]})
        except Exception:  # noqa: BLE001 — не критично
            pass

    return {"ok": True, "applied": applied, "preset": preset["title"]}


# ============================================================================
# WORKSPACE + DEMO — онбординг, брендинг, песочница
# ============================================================================

class WorkspaceSaveRequest(BaseModel):
    business_name: Optional[str] = Field(None, max_length=80)
    logo_url: Optional[str] = Field(None, max_length=500)
    accent_color: Optional[str] = Field(None, max_length=20)
    onboarding_done: Optional[bool] = None
    niche_key: Optional[str] = None


class LanguagesSaveRequest(BaseModel):
    languages: list[str] = Field(default_factory=list)


@router.get("/languages")
async def languages_get(_: None = Depends(_verify_member)):
    """Текущий список поддерживаемых языков (рантайм-оверрайд или config.yaml)."""
    import main as _main
    from services import bot_settings
    ov = (bot_settings.get("languages") or "").strip()
    langs = [x.strip() for x in ov.split(",") if x.strip()] if ov else list(_main.tenant.languages or ["ru"])
    return {"languages": langs}


@router.post("/languages")
async def languages_save(req: LanguagesSaveRequest, _: None = Depends(_verify_owner)):
    """Сохранить список языков (добавить/удалить). Первый = основной (приветствие).
    Живой диалог LLM отвечает на языке клиента независимо."""
    from services import bot_settings
    seen: set = set()
    out: list = []
    for x in (req.languages or []):
        c = (x or "").strip().lower()
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    bot_settings.set_many({"languages": ",".join(out)})
    return {"ok": True, "languages": out}


@router.get("/workspace")
async def workspace_get(
    _: None = Depends(_verify_member),
    db: Session = Depends(get_db),
):
    import main as _main
    from services import bot_settings
    from services.demo_seed import demo_count
    ws = bot_settings.get_all()
    return {
        "business_name": ws.get("business_name") or _main.tenant.display_name,
        "onboarding_done": bool(ws.get("onboarding_done", False)),
        "niche_key": ws.get("niche_key"),
        "logo_url": ws.get("logo_url"),
        "accent_color": ws.get("accent_color"),
        "demo_leads": demo_count(db),
    }


@router.post("/workspace")
async def workspace_save(
    req: WorkspaceSaveRequest,
    _: None = Depends(_verify_owner),
):
    from services import bot_settings
    values: dict = {}
    if req.business_name is not None:
        values["business_name"] = req.business_name.strip() or None
    if req.onboarding_done is not None:
        values["onboarding_done"] = req.onboarding_done
    if req.niche_key is not None:
        values["niche_key"] = req.niche_key or None
    if req.logo_url is not None:
        values["logo_url"] = req.logo_url.strip() or None
    if req.accent_color is not None:
        import re as _re
        c = req.accent_color.strip()
        if c and not _re.fullmatch(r"#[0-9a-fA-F]{6}", c):
            raise HTTPException(status_code=422, detail="accent_color: формат #rrggbb")
        values["accent_color"] = c or None
    if not values:
        raise HTTPException(status_code=422, detail="Нечего сохранять")
    try:
        bot_settings.set_many(values)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {"ok": True}


OBJECTION_TAGS = ("price", "timing", "trust", "no_need", "competitor", "other")


@router.get("/analytics/objections")
async def analytics_objections(
    refresh: bool = False,
    _: dict = Depends(_verify_owner),
    db: Session = Depends(get_db),
):
    """«Почему не покупают»: LLM-теги проигранных диалогов (price/timing/trust/
    no_need/competitor/other) + цитата лида. Тег кэшируется в
    customer.profile_data['objection'] — LLM зовём один раз на диалог
    (refresh=true размечает ещё не размеченные, до 12 за вызов)."""
    rows = (
        db.query(Conversation, Customer)
        .join(Customer, Conversation.customer_id == Customer.id)
        .filter(Conversation.lead_stage == "lost")
        .order_by(Conversation.last_message_at.desc().nullslast())
        .limit(30)
        .all()
    )
    items, pending = [], []
    for conv, cust in rows:
        cached = ((cust.profile_data or {}).get("objection") or None)
        if cached and cached.get("tag") in OBJECTION_TAGS:
            items.append({"name": cust.name or cust.email or "лид",
                          "tag": cached["tag"], "quote": cached.get("quote", ""),
                          "lost_reason": conv.lost_reason})
        else:
            pending.append((conv, cust))

    analyzed_now = 0
    if refresh and pending:
        import main as _main
        blocks = []
        by_key = {}
        for conv, cust in pending[:12]:
            msgs = (
                db.query(Message)
                .filter(Message.conversation_id == conv.id, Message.role == "user")
                .order_by(Message.created_at.desc())
                .limit(3)
                .all()
            )
            text = " / ".join(m.content[:200] for m in reversed(msgs)) or "(лид не писал)"
            key = str(conv.id)[:8]
            by_key[key] = (conv, cust)
            blocks.append(f"{key}: {text}")
        prompt = (
            "Классифицируй причину отказа каждого лида ровно одной категорией из: "
            "price, timing, trust, no_need, competitor, other.\n"
            "Формат ответа — СТРОГО по строке на лида, без пояснений:\n"
            "<id>|<категория>|<короткая цитата из слов лида (до 10 слов)>\n\n"
            + "\n".join(blocks)
        )
        try:
            resp = await _main.primary_llm.ainvoke(prompt)
            for line in (resp.content or "").splitlines():
                parts = [p.strip() for p in line.split("|")]
                if len(parts) >= 2 and parts[0] in by_key:
                    tag = parts[1] if parts[1] in OBJECTION_TAGS else "other"
                    quote = parts[2][:200] if len(parts) > 2 else ""
                    conv, cust = by_key[parts[0]]
                    pd = dict(cust.profile_data or {})
                    pd["objection"] = {"tag": tag, "quote": quote}
                    cust.profile_data = pd
                    items.append({"name": cust.name or cust.email or "лид",
                                  "tag": tag, "quote": quote,
                                  "lost_reason": conv.lost_reason})
                    analyzed_now += 1
            db.commit()
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=f"LLM analysis failed: {e}")

    counts: dict = {}
    for it in items:
        counts[it["tag"]] = counts.get(it["tag"], 0) + 1
    return {
        "counts": counts,
        "items": items[:20],
        "unanalyzed": max(0, len(pending) - analyzed_now),
        "total_lost": len(rows),
    }


@router.get("/export/leads.csv")
async def export_leads_csv(
    _: None = Depends(_verify_member),
    db: Session = Depends(get_db),
):
    """Вся база лидов в CSV (Excel-совместимый, UTF-8 BOM)."""
    import csv
    import io
    from fastapi.responses import Response

    field_defs = db.query(CustomFieldDef).order_by(CustomFieldDef.position.asc()).all()
    rows = (
        db.query(Conversation, Customer)
        .join(Customer, Conversation.customer_id == Customer.id)
        .order_by(Conversation.last_message_at.desc().nullslast())
        .limit(5000)
        .all()
    )
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")  # ; — чтобы Excel с русской локалью открыл колонками
    w.writerow(
        ["Имя", "Email", "Телефон", "Канал", "Стадия", "Причина проигрыша",
         "Скор", "Температура", "Создан", "Последнее сообщение"]
        + [f.label for f in field_defs]
    )
    for conv, cust in rows:
        fields = ((cust.profile_data or {}).get("fields") or {})
        w.writerow([
            cust.name or "", cust.email or "", cust.phone or "",
            conv.channel, conv.lead_stage, conv.lost_reason or "",
            cust.lead_score, cust.lead_temperature,
            conv.created_at.strftime("%Y-%m-%d %H:%M") if conv.created_at else "",
            conv.last_message_at.strftime("%Y-%m-%d %H:%M") if conv.last_message_at else "",
        ] + [fields.get(f.key, "") for f in field_defs])
    csv_bytes = ("﻿" + buf.getvalue()).encode("utf-8")  # BOM для Excel
    return Response(
        content=csv_bytes,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=leads.csv"},
    )


@router.post("/digest/test")
async def digest_test(_: None = Depends(_verify_owner)):
    """Отправить дайджест прямо сейчас (проверка/демо)."""
    from services.digest import send_digest
    return await send_digest()


@router.post("/demo/seed")
async def demo_seed_ep(
    _: None = Depends(_verify_owner),
    db: Session = Depends(get_db),
):
    """Наполнить песочницу демо-лидами (7 шт, разные стадии/каналы + задачи).
    Идемпотентно. Никаких внешних отправок: channel_conversation_id=None."""
    from services.demo_seed import seed_demo
    result = seed_demo(db)
    db.commit()
    return {"ok": True, **result}


@router.post("/demo/clear")
async def demo_clear_ep(
    _: None = Depends(_verify_owner),
    db: Session = Depends(get_db),
):
    """Удалить ТОЛЬКО демо-данные (метка profile_data.demo) — явная кнопка
    пользователя в UI. Реальные лиды не затрагиваются."""
    from services.demo_seed import clear_demo
    result = clear_demo(db)
    db.commit()
    return {"ok": True, **result}


# ============================================================================
# helpers
# ============================================================================

def _uuid_or_422(value: str) -> UUID:
    try:
        return UUID(value)
    except ValueError:
        raise HTTPException(status_code=422, detail="id must be a UUID")


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
