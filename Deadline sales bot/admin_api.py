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
    ConversationStatusEnum,
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

CHANNELS = ("whatsapp", "website", "telegram", "instagram", "messenger")


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
        "whatsapp": bool(getattr(s, "waha_base_url", None) or getattr(s, "greenapi_id_instance", None)),
        "website": True,
        "telegram": bool(s.telegram_bot_token),
        "instagram": bool(s.meta_page_access_token),
        "messenger": bool(s.meta_page_access_token),
    }
    # Доп.метрики каналов для Канваса-обзора: новых за вчера, горячих, без задачи.
    from datetime import datetime as _dt, timedelta as _td, timezone as _tz
    _now = _dt.now(_tz.utc)
    _t0 = _now.replace(hour=0, minute=0, second=0, microsecond=0)
    _y0 = _t0 - _td(days=1)
    new_yest = dict(db.execute(
        sql_select(Conversation.channel, sql_func.count())
        .where(Conversation.created_at >= _y0, Conversation.created_at < _t0)
        .group_by(Conversation.channel)
    ).fetchall())
    hot_by_ch = dict(db.execute(
        sql_select(Conversation.channel, sql_func.count())
        .select_from(Conversation).join(Customer, Conversation.customer_id == Customer.id)
        .where(Customer.lead_temperature.in_(("hot", "ready")),
               Conversation.status != "archived")
        .group_by(Conversation.channel)
    ).fetchall())
    _have_task = {r[0] for r in db.execute(
        sql_select(ScheduledAction.conversation_id)
        .where(ScheduledAction.status.in_(("pending", "processing")),
               ScheduledAction.conversation_id.isnot(None))
    ).fetchall()}
    _active_rows = db.execute(
        sql_select(Conversation.channel, Conversation.id)
        .where(Conversation.status != "archived",
               Conversation.lead_stage.in_(list(_ACTIVE_STAGES)))
    ).fetchall()
    no_task_ch: dict = {}
    for _chn, _cid in _active_rows:
        if _cid not in _have_task:
            no_task_ch[_chn] = no_task_ch.get(_chn, 0) + 1
    channels = []
    for ch in CHANNELS:
        last = last_msg_by_channel.get(ch)
        channels.append({
            "id": ch,
            "configured": configured[ch],
            "conversations": int(by_channel_total.get(ch, 0)),
            "open": int(by_channel_open.get(ch, 0)),
            "new_yesterday": int(new_yest.get(ch, 0)),
            "hot": int(hot_by_ch.get(ch, 0)),
            "no_task": int(no_task_ch.get(ch, 0)),
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
    _eod = _now.replace(hour=23, minute=59, second=59)
    tasks_overdue = db.execute(
        sql_select(sql_func.count()).select_from(ScheduledAction)
        .where(ScheduledAction.status.in_(("pending", "processing")),
               ScheduledAction.due_at < _now)
    ).scalar() or 0
    tasks_today = db.execute(
        sql_select(sql_func.count()).select_from(ScheduledAction)
        .where(ScheduledAction.status.in_(("pending", "processing")),
               ScheduledAction.due_at >= _now, ScheduledAction.due_at <= _eod)
    ).scalar() or 0
    no_task_total = sum(no_task_ch.values())

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
        "tasks": {
            "scheduled_pending": int(tasks_pending),
            "overdue": int(tasks_overdue),
            "today": int(tasks_today),
            "no_task": int(no_task_total),
        },
        "inbox": {
            "open": int(inbox_open),
            "takeover": int(inbox_takeover),
            "handed_off": int(inbox_handed_off),
        },
    }


# ============================================================================
# INBOX — переписки всех каналов в одном месте
# ============================================================================

def _wa_display_name(cust: Customer, conv: Conversation) -> str:
    """Никогда не «Без имени»: имя → телефон → +номер (реальный @c.us) → хвост
    скрытого @lid. Юзер просил видеть хотя бы номер, а не «Без имени»."""
    import re as _re
    if (cust.name or "").strip():
        return cust.name.strip()
    if (cust.phone or "").strip():
        return cust.phone.strip()
    cid = (conv.channel_conversation_id or "").strip()
    digits = _re.sub(r"\D", "", cid)
    if digits:
        # реальный номер (@c.us, ≤13 цифр) → +номер; скрытый @lid (длинный) → хвост
        if len(digits) <= 13:
            return "+" + digits
        return "WhatsApp •" + digits[-4:]
    return "Без имени"


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
            "display_name": _wa_display_name(cust, conv),
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
    include_archived: bool = False,
    limit: int = 50,
    offset: int = 0,
    _: None = Depends(_verify_member),
    db: Session = Depends(get_db),
):
    limit = max(1, min(limit, 1000))
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
    elif not include_archived:
        # По умолчанию скрываем архивные (слитые дубли, сидлайн recall) из
        # активных списков/воронки — чтобы не мешали управлению.
        query = query.filter(Conversation.status != ConversationStatusEnum.ARCHIVED.value)
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

    # Всегда показывать АКТУАЛЬНЫЙ предложенный ответ: для активного WhatsApp-лида
    # при открытии карточки генерируем черновик, если его нет ИЛИ он устарел (после
    # новых реплик лида/оператора). Один LLM-вызов на открытие/устаревание; дальше
    # based_on_count свежий и повторные опросы карточки не триггерят регенерацию.
    # ВАЖНО: НИКАКИХ LLM в этом GET-пути (карточка поллится панелью; LLM держал
    # бы DB-коннект → пул исчерпывается → event loop виснет, sync SQLAlchemy).
    # Черновик генерится в ФОНЕ (services.conversation_brain по новым сообщениям
    # + cron), здесь только отдаём готовый + дешёвый флаг свежести.
    from services import wa_drafts
    _pending_draft = conv.pending_wa_draft
    if _pending_draft:
        try:
            _pending_draft = {**_pending_draft, "stale": wa_drafts.is_stale(db, conv)}
        except Exception:  # noqa: BLE001
            pass

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
        "pending_wa_draft": _pending_draft,
        "pending_call_suggestion": getattr(conv, "pending_call_suggestion", None),
        "wa_autonomous": bool(getattr(conv, "wa_autonomous", False)),
        # Текущий назначенный созвон (для ручного переноса/отмены из карточки).
        "booked_call_at": (cust.profile_data or {}).get("booked_call_at"),
        "call_medium": (cust.profile_data or {}).get("call_medium"),
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
# СОЗВОН — ручной перенос/отмена из панели (раньше только если лид сам напишет).
# Переиспользует те же функции, что и авто-бронь: cancel_call_actions (снять
# старые напоминания), write_call_booking + write_call_reminder (новое время),
# reminder_schedule/тексты из services.scheduling. Хранит booked_call_at в
# customer.profile_data — как делает живой поток в main.py.
# ============================================================================

class CallActionRequest(BaseModel):
    action: str                      # "reschedule" | "cancel"
    time: Optional[str] = None       # ISO datetime (для reschedule)


@router.post("/conversations/{conv_id}/call")
async def conversation_call(
    conv_id: str,
    req: CallActionRequest,
    _: None = Depends(_verify_member),
    db: Session = Depends(get_db),
):
    import asyncio
    from datetime import datetime, timezone
    import main as _main
    from services.scheduled_actions import (
        cancel_call_actions, write_call_booking, write_call_reminder,
    )
    from services import scheduling as _sched

    conv, cust = _get_conv_or_404(db, conv_id)
    prof = dict(cust.profile_data or {})

    # В обоих случаях снимаем старую бронь + напоминания (обратимо, в cancelled).
    await asyncio.to_thread(cancel_call_actions, str(conv.id))

    if req.action == "cancel":
        prof.pop("booked_call_at", None)
        prof.pop("call_medium", None)
        cust.profile_data = prof
        db.commit()
        return {"ok": True, "action": "cancel"}

    if req.action != "reschedule":
        raise HTTPException(status_code=400, detail="action: reschedule | cancel")
    if not req.time:
        raise HTTPException(status_code=400, detail="Нужно время (time) для переноса")
    new_dt = _parse_iso(req.time)
    if new_dt.tzinfo is None:
        new_dt = new_dt.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    if new_dt <= now:
        raise HTTPException(status_code=400, detail="Время должно быть в будущем")

    prof["booked_call_at"] = new_dt.isoformat()
    cust.profile_data = prof
    conv.lead_stage = "on_call"
    db.commit()

    chat = conv.channel_conversation_id
    medium = prof.get("call_medium")
    lead_name = cust.name or cust.email or "лид"
    contact = cust.email or ""
    lang = prof.get("lang") or "ru"
    is_msgr = (conv.channel or "website").lower() != "website"

    await asyncio.to_thread(
        write_call_booking,
        customer_id=str(cust.id), conversation_id=str(conv.id),
        channel=conv.channel, chat_id=str(chat) if chat else None,
        call_at=new_dt, medium=medium,
    )
    for fire, label in _sched.reminder_schedule(new_dt, now):
        if chat and is_msgr:
            await asyncio.to_thread(
                write_call_reminder,
                customer_id=str(cust.id), conversation_id=str(conv.id),
                channel=conv.channel, chat_id=str(chat), due_at=fire,
                text=_sched.lead_reminder_text(new_dt, label, medium, lang=lang,
                                              phone=str(chat) if chat else None),
                audience="lead",
            )
        if _main.settings.telegram_operator_group_id:
            await asyncio.to_thread(
                write_call_reminder,
                customer_id=str(cust.id), conversation_id=str(conv.id),
                channel=conv.channel, chat_id=str(_main.settings.telegram_operator_group_id),
                due_at=fire,
                text=_sched.admin_reminder_text(new_dt, lead_name, label, medium, contact),
                audience="admin",
            )
    return {"ok": True, "action": "reschedule", "call_at": new_dt.isoformat()}


class CallSuggestionRequest(BaseModel):
    action: str  # "confirm" | "dismiss"
    # Фолбэк: время предложения, которое ВИДИТ менеджер в карточке/попапе. Если фон
    # (брейн/дедуп) успел затереть conv.pending_call_suggestion между показом и кликом —
    # бронируем по этому значению, чтобы кнопка «Создать событие» всегда работала.
    at: Optional[str] = None
    medium: Optional[str] = None


@router.post("/conversations/{conv_id}/call-suggestion")
async def conversation_call_suggestion(
    conv_id: str,
    req: CallSuggestionRequest,
    _: None = Depends(_verify_member),
    db: Session = Depends(get_db),
):
    """Предложение созвона (бот распознал договорённость в переписке):
    confirm → создаём событие в календаре (та же логика, что ручная бронь) + чистим;
    dismiss → запоминаем, что отклонили это время (бот не предложит снова) + чистим."""
    from datetime import datetime, timezone
    import main as _main
    conv, cust = _get_conv_or_404(db, conv_id)
    sugg = getattr(conv, "pending_call_suggestion", None) or {}
    if req.action == "dismiss":
        prof = dict(cust.profile_data or {})
        if sugg.get("at"):
            prof["call_suggest_dismissed_at_val"] = sugg.get("at")
        cust.profile_data = prof
        conv.pending_call_suggestion = None
        db.commit()
        return {"ok": True, "action": "dismiss"}
    if req.action != "confirm":
        raise HTTPException(status_code=400, detail="action: confirm | dismiss")
    # Время берём из сохранённого предложения, иначе из запроса (то, что менеджер
    # видит в карточке) — кнопка работает даже если фон затёр поле между показом и кликом.
    at_raw = sugg.get("at") or req.at
    medium = sugg.get("medium") or req.medium
    if not at_raw:
        raise HTTPException(status_code=404, detail="Нет предложения созвона")
    try:
        new_dt = datetime.fromisoformat(str(at_raw).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="Некорректная дата созвона")
    if new_dt.tzinfo is None:
        new_dt = new_dt.replace(tzinfo=timezone.utc)
    new_dt = new_dt.astimezone(timezone.utc)
    from services.conversation_brain import _book  # бронь+напоминания+уведомление
    await _book(db, conv, cust, _main.settings, new_dt, medium)
    conv.pending_call_suggestion = None
    db.commit()
    return {"ok": True, "action": "confirm", "call_at": new_dt.isoformat()}


@router.get("/whatsapp/pending-suggestions")
async def whatsapp_pending_suggestions(
    _: None = Depends(_verify_member),
    db: Session = Depends(get_db),
):
    """Все диалоги с ОЖИДАЮЩИМ предложением созвона — для всплывающих уведомлений
    в панели (бот распознал договорённость, ждёт подтверждения менеджера)."""
    rows = (
        db.query(Conversation, Customer)
        .join(Customer, Conversation.customer_id == Customer.id)
        .filter(Conversation.pending_call_suggestion.isnot(None))
        .order_by(Conversation.last_message_at.desc().nullslast())
        .limit(20).all()
    )
    items = []
    for c, cust in rows:
        sugg = c.pending_call_suggestion or {}
        if not sugg.get("at"):
            continue
        items.append({
            "id": str(c.id),
            "name": _wa_display_name(cust, c),
            "when_human": sugg.get("when_human"),
            "at": sugg.get("at"),
        })
    return {"items": items}


@router.post("/conversations/{conv_id}/suggest-reply")
async def conversation_suggest_reply(
    conv_id: str,
    _: None = Depends(_verify_member),
    db: Session = Depends(get_db),
):
    """Сгенерировать предложенный ответ для ЭТОГО диалога и положить в
    pending_wa_draft → в карточке появится блок «🤖 Бот предлагает ответить»
    с кнопкой ✅ Отправить. Для любого лида по запросу (не только пакетно)."""
    import main as _main
    from services import wa_drafts

    conv, cust = _get_conv_or_404(db, conv_id)
    try:
        payload = await wa_drafts.generate_for_conv(
            db, conv, cust, _main.primary_llm, source="manual_suggest",
        )
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"LLM draft failed: {e}")
    if not payload:
        raise HTTPException(status_code=502, detail="LLM вернул пустой ответ")
    db.commit()
    return {"ok": True, "draft": payload["text"]}


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
# WHATSAPP — дедупликация fable-import лидов и @lid-диалогов по имени.
# Склеивает fable-import (channel_conversation_id = телефон, <13 символов,
# wa_classification.by == 'fable-import') с @lid-диалогами (id >= 13 символов,
# by == 'llm') РОВНО при ОДНОМ совпадении первого слова имени. Консервативно.
# execute=false — только предпросмотр; execute=true — применить слияние.
# ============================================================================

_LEAD_STAGE_ORDER = [
    "new_lead", "in_dialog", "qualified", "nda", "on_call", "tz_approved",
    "proposal", "prepayment", "in_work", "completed_won", "post_sale", "lost",
]


class WaDedupRequest(BaseModel):
    execute: bool = False
    # Бэклог: разрезолвить телефон у старых @lid-карточек без штампа (WAHA LID API,
    # СЕТЬ) — чтобы дедуп-по-телефону потом схлопнул их. Ограничено `limit`, чтобы
    # не держать коннект долго. Новые входящие штампуются сами в _handle_message.
    resolve_lids: bool = False
    limit: int = 15


@router.post("/whatsapp/dedup")
async def whatsapp_dedup(
    req: WaDedupRequest,
    _: None = Depends(_verify_owner),
    db: Session = Depends(get_db),
):
    """Дедупликация: склеить fable-import карточки с @lid-диалогами по первому
    слову имени. Консервативно — только при РОВНО ОДНОМ кандидате.
    execute=false — dry-run (только предпросмотр); execute=true — применить.
    resolve_lids=true — сперва добить телефоны у старых @lid-карточек (СЕТЬ, bounded
    limit) → потом дедуп-по-телефону их схлопнет."""
    from db.models import ConversationStatusEnum

    # БЭКЛОГ: разрезолвить телефон у старых @lid-карточек без штампа. Сетевые вызовы
    # WAHA LID API — отпускаем коннект (commit) ПЕРЕД сетью, чтобы не держать пул
    # (урок висов 06-15). Bounded по req.limit.
    resolved_lids: list = []
    if req.resolve_lids and req.execute:
        import main as _main
        from channels.waha import resolve_lid_phone
        st = _main.settings
        cands: list = []
        if getattr(st, "waha_base_url", None):
            _rows = (
                db.query(Conversation, Customer)
                .join(Customer, Conversation.customer_id == Customer.id)
                .filter(Conversation.channel == "whatsapp",
                        Conversation.status != ConversationStatusEnum.ARCHIVED)
                .all()
            )
            for _conv, _cust in _rows:
                _cid = _conv.channel_conversation_id or ""
                if len(_cid) >= 13 and not (_cust.phone or "").strip():
                    cands.append((str(_cust.id), _cid))
                if len(cands) >= max(1, min(req.limit, 50)):
                    break
        db.commit()  # отпустить коннект ПЕРЕД сетевыми вызовами
        for _cust_id, _cid in cands:
            try:
                _pn = await resolve_lid_phone(
                    st.waha_base_url, st.waha_api_key or "",
                    st.waha_session or "default", _cid)
            except Exception:  # noqa: BLE001
                _pn = None
            if not _pn:
                continue
            _c = db.query(Customer).filter(Customer.id == _cust_id).first()
            if _c is not None and not (_c.phone or "").strip():
                _c.phone = ("+" + _pn)[:50]
                db.commit()
                resolved_lids.append({"customer": _cust_id, "phone": _pn})

    # Загружаем все WhatsApp-диалоги с Customer-ом
    all_convs = (
        db.query(Conversation, Customer)
        .join(Customer, Conversation.customer_id == Customer.id)
        .filter(Conversation.channel == "whatsapp")
        .all()
    )

    # Разбиваем на fable-import (телефон, <13 символов) и @lid (>= 13 символов, llm)
    fable_convs = []
    lid_convs = []
    for conv, cust in all_convs:
        cid = conv.channel_conversation_id or ""
        wac = conv.wa_classification or {}
        by = wac.get("by", "")
        if by == "fable-import" and len(cid) < 13:
            fable_convs.append((conv, cust))
        elif len(cid) >= 13 and by == "llm":
            lid_convs.append((conv, cust))

    merges = []
    skipped = []

    for fconv, fcust in fable_convs:
        fname = (fcust.name or "").strip()
        if not fname:
            skipped.append({"name": "(нет имени)", "fable_id": str(fconv.id), "reason": "пустое имя"})
            continue
        core = fname.split()[0].lower()

        candidates = [
            (lconv, lcust)
            for lconv, lcust in lid_convs
            if (lcust.name or "").strip().split()[0:1] and
               (lcust.name or "").strip().split()[0].lower() == core
        ]

        if len(candidates) == 0:
            skipped.append({"name": fname, "fable_id": str(fconv.id), "reason": "нет кандидатов среди @lid"})
            continue
        if len(candidates) > 1:
            names = [c.name for _, c in candidates]
            skipped.append({"name": fname, "fable_id": str(fconv.id),
                            "reason": f"неоднозначно: {len(candidates)} кандидата — {names}"})
            continue

        lconv, lcust = candidates[0]
        action_taken = []

        if req.execute:
            # 1. pending_wa_draft: перенести из fable в lid если у lid нет своего
            if fconv.pending_wa_draft and not lconv.pending_wa_draft:
                lconv.pending_wa_draft = fconv.pending_wa_draft
                action_taken.append("pending_wa_draft перенесён")

            # 2. wa_classification: дописать demo_url/note в summary lid если пусто
            fwac = fconv.wa_classification or {}
            demo = fwac.get("demo_url")
            note = fwac.get("note")
            if demo or note:
                bits = []
                if demo: bits.append(f"Демо (из fable): {demo}")
                if note: bits.append(f"Заметка (из fable): {note}")
                addition = " · ".join(bits)
                if not (lconv.summary or "").strip():
                    lconv.summary = addition[:2000]
                    action_taken.append("summary дополнен из fable")

            # 3. lead_stage: поднять до fable-стадии если она «дальше» по воронке
            try:
                fstage_idx = _LEAD_STAGE_ORDER.index(fconv.lead_stage or "new_lead")
            except ValueError:
                fstage_idx = 0
            try:
                lstage_idx = _LEAD_STAGE_ORDER.index(lconv.lead_stage or "new_lead")
            except ValueError:
                lstage_idx = 0
            if fstage_idx > lstage_idx:
                lconv.lead_stage = fconv.lead_stage
                action_taken.append(f"lead_stage поднят до {fconv.lead_stage}")

            # 4. fable-conv → ARCHIVED (обратимо, не удалять)
            fconv.status = ConversationStatusEnum.ARCHIVED
            fconv.summary = ((fconv.summary or "") + f" → слит в {lconv.id}").strip()[:2000]
            action_taken.append("fable-conv архивирован")

            db.flush()

        merges.append({
            "name": fname,
            "fable_id": str(fconv.id),
            "lid_id": str(lconv.id),
            "lid_cid": lconv.channel_conversation_id,
            "action": ", ".join(action_taken) if action_taken else "dry-run",
        })

    # Проход B: дедуп по штампованному телефону (чисто БД) — схлопывает @lid-
    # дубли с телефонными двойниками. Идёт ПОСЛЕ resolve_lids, чтобы поймать
    # только что добитые номера.
    phone_dedup = {"groups": 0, "archived": 0, "pairs": []}
    name_dedup = {"groups": 0, "archived": 0, "pairs": []}
    orphan_actions = {"superseded": 0}
    task_dups = {"superseded": 0}
    if req.execute:
        from services.whatsapp_sync import (
            dedup_wa_by_phone, dedup_wa_by_name, cancel_orphan_scheduled_actions,
            dedup_scheduled_actions,
        )
        phone_dedup = dedup_wa_by_phone(db)
        # + дедуп «@lid-тени» по имени (телефон не разрезолвлен у @lid-карточки).
        name_dedup = dedup_wa_by_name(db)
        # Погасить задачи/напоминания всех архивных карточек + дедуп одинаковых задач
        # («Лид завис — связаться» по 2-3 на лида) → чистый задачник/календарь.
        orphan_actions = cancel_orphan_scheduled_actions(db)
        task_dups = dedup_scheduled_actions(db)
        db.commit()

    return {
        "ok": True,
        "execute": req.execute,
        "merges": merges,
        "skipped": skipped,
        "resolved_lids": resolved_lids,
        "phone_dedup": phone_dedup,
        "name_dedup": name_dedup,
        "orphan_actions": orphan_actions,
        "task_dups": task_dups,
    }


# ============================================================================
# WHATSAPP — пакетная подготовка ОТВЕТОВ на одобрение. Для каждого активного
# лида генерируем «лучший следующий ответ» (по переписке + стадии) и кладём в
# pending_wa_draft → всплывёт в карточке с кнопками ✅/🚫. Оператор просматривает
# и одобряет. Фоновая задача (LLM по каждому лиду), прогресс в _WA_DRAFTS_STATE.
# ============================================================================

_WA_DRAFTS_STATE: dict = {
    "running": False, "started_at": None, "finished_at": None,
    "prepared": 0, "skipped": 0, "errors": 0, "total": 0, "error": None,
}


async def _run_prepare_drafts_bg(overwrite: bool, exclude_phones: list[str] | None = None) -> None:
    import main as _main
    import re as _re
    from datetime import datetime, timezone
    from db.connection import session_scope

    # Нормализуем exclude_phones до цифр для сравнения
    _excluded = {_re.sub(r"\D", "", p) for p in (exclude_phones or []) if p}

    _WA_DRAFTS_STATE.update({
        "running": True, "started_at": datetime.now(timezone.utc).isoformat(),
        "finished_at": None, "prepared": 0, "skipped": 0, "errors": 0,
        "total": 0, "error": None,
    })
    try:
        with session_scope() as db:
            rows = (
                db.query(Conversation, Customer)
                .join(Customer, Conversation.customer_id == Customer.id)
                .filter(Conversation.channel == "whatsapp")
                .filter(Conversation.status != ConversationStatusEnum.ARCHIVED.value)
                .all()
            )
            # активные диалоги: НЕ «проигран/сдано». Берём всех, КРОМЕ явно
            # помеченных «не лид» (wa_classification.is_lead == False). Живые
            # диалоги без классификации (пришли вебхуком) ВКЛЮЧАЕМ — иначе их
            # черновики не обновляются персоной/оффером (баг: раньше требовали is_lead).
            targets = []
            for conv, cust in rows:
                wac = conv.wa_classification or {}
                if wac.get("is_lead") is False:  # явно не лид (триаж) — пропускаем
                    continue
                if (conv.lead_stage or "") in ("lost", "completed_won"):
                    continue
                if conv.pending_wa_draft and not overwrite:
                    continue
                if not conv.channel_conversation_id:
                    continue
                # Пропускаем номера из списка исключений
                if _excluded and _re.sub(r"\D", "", conv.channel_conversation_id or "") in _excluded:
                    continue
                targets.append((conv, cust))
            _WA_DRAFTS_STATE["total"] = len(targets)

            from services import wa_drafts
            import asyncio as _aio_pd
            for conv, cust in targets:
                # Пауза между лидами: пакет не «бежит» по всем разом, уступает
                # event loop (health/вебхуки отвечают) и не давит на пул/LLM.
                await _aio_pd.sleep(0.6)
                try:
                    payload = await wa_drafts.generate_for_conv(
                        db, conv, cust, _main.primary_llm, source="batch_prepare",
                    )
                    if not payload:
                        _WA_DRAFTS_STATE["skipped"] += 1
                        continue
                    db.commit()
                    _WA_DRAFTS_STATE["prepared"] += 1
                except Exception as e:  # noqa: BLE001
                    db.rollback()
                    _WA_DRAFTS_STATE["errors"] += 1
                    log.warning(f"[prepare-drafts] lead {conv.id} failed: {e}")
    except Exception as e:  # noqa: BLE001
        log.error(f"[prepare-drafts] run failed: {e}")
        _WA_DRAFTS_STATE["error"] = str(e)
    finally:
        from datetime import datetime as _dt, timezone as _tz
        _WA_DRAFTS_STATE["running"] = False
        _WA_DRAFTS_STATE["finished_at"] = _dt.now(_tz.utc).isoformat()


class PrepareDraftsRequest(BaseModel):
    overwrite: bool = False
    exclude_phones: list[str] = []


@router.post("/whatsapp/prepare-drafts")
async def whatsapp_prepare_drafts(
    req: PrepareDraftsRequest,
    _: None = Depends(_verify_owner),
):
    """Сгенерировать черновики ответов для всех активных лидов (в фоне).
    По умолчанию пропускает диалоги, где уже есть черновик (overwrite=true — пересоздать).
    exclude_phones — список номеров (в любом формате), которые нужно пропустить."""
    import asyncio
    if _WA_DRAFTS_STATE.get("running"):
        return {"ok": True, "already_running": True, "state": _WA_DRAFTS_STATE}
    asyncio.create_task(_run_prepare_drafts_bg(bool(req.overwrite), list(req.exclude_phones)))
    return {"ok": True, "started": True}


@router.get("/whatsapp/drafts-status")
async def whatsapp_drafts_status(_: None = Depends(_verify_member)):
    """Прогресс пакетной подготовки черновиков ответов."""
    return _WA_DRAFTS_STATE


# ============================================================================
# МАССОВЫЙ ДОЖИМ СПЯЩИХ (под контролем): бот находит молчунов, для них уже готовы
# черновики (prepare-drafts) → менеджер смотрит список и одобряет (пачкой/точечно).
# Отправка фоном, троттл анти-бан, БЕЗ удержания DB-сессии во время пауз.
# ============================================================================

_WA_SLEEP_TEMP_PRI = {"ready": 4, "hot": 3, "warm": 2, "cold": 1}


@router.get("/whatsapp/sleeping")
async def whatsapp_sleeping(
    hours: int = 24,
    limit: int = 80,
    _: None = Depends(_verify_member),
    db: Session = Depends(get_db),
):
    """Спящие лиды = активные whatsapp-карточки, молчащие дольше `hours` часов
    (last_message_at старее порога). С готовым черновиком — сверху."""
    from db.models import ConversationStatusEnum
    from datetime import datetime as _dt, timedelta as _td, timezone as _tz
    cutoff = _dt.now(_tz.utc) - _td(hours=max(1, hours))
    rows = (
        db.query(Conversation, Customer)
        .join(Customer, Conversation.customer_id == Customer.id)
        .filter(Conversation.channel == "whatsapp",
                Conversation.status != ConversationStatusEnum.ARCHIVED,
                Conversation.lead_stage.in_(list(_ACTIVE_STAGES)),
                Conversation.last_message_at < cutoff)
        .order_by(Conversation.last_message_at.desc().nullslast())
        .limit(min(limit, 200)).all()
    )
    now = _dt.now(_tz.utc)
    items = []
    for conv, c in rows:
        pend = conv.pending_wa_draft or {}
        lm = conv.last_message_at
        hrs = int((now - lm).total_seconds() // 3600) if lm else None
        items.append({
            "conversation_id": str(conv.id),
            "name": _wa_display_name(c, conv),
            "stage": conv.lead_stage,
            "stage_label": _STAGE_LABEL.get(conv.lead_stage or "", conv.lead_stage or ""),
            "temperature": c.lead_temperature,
            "hours_silent": hrs,
            "draft": (pend.get("text") or "")[:600],
            "has_draft": bool(pend.get("text")),
            "_pri": _WA_SLEEP_TEMP_PRI.get((c.lead_temperature or "").lower(), 0),
        })
    items.sort(key=lambda x: (not x["has_draft"], -x["_pri"]))
    for it in items:
        it.pop("_pri", None)
    return {"items": items, "count": len(items),
            "ready": sum(1 for i in items if i["has_draft"])}


_WA_SEND_STATE: dict = {
    "running": False, "started_at": None, "finished_at": None,
    "sent": 0, "skipped": 0, "errors": 0, "total": 0, "error": None,
}


async def _run_send_sleeping_bg(conv_ids: list) -> None:
    """Фон: отправить готовые черновики пачке диалогов. Троттл в _wa_send (анти-бан).
    DB-сессию НЕ держим во время паузы-троттла — читаем/пишем короткими сессиями."""
    import main as _main
    from db.connection import session_scope
    from services.conversations import append_message
    from datetime import datetime as _dt, timezone as _tz
    _WA_SEND_STATE.update({"running": True, "started_at": _dt.now(_tz.utc).isoformat(),
                           "finished_at": None, "sent": 0, "skipped": 0, "errors": 0,
                           "total": len(conv_ids), "error": None})
    try:
        for cid in conv_ids:
            try:
                with session_scope() as db:  # 1) прочитать черновик (короткая сессия)
                    conv = db.get(Conversation, UUID(cid))
                    if conv is None:
                        _WA_SEND_STATE["skipped"] += 1
                        continue
                    pend = dict(conv.pending_wa_draft or {})
                    to = pend.get("to_wa_id") or conv.channel_conversation_id or ""
                text = (pend.get("text") or "").strip()
                if not text or not to:
                    _WA_SEND_STATE["skipped"] += 1
                    continue
                delivered = await _main._wa_send(to, text, pend.get("phone_number_id") or "")  # троттл, сессия НЕ держится
                with session_scope() as db:  # 3) записать факт + снять черновик
                    conv = db.get(Conversation, UUID(cid))
                    if conv is not None:
                        append_message(db, conv.id, role="assistant", content=text,
                                       extra_meta={"approved_via": "mass-sleeping", "delivered": delivered})
                        conv.pending_wa_draft = None
                _WA_SEND_STATE["sent"] += 1
            except Exception as e:  # noqa: BLE001
                _WA_SEND_STATE["errors"] += 1
                log.warning(f"[send-sleeping] {str(cid)[:8]} failed: {e}")
    except Exception as e:  # noqa: BLE001
        _WA_SEND_STATE["error"] = str(e)
    finally:
        _WA_SEND_STATE["running"] = False
        from datetime import datetime as _dt2, timezone as _tz2
        _WA_SEND_STATE["finished_at"] = _dt2.now(_tz2.utc).isoformat()


class SendSleepingRequest(BaseModel):
    conversation_ids: list = []   # пусто = все спящие с готовым черновиком
    hours: int = 24


@router.post("/whatsapp/send-sleeping")
async def whatsapp_send_sleeping(
    req: SendSleepingRequest,
    _: None = Depends(_verify_owner),
    db: Session = Depends(get_db),
):
    """Под контролем: отправить ОДОБРЕННЫЕ (готовые) черновики пачке спящих лидов.
    Если ids пусто — всем спящим с готовым черновиком. Фон + троттл (анти-бан)."""
    import asyncio
    if _WA_SEND_STATE.get("running"):
        return {"ok": True, "already_running": True, "state": _WA_SEND_STATE}
    ids = [str(x) for x in (req.conversation_ids or [])]
    if not ids:
        from db.models import ConversationStatusEnum
        from datetime import datetime as _dt, timedelta as _td, timezone as _tz
        cutoff = _dt.now(_tz.utc) - _td(hours=max(1, req.hours))
        rows = (
            db.query(Conversation.id)
            .filter(Conversation.channel == "whatsapp",
                    Conversation.status != ConversationStatusEnum.ARCHIVED,
                    Conversation.lead_stage.in_(list(_ACTIVE_STAGES)),
                    Conversation.last_message_at < cutoff,
                    Conversation.pending_wa_draft.isnot(None))
            .limit(100).all()
        )
        ids = [str(r[0]) for r in rows]
    if not ids:
        return {"ok": True, "started": False, "reason": "нет готовых черновиков"}
    ids = ids[:100]
    asyncio.create_task(_run_send_sleeping_bg(ids))
    return {"ok": True, "started": True, "total": len(ids)}


@router.get("/whatsapp/send-status")
async def whatsapp_send_status(_: None = Depends(_verify_member)):
    """Прогресс массовой отправки дожима спящим."""
    return _WA_SEND_STATE


class CleanPhantomsRequest(BaseModel):
    execute: bool = False


@router.post("/whatsapp/clean-phantoms")
async def whatsapp_clean_phantoms(
    req: CleanPhantomsRequest,
    _: None = Depends(_verify_owner),
    db: Session = Depends(get_db),
):
    """Удалить «фантомные» сообщения бота в WhatsApp-диалогах — те, что бот
    сгенерил, но в WhatsApp их НЕ было: role=assistant без подтверждения
    отправки (нет waha_id из истории, не одобрено оператором approved_via,
    не доставлено delivered). Эти сообщения сбивают с толку — их в мессенджере
    не существует. Системные [ADMIN]-заметки (role=system) НЕ трогаем.
    execute=false — только посчитать; execute=true — удалить."""
    rows = (
        db.query(Message)
        .join(Conversation, Message.conversation_id == Conversation.id)
        .filter(Conversation.channel == "whatsapp")
        .filter(Message.role == "assistant")
        .all()
    )
    victims = []
    for m in rows:
        meta = m.extra_meta or {}
        if meta.get("waha_id") or meta.get("approved_via") or meta.get("delivered"):
            continue  # подтверждённо реальное — оставляем
        victims.append(m)
    samples = [(m.content or "")[:70] for m in victims[:10]]
    if req.execute:
        for m in victims:
            db.delete(m)
        db.commit()
    return {"ok": True, "execute": req.execute, "count": len(victims), "samples": samples}


@router.post("/whatsapp/brain-sweep")
async def whatsapp_brain_sweep(
    since_minutes: int = 1440,
    force: bool = False,
    limit: int = 25,
    _: None = Depends(_verify_member),
):
    """Умное авто-ведение: пройтись по недавним WhatsApp-диалогам СЕЙЧАС —
    подвинуть воронку и поставить созвоны из договорённостей (в т.ч. ручных).
    Обычно крутится в кроне каждые ~10 мин; эндпоинт для ручного запуска/теста.
    force=true — переанализировать ДАЖЕ диалоги без новых сообщений (например после
    смены логики мозга — добронировать старые договорённости)."""
    import main as _main
    from services.conversation_brain import sweep_recent
    res = await sweep_recent(_main.primary_llm, _main.settings,
                             since_minutes=since_minutes, limit=limit, force=force)
    return {"ok": True, **res}


@router.post("/whatsapp/recheck-suggestions")
async def whatsapp_recheck_suggestions(
    _: None = Depends(_verify_owner),
    db: Session = Depends(get_db),
):
    """Прицельно перепроверить ВСЕ карточки С предложением созвона (а не только
    «недавние», как brain-sweep): мозг переоценивает, есть ли реальная договорённость
    о ЗВОНКЕ — ложные/устаревшие снимает (кейс Вячеслав). Bounded, своя сессия на лида
    (анти-вис). Подтверждённые брони (booked_call_at) не трогает."""
    from db.connection import session_scope
    from services.conversation_brain import analyze_and_advance
    import main as _main
    ids = [
        str(r[0]) for r in db.query(Conversation.id)
        .filter(Conversation.pending_call_suggestion.isnot(None)).limit(80).all()
    ]
    db.commit()
    out = {"checked": 0, "cleared": 0, "kept": 0}
    for cid in ids:
        try:
            with session_scope() as s:
                conv = s.get(Conversation, UUID(cid))
                if conv is None:
                    continue
                cust = s.get(Customer, conv.customer_id)
                res = await analyze_and_advance(s, conv, cust, _main.primary_llm,
                                                _main.settings, refresh_draft=False)
                out["checked"] += 1
                if res.get("cleared_suggestion"):
                    out["cleared"] += 1
                elif res.get("suggested"):
                    out["kept"] += 1
        except Exception as e:  # noqa: BLE001
            log.warning(f"recheck-suggestion {cid[:8]}: {e}")
    return out


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
    from db.models import ConversationStatusEnum
    now = datetime.now(timezone.utc)
    eod = now.replace(hour=23, minute=59, second=59)
    week = now + timedelta(days=7)

    # Исключаем задачи АРХИВНЫХ карточек (дубли, слитые дедупом) — иначе осиротевшие
    # задачи/напоминания висят в «Мой день» как просрочка, хотя карточки уже нет.
    rows = (
        db.query(ScheduledAction, Customer)
        .join(Customer, ScheduledAction.customer_id == Customer.id)
        .outerjoin(Conversation, ScheduledAction.conversation_id == Conversation.id)
        .filter(ScheduledAction.status.in_(("pending", "processing")))
        .filter(ScheduledAction.due_at <= week)
        .filter((Conversation.id.is_(None)) |
                (Conversation.status != ConversationStatusEnum.ARCHIVED))
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
        .filter(Conversation.status != ConversationStatusEnum.ARCHIVED)
        .limit(100)
        .all()
    )
    # Дедуп созвонов: один человек может иметь ДВЕ карточки-контакта (рекламный @lid
    # + импорт по телефону) → созвон дублировался («Денис ×2»). Ключ — телефон, иначе
    # имя, иначе id. Приоритет карточке с назначенным временем (booked_call_at).
    seen_call: dict = {}
    for c, conv in custs:
        booked = (c.profile_data or {}).get("booked_call_at")
        phone = "".join(ch for ch in (getattr(c, "phone", None) or "") if ch.isdigit())
        key = phone or (c.name or "").strip().lower() or str(c.id)
        if key in seen_call:
            # уже есть — заменяем только если у нового есть время, а у старого нет
            if booked and not seen_call[key].get("call_at"):
                seen_call[key]["call_at"] = booked
                seen_call[key]["medium"] = (c.profile_data or {}).get("call_medium")
            continue
        medium = (c.profile_data or {}).get("call_medium")
        seen_call[key] = {
            "customer": {"id": str(c.id), "name": c.name, "email": c.email},
            "conversation_id": str(conv.id),
            "channel": conv.channel,
            "call_at": booked,
            "medium": medium,
        }
    calls = list(seen_call.values())

    return {"overdue": overdue, "today": today, "upcoming": upcoming, "calls": calls}


@router.get("/calendar-events")
async def calendar_events(
    start: str,
    end: str,
    _: None = Depends(_verify_member),
    db: Session = Depends(get_db),
):
    """События календаря за ПРОИЗВОЛЬНЫЙ диапазон [start, end) — для FullCalendar
    (месяц/неделя/день подгружают свой видимый период). Созвоны (booked_call_at) +
    задачи (scheduled_actions.due_at). Архивные карточки исключены."""
    from db.models import ConversationStatusEnum
    try:
        rng_start = _parse_iso(start)
        rng_end = _parse_iso(end)
    except Exception:
        raise HTTPException(status_code=422, detail="start/end must be ISO datetimes")

    events: list = []
    # 1) Задачи/напоминания (due_at в диапазоне), не из архивных карточек
    rows = (
        db.query(ScheduledAction, Customer)
        .join(Customer, ScheduledAction.customer_id == Customer.id)
        .outerjoin(Conversation, ScheduledAction.conversation_id == Conversation.id)
        .filter(ScheduledAction.status.in_(("pending", "processing")))
        .filter(ScheduledAction.due_at >= rng_start, ScheduledAction.due_at < rng_end)
        .filter((Conversation.id.is_(None)) |
                (Conversation.status != ConversationStatusEnum.ARCHIVED))
        .order_by(ScheduledAction.due_at.asc())
        .limit(500)
        .all()
    )
    for a, c in rows:
        if not a.due_at:
            continue
        if a.action_type == "call_booked":
            continue  # сам созвон показываем из booked_call_at (ниже) — не дублируем
        # JUNK: контакт без имени И без телефона И без email — мусор (WhatsApp-статусы/
        # рассылки), не показываем как событие (кейс «!!!!!!!» без номера).
        if not ((c.name or "").strip() or getattr(c, "phone", None) or (c.email or "").strip()):
            continue
        text = (a.payload or {}).get("text") or (a.payload or {}).get("title") or ""
        if a.action_type == "call_reminder":
            kind, icon = "reminder", "⏰"
        elif a.executor == "bot":
            kind, icon = "bot", "🤖"
        else:
            kind, icon = "task", "📋"
        events.append({
            "id": "task-" + str(a.id),
            "kind": kind,
            "title": f"{icon} {c.name or 'Лид'}: {text[:44]}",
            "start": a.due_at.isoformat(),
            "conversation_id": str(a.conversation_id) if a.conversation_id else None,
            "action_id": str(a.id),
        })

    # 2) Назначенные созвоны (booked_call_at в диапазоне), дедуп по тел./имени
    custs = (
        db.query(Customer, Conversation)
        .join(Conversation, Conversation.customer_id == Customer.id)
        .filter(Customer.profile_data.isnot(None))
        .filter(Conversation.lead_stage == "on_call")
        .filter(Conversation.status != ConversationStatusEnum.ARCHIVED)
        .limit(300)
        .all()
    )
    seen_call: set = set()
    for c, conv in custs:
        booked = (c.profile_data or {}).get("booked_call_at")
        if not booked:
            continue
        try:
            bdt = _parse_iso(str(booked))
        except Exception:
            continue
        if not (rng_start <= bdt < rng_end):
            continue
        phone = "".join(ch for ch in (getattr(c, "phone", None) or "") if ch.isdigit())
        key = phone or (c.name or "").strip().lower() or str(c.id)
        if key in seen_call:
            continue
        seen_call.add(key)
        medium = (c.profile_data or {}).get("call_medium")
        events.append({
            "id": "call-" + str(conv.id),
            "kind": "call",
            "title": f"📞 {c.name or c.email or 'Лид'}{' · ' + medium if medium else ''}",
            "start": bdt.isoformat(),
            "conversation_id": str(conv.id),
            "action_id": None,
        })

    return {"events": events}


# ============================================================================
# ЗАДАЧНИК В СТИЛЕ CRM (amoCRM/Kommo): у каждого активного лида должна быть
# СЛЕДУЮЩАЯ задача. Лид без задачи = забытый лид → выводим отдельно. Приоритет
# по температуре + стадии. Видно, что бот делает сам, а что — администратор.
# ============================================================================

_ACTIVE_STAGES = {"new_lead", "in_dialog", "qualified", "nda", "on_call",
                  "tz_approved", "proposal", "prepayment", "in_work"}
_STAGE_LABEL = {
    "new_lead": "Новый", "in_dialog": "Диалог", "qualified": "Квалифицирован",
    "nda": "NDA", "on_call": "Созвон назначен", "tz_approved": "ТЗ согласовано",
    "proposal": "КП", "prepayment": "Предоплата", "in_work": "В работе",
    "completed_won": "Выиграно", "lost": "Потеряно", "post_sale": "Постпродажа",
}
# Что делать дальше на каждой стадии + может ли это сделать бот сам.
_NEXT_ACTION = {
    "new_lead": ("Квалифицировать: выяснить задачу", True),
    "in_dialog": ("Выяснить задачу проекта", True),
    "qualified": ("Назначить созвон / подготовить КП", True),
    "nda": ("Подписать NDA", False),
    "on_call": ("Провести/подтвердить созвон", False),
    "tz_approved": ("Подготовить КП", False),
    "proposal": ("Дожать по КП", True),
    "prepayment": ("Получить предоплату", False),
    "in_work": ("Вести проект", False),
}
_TEMP_PRI = {"ready": 4, "hot": 3, "warm": 2, "cold": 1}
_BOT_ACTIONS = {"followup_message", "warming_touch"}


@router.get("/task-board")
async def task_board(
    _: None = Depends(_verify_member),
    db: Session = Depends(get_db),
):
    """Доска задач (CRM-логика). Бакеты по срочности с контекстом лида (стадия,
    температура) + блок «Лиды без задачи». Не показываем задачи архивных карточек."""
    from db.models import ConversationStatusEnum
    now = datetime.now(timezone.utc)
    eod = now.replace(hour=23, minute=59, second=59)
    eod_tom = eod + timedelta(days=1)
    eow = now + timedelta(days=7)

    rows = (
        db.query(ScheduledAction, Customer, Conversation)
        .join(Customer, ScheduledAction.customer_id == Customer.id)
        .outerjoin(Conversation, ScheduledAction.conversation_id == Conversation.id)
        .filter(ScheduledAction.status.in_(("pending", "processing")))
        .filter((Conversation.id.is_(None)) |
                (Conversation.status != ConversationStatusEnum.ARCHIVED))
        .order_by(ScheduledAction.due_at.asc())
        .limit(500)
        .all()
    )

    def pri(temp: Optional[str], stage: Optional[str]) -> int:
        try:
            si = _LEAD_STAGE_ORDER.index(stage or "new_lead")
        except ValueError:
            si = 0
        return _TEMP_PRI.get((temp or "").lower(), 0) * 100 + si

    def pack(a: ScheduledAction, c: Customer, conv: Optional[Conversation]) -> dict:
        stage = conv.lead_stage if conv else None
        temp = c.lead_temperature
        return {
            "id": str(a.id),
            "who": "bot" if a.executor == "bot" else "human",
            "can_bot": a.action_type in _BOT_ACTIONS,
            "action_type": a.action_type,
            "text": (a.payload or {}).get("text") or (a.payload or {}).get("title") or "",
            "due_at": a.due_at.isoformat() if a.due_at else None,
            "conversation_id": str(a.conversation_id) if a.conversation_id else None,
            "name": c.name or c.email or "Лид",
            "stage": stage, "stage_label": _STAGE_LABEL.get(stage or "", stage or ""),
            "temperature": temp,
            "channel": a.channel,
            "priority": pri(temp, stage),
        }

    buckets = {"overdue": [], "today": [], "tomorrow": [], "week": [], "later": []}
    convs_with_task: set = set()
    for a, c, conv in rows:
        if a.conversation_id:
            convs_with_task.add(a.conversation_id)
        due = a.due_at
        if due and due.tzinfo is None:
            due = due.replace(tzinfo=timezone.utc)
        item = pack(a, c, conv)
        if not due:
            buckets["later"].append(item)
        elif due < now:
            buckets["overdue"].append(item)
        elif due <= eod:
            buckets["today"].append(item)
        elif due <= eod_tom:
            buckets["tomorrow"].append(item)
        elif due <= eow:
            buckets["week"].append(item)
        else:
            buckets["later"].append(item)
    for k in buckets:
        buckets[k].sort(key=lambda x: (-x["priority"], x["due_at"] or ""))

    # Лиды БЕЗ задачи — активная стадия, не архив, нет pending-действия.
    active_convs = (
        db.query(Conversation, Customer)
        .join(Customer, Conversation.customer_id == Customer.id)
        .filter(Conversation.status != ConversationStatusEnum.ARCHIVED)
        .filter(Conversation.lead_stage.in_(list(_ACTIVE_STAGES)))
        .order_by(Conversation.last_message_at.desc().nullslast())
        .limit(400)
        .all()
    )
    no_task = []
    for conv, c in active_convs:
        if conv.id in convs_with_task:
            continue
        # Умный next_action (мозг разобрал диалог) — приоритетнее статичного по стадии.
        na = getattr(conv, "next_action", None) or {}
        stage_nxt, bot_ok = _NEXT_ACTION.get(conv.lead_stage or "new_lead", ("Решить следующий шаг", False))
        no_task.append({
            "conversation_id": str(conv.id),
            "name": c.name or c.email or (("+" + c.phone) if getattr(c, "phone", None) else "Лид"),
            "stage": conv.lead_stage,
            "stage_label": _STAGE_LABEL.get(conv.lead_stage or "", conv.lead_stage or ""),
            "temperature": c.lead_temperature,
            "channel": conv.channel,
            "last_message_at": conv.last_message_at.isoformat() if conv.last_message_at else None,
            "next_action": na.get("label") or stage_nxt,
            "mode": na.get("mode"),          # bot_auto|needs_approval|human|reengage|wait|unclear (None = не разобран)
            "kind": na.get("kind"),
            "draft": (na.get("draft") or "")[:400] if na.get("draft") else "",
            "reason": na.get("reason") or "",
            "analyzed": bool(na),
            "bot_can": bot_ok,
            "wa_autonomous": bool(getattr(conv, "wa_autonomous", False)),
            "priority": pri(c.lead_temperature, conv.lead_stage),
        })
    # Сначала неразобранные/срочные (по приоритету), unclear (нужна помощь) — выше.
    no_task.sort(key=lambda x: (x["mode"] != "unclear", -x["priority"]))

    return {
        "summary": {
            "overdue": len(buckets["overdue"]), "today": len(buckets["today"]),
            "no_task": len(no_task),
            "bot": sum(1 for b in buckets.values() for t in b if t["who"] == "bot"),
            "human": sum(1 for b in buckets.values() for t in b if t["who"] == "human"),
        },
        "buckets": buckets,
        "no_task_leads": no_task[:60],
    }


class GenerateNextRequest(BaseModel):
    limit: int = 8


@router.post("/task-board/generate")
async def task_board_generate(
    req: GenerateNextRequest,
    _: None = Depends(_verify_owner),
    db: Session = Depends(get_db),
):
    """Мозг разбирает лидов БЕЗ задачи и формирует умный следующий шаг (next_action)
    по каждому: дожать молчуна / ответить / передать человеку / (если не ясно) →
    пас администратору. Bounded (req.limit), КАЖДЫЙ лид в СВОЕЙ короткой сессии —
    LLM не держит общий пул (анти-вис). Заодно чистит просроченные фантом-созвоны."""
    from db.models import ConversationStatusEnum
    from db.connection import session_scope
    from services.next_action import generate_next_action
    import main as _main

    lim = max(1, min(req.limit, 20))
    # 1) собрать id лидов без задачи (активные, не архив, нет pending-действия)
    have_task = {
        r[0] for r in db.query(ScheduledAction.conversation_id)
        .filter(ScheduledAction.status.in_(("pending", "processing")),
                ScheduledAction.conversation_id.isnot(None)).all()
    }
    rows = (
        db.query(Conversation.id)
        .filter(Conversation.status != ConversationStatusEnum.ARCHIVED)
        .filter(Conversation.lead_stage.in_(list(_ACTIVE_STAGES)))
        .order_by(Conversation.last_message_at.desc().nullslast())
        .limit(400).all()
    )
    cand = [str(r[0]) for r in rows if r[0] not in have_task][:lim]
    db.commit()  # отпустить коннект перед LLM-циклом

    counts: dict = {}
    now = datetime.now(timezone.utc)
    for cid in cand:
        try:
            with session_scope() as s:
                conv = s.get(Conversation, UUID(cid))
                if conv is None:
                    continue
                cust = s.get(Customer, conv.customer_id)
                # почистить просроченный фантом-созвон (Zaal-кейс)
                sugg = conv.pending_call_suggestion or {}
                if sugg.get("at"):
                    try:
                        sat = datetime.fromisoformat(str(sugg["at"]).replace("Z", "+00:00"))
                        if sat.tzinfo is None:
                            sat = sat.replace(tzinfo=timezone.utc)
                        if sat < now:
                            conv.pending_call_suggestion = None
                    except (ValueError, TypeError):
                        pass
                na = await generate_next_action(s, conv, cust, _main.primary_llm)
                m = (na or {}).get("mode") or "skip"
                counts[m] = counts.get(m, 0) + 1
        except Exception as e:  # noqa: BLE001
            log.warning(f"next_action gen failed {cid[:8]}: {e}")
    return {"ok": True, "processed": len(cand), "by_mode": counts}


# ============================================================================
# БЭКАП БД — портативный дамп (все переписки/статусы/задачи). Скачать вручную или
# отправить в Telegram владельцу (offsite-копия). Авто-бэкап раз в день — в кроне.
# ============================================================================

def _owner_tg() -> tuple:
    import main as _main
    from services import bot_settings as _bs
    st = _main.settings
    chat = (_bs.get("manager_chat_id") or "").strip() or (getattr(st, "telegram_chat_id", None) or "")
    token = getattr(st, "telegram_bot_token", None)
    return token, chat


@router.get("/db-backup")
async def db_backup_download(_: None = Depends(_verify_owner)):
    """Скачать полный бэкап БД (gzip JSON): все переписки, статусы, стадии, задачи,
    правила. Обход БД в потоке (не блокирует event loop)."""
    import asyncio
    from fastapi import Response
    from services.db_backup import build_export
    fname, blob = await asyncio.to_thread(build_export)
    return Response(
        content=blob, media_type="application/gzip",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@router.post("/db-backup/send-telegram")
async def db_backup_send_telegram(_: None = Depends(_verify_owner)):
    """Отправить бэкап БД владельцу в Telegram прямо сейчас (offsite-копия)."""
    import asyncio
    from services.db_backup import build_export
    from channels.telegram import send_telegram_document
    token, chat = _owner_tg()
    if not (token and chat):
        raise HTTPException(status_code=409,
                            detail="Telegram владельца не настроен (TELEGRAM_CHAT_ID / BOT_TOKEN)")
    fname, blob = await asyncio.to_thread(build_export)
    ok = await send_telegram_document(token, str(chat), fname, blob,
                                      caption="💾 Бэкап базы DEADLINE (все переписки/статусы)")
    if not ok:
        raise HTTPException(status_code=502, detail="Не удалось отправить в Telegram")
    return {"ok": True, "filename": fname, "size_kb": len(blob) // 1024}


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


class ActionRescheduleRequest(BaseModel):
    due_at: str


@router.post("/scheduled-actions/{action_id}/reschedule")
async def scheduled_action_reschedule(
    action_id: str,
    req: ActionRescheduleRequest,
    _: None = Depends(_verify_member),
    db: Session = Depends(get_db),
):
    """Перенос задачи на новое время (drag-n-drop в календаре) — меняет due_at."""
    try:
        aid = UUID(action_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="action_id must be a UUID")
    row = db.get(ScheduledAction, aid)
    if row is None:
        raise HTTPException(status_code=404, detail="Action not found")
    if row.status not in ("pending", "processing"):
        raise HTTPException(status_code=409, detail=f"Уже в статусе {row.status}")
    new_dt = _parse_iso(req.due_at)
    row.due_at = new_dt
    db.commit()
    return {"ok": True, "due_at": new_dt.isoformat()}


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


class PromptPreviewRequest(BaseModel):
    question: str


@router.post("/prompt/preview")
async def prompt_preview(
    req: PromptPreviewRequest,
    _: None = Depends(_verify_owner),
):
    """Быстрый прогон активного системного промпта через LLM.
    Плейсхолдеры заполняются заглушками — без RAG-контекста и истории лида.
    Позволяет оператору проверить тон/логику текущего промпта."""
    import asyncio
    from services.prompt_store import get_active_system_prompt
    from prompts import SYSTEM_PROMPT

    tpl = get_active_system_prompt() or SYSTEM_PROMPT
    filled = tpl.format(
        context="(превью — без базы знаний)",
        history="",
        question=req.question,
        corrections="",
        handoff_block="",
    )
    try:
        import main as _main
        response = await asyncio.to_thread(_main.primary_llm.invoke, filled)
        reply = response.content.strip()
        return {"ok": True, "reply": reply}
    except Exception as exc:  # noqa: BLE001
        log.warning(f"prompt_preview LLM error: {exc}")
        return {"ok": False, "error": str(exc)}


_GOAL_LABELS = {
    "call": "📞 вести на созвон с менеджером",
    "collect_lead": "📥 собрать заявку (контакт + бриф задачи)",
    "consult": "💬 проконсультировать и помочь определиться",
    "sale": "💰 довести до оплаты/договорённости",
}


class SimulateLeadRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)


@router.post("/whatsapp/simulate-lead")
async def whatsapp_simulate_lead(
    req: SimulateLeadRequest,
    _: None = Depends(_verify_member),
):
    """Симуляция: «если НОВЫЙ лид сейчас напишет это в WhatsApp — что предложит
    бот и куда поведёт». Использует ТОТ ЖЕ генератор, что и предлагаемые ответы в
    карточке (services.wa_drafts: цель проекта → «от $X» → созвон), поэтому
    показывает ровно то, что вы одобряете в режиме наблюдения. Один LLM-вызов по
    запросу владельца — ничего не отправляет и не пишет в БД."""
    import main as _main
    from services import bot_settings as _bs
    from services import wa_drafts

    import asyncio as _aio
    goal = (_bs.get_all() or {}).get("bot_goal") or "call"
    dialog = f"Лид: {req.message.strip()[:500]}"
    kb = await _aio.to_thread(wa_drafts._kb_context, req.message)
    try:
        result = await _main.primary_llm.ainvoke(wa_drafts._prompt("клиент", "new_lead", dialog, kb, wa_drafts._active_offer()))
        reply = wa_drafts._clean_draft(getattr(result, "content", None) or "")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"LLM error: {exc}")
    if not reply:
        reply = "(бот вернул пустой/служебный ответ — попробуйте переформулировать сообщение лида)"
    return {
        "ok": True,
        "reply": reply,
        "goal": goal,
        "goal_label": _GOAL_LABELS.get(goal, goal),
    }


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


@router.delete("/kb/{source}")
async def kb_delete(
    source: str,
    _: None = Depends(_verify_owner),
    db: Session = Depends(get_db),
):
    """Удалить все чанки KB с данным source (только владелец — деструктивно)."""
    n = db.query(KBChunk).filter(KBChunk.source == source).delete()
    db.commit()
    return {"ok": True, "deleted": n}


@router.get("/kb/{source}/chunks")
async def kb_chunks_view(
    source: str,
    _: None = Depends(_verify_member),
    db: Session = Depends(get_db),
):
    """Показать ЧАНКИ документа KB — чтобы видеть, КАК система нарезала текст
    (и убедиться, что хорошо). По порядку chunk_index."""
    rows = (
        db.query(KBChunk).filter(KBChunk.source == source)
        .order_by(KBChunk.chunk_index).all()
    )
    return {"source": source, "chunks": [
        {"id": str(c.id), "index": c.chunk_index, "content": c.content,
         "chars": len(c.content or "")} for c in rows
    ]}


class KbChunkEditRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=20000)


@router.post("/kb/chunk/{chunk_id}")
async def kb_chunk_edit(
    chunk_id: str,
    req: KbChunkEditRequest,
    _: None = Depends(_verify_owner),
    db: Session = Depends(get_db),
):
    """Править ОДИН чанк KB + переэмбеддить (чтобы поиск учитывал правку). Эмбеддинг
    в потоке — не блокирует event loop."""
    import asyncio
    import main as _m
    try:
        cid = UUID(chunk_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="chunk_id must be a UUID")
    row = db.get(KBChunk, cid)
    if row is None:
        raise HTTPException(status_code=404, detail="Чанк не найден")
    text = (req.content or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Пустой чанк")
    try:
        vec = await asyncio.to_thread(lambda: _m.embeddings.embed_documents([text])[0])
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Embedding failed: {e}")
    row.content = text
    row.embedding = vec
    db.commit()
    return {"ok": True, "id": chunk_id, "chars": len(text)}


@router.delete("/kb/chunk/{chunk_id}")
async def kb_chunk_delete(
    chunk_id: str,
    _: None = Depends(_verify_owner),
    db: Session = Depends(get_db),
):
    """Удалить один чанк KB (плохо нарезанный/мусорный). Только владелец."""
    try:
        cid = UUID(chunk_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="chunk_id must be a UUID")
    n = db.query(KBChunk).filter(KBChunk.id == cid).delete()
    db.commit()
    return {"ok": True, "deleted": n}


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


@router.get("/automations/{rule_id}/runs")
async def automation_runs(
    rule_id: str,
    limit: int = 20,
    _: None = Depends(_verify_member),
    db: Session = Depends(get_db),
):
    """Последние срабатывания правила (для раскрывашки «История» в UI)."""
    uid = _uuid_or_422(rule_id)
    limit = max(1, min(limit, 100))
    rows = (
        db.query(AutomationRun)
        .filter(AutomationRun.rule_id == uid)
        .order_by(AutomationRun.fired_at.desc())
        .limit(limit)
        .all()
    )
    return {
        "items": [
            {
                "id": str(r.id),
                "conversation_id": str(r.conversation_id),
                "fired_at": r.fired_at.isoformat() if r.fired_at else None,
                "detail": r.detail,
            }
            for r in rows
        ]
    }


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
