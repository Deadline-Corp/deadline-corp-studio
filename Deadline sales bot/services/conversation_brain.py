"""Умное авто-ведение WhatsApp-диалога.

По каждой новой реплике — лида ИЛИ ручного ответа оператора с телефона — бот
сам, без участия человека:
  1. двигает стадию воронки ВПЕРЁД (никогда назад),
  2. заносит созвон в календарь, когда время реально согласовано (в т.ч. из
     фразы оператора «договорились на среду в 15»),
  3. сигналит владельцу в Telegram, когда лид явно просит человека/созвон.

Один LLM-вызов (Gemini) возвращает структурное решение по переписке. Всё
best-effort: любая ошибка логируется, диалог не ломается. Бронь/напоминания
переиспользуют те же примитивы, что ручная бронь из карточки
(services.scheduled_actions + services.scheduling) — единый формат.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy.orm import Session

from db.models import Conversation, Customer, Message, StageTransition
from services import funnel_store, scheduling as _sched
from services.scheduled_actions import (
    cancel_call_actions, write_call_booking, write_call_reminder,
)

log = logging.getLogger(__name__)

# Прямой порядок встроенных стадий — двигаем только вперёд.
_FORWARD = ["new_lead", "in_dialog", "qualified", "on_call",
            "proposal", "prepayment", "completed_won"]


def _transcript(db: Session, conv: Conversation, limit: int = 14) -> str:
    rows = (
        db.query(Message)
        .filter(Message.conversation_id == conv.id)
        .order_by(Message.created_at.desc())
        .limit(limit).all()
    )
    out = []
    for m in reversed(rows):
        if m.role == "user":
            who = "Лид"
        elif m.role in ("assistant", "operator"):
            who = "Мы"
        else:
            continue
        out.append(f"{who}: {(m.content or '')[:300]}")
    return "\n".join(out)


def _dialog_count(db: Session, conv: Conversation) -> int:
    """Число реплик лида/нас/оператора — мера новизны для периодического sweep."""
    return (
        db.query(Message)
        .filter(Message.conversation_id == conv.id)
        .filter(Message.role.in_(("user", "assistant", "operator")))
        .count()
    )


def _parse_json(raw: str) -> Optional[dict]:
    if not raw:
        return None
    s = raw.strip()
    # срезаем ```json ... ``` если модель обернула
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s).strip()
    m = re.search(r"\{.*\}", s, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except (ValueError, TypeError):
        return None


def _stage_forward(current: str, suggested: str) -> Optional[str]:
    """Вернуть новую стадию, только если suggested СТРОГО впереди current."""
    if suggested not in _FORWARD or current not in _FORWARD:
        return None
    if _FORWARD.index(suggested) > _FORWARD.index(current):
        return suggested
    return None


async def _signal_owner(settings: Any, text: str) -> None:
    from services import bot_settings as _bs
    from channels.telegram import send_telegram_reply
    chat = (_bs.get("manager_chat_id") or "").strip() or (settings.telegram_chat_id or "")
    token = settings.telegram_bot_token
    if not (chat and token):
        return
    try:
        await send_telegram_reply(token, str(chat), text[:3500])
    except Exception as e:  # noqa: BLE001
        log.warning(f"brain owner-signal failed: {e}")


async def _book(db: Session, conv: Conversation, cust: Customer,
                settings: Any, new_dt: datetime, medium: Optional[str]) -> None:
    """Бронь созвона = ровно то же, что ручной перенос из карточки."""
    import asyncio
    prof = dict(cust.profile_data or {})
    await asyncio.to_thread(cancel_call_actions, str(conv.id))
    prof["booked_call_at"] = new_dt.isoformat()
    if medium:
        prof["call_medium"] = medium
    prof.pop("brain_signaled", None)  # бронь снимает «просил человека»
    cust.profile_data = prof
    _from = conv.lead_stage
    conv.lead_stage = "on_call"
    if _from != "on_call":
        db.add(StageTransition(
            conversation_id=conv.id, customer_id=conv.customer_id,
            from_stage=_from, to_stage="on_call", by="bot-brain",
        ))
    db.commit()

    now = datetime.now(timezone.utc)
    chat = conv.channel_conversation_id
    lead_name = cust.name or cust.email or "лид"
    contact = cust.email or ""
    lang = prof.get("lang") or "ru"
    await asyncio.to_thread(
        write_call_booking,
        customer_id=str(cust.id), conversation_id=str(conv.id),
        channel=conv.channel, chat_id=str(chat) if chat else None,
        call_at=new_dt, medium=medium,
    )
    for fire, label in _sched.reminder_schedule(new_dt, now):
        if chat:
            await asyncio.to_thread(
                write_call_reminder,
                customer_id=str(cust.id), conversation_id=str(conv.id),
                channel=conv.channel, chat_id=str(chat), due_at=fire,
                text=_sched.lead_reminder_text(new_dt, label, medium, lang=lang,
                                               phone=str(chat)),
                audience="lead",
            )
        if settings.telegram_operator_group_id:
            await asyncio.to_thread(
                write_call_reminder,
                customer_id=str(cust.id), conversation_id=str(conv.id),
                channel=conv.channel,
                chat_id=str(settings.telegram_operator_group_id), due_at=fire,
                text=_sched.admin_reminder_text(new_dt, lead_name, label, medium, contact),
                audience="admin",
            )
    # СРАЗУ уведомить владельца/менеджера: «поймал договорённость и поставил в
    # календарь» (просьба владельца — видеть, что бот распознал ручную договорённость).
    try:
        when_lead = _sched.format_slot_human(new_dt, tz=_sched.lead_tz_from_phone(str(chat or "")))
        tzlbl = _sched.tz_label_from_phone(str(chat or ""))
        await _signal_owner(
            settings,
            f"📅 Поставил созвон в календарь из переписки:\n"
            f"Лид: {lead_name} ({chat})\n"
            f"Когда: {when_lead} ({tzlbl}){(' · ' + medium) if medium else ''}\n"
            f"Стадия → 📞 Созвон назначен. Если время не то — поправьте в карточке.",
        )
    except Exception as e:  # noqa: BLE001
        log.warning(f"[{str(conv.id)[:8]}] book notify failed: {e}")


async def analyze_and_advance(db: Session, conv: Conversation, cust: Customer,
                              llm: Any, settings: Any, refresh_draft: bool = True) -> dict:
    """Проанализировать диалог и применить решения. Возвращает что сделано.
    refresh_draft=False — НЕ перегенерировать черновик (его LLM+KB-эмбед тяжёлые;
    в bulk-cron-sweep отключаем, чтобы не исчерпать пул; черновик освежается на
    вебхуке per-message)."""
    done: dict = {"stage": None, "booked": None, "signaled": False}
    if (conv.lead_stage or "new_lead") in ("lost", "completed_won"):
        return done
    transcript = _transcript(db, conv)
    if not transcript.strip():
        return done

    tz = _sched.lead_tz_from_phone(conv.channel_conversation_id or "")
    tz_label = _sched.tz_label_from_phone(conv.channel_conversation_id or "")
    now_utc = datetime.now(timezone.utc)
    now_lead = now_utc.astimezone(tz)
    prompt = (
        "Проанализируй переписку веб-студии с лидом и верни СТРОГО JSON (без пояснений). "
        "Поля:\n"
        '  "stage": одна из new_lead|in_dialog|qualified|on_call|proposal|prepayment|completed_won '
        "— текущая стадия по смыслу (in_dialog=идёт разговор, но проект ещё не описан; "
        "qualified=ТОЛЬКО если лид описал КОНКРЕТНУЮ задачу/проект — что именно нужно сделать "
        "(сайт/магазин/бот/Mini App/AI и зачем); просто «привет/расскажите подробнее» — это "
        "in_dialog, НЕ qualified; on_call=договорились о созвоне; proposal=обсуждается КП/цена; "
        "prepayment=готов платить);\n"
        '  "call_agreed": true если СТОРОНЫ ДОГОВОРИЛИСЬ о созвоне на конкретный ДЕНЬ '
        "(точный час не обязателен — «в среду утром», «завтра днём», «в пятницу» тоже "
        "считаются договорённостью; в т.ч. если ЭТО НАШ менеджер написал «договорились/"
        "поставил на среду утром»);\n"
        '  "call_datetime_utc": ISO8601 в UTC. Если час не назван — бери разумный по части '
        "суток (утро→10:00, день→14:00, вечер→18:00 ПО ВРЕМЕНИ ЛИДА), иначе null. "
        f"Сейчас {now_utc.isoformat()} (UTC), у лида {now_lead.strftime('%Y-%m-%d %H:%M')} ({tz_label}). "
        "Считай дни недели/«завтра»/«в среду утром» от времени ЛИДА, затем переведи в UTC;\n"
        '  "call_medium": "WhatsApp"|"Телефон"|"Zoom"|"Google Meet"|null;\n'
        '  "wants_human": true если лид ЯВНО просит позвонить/связаться с человеком/менеджером;\n'
        '  "reason": кратко почему (≤120 симв).\n\n'
        f"Переписка:\n{transcript}"
    )
    try:
        result = await llm.ainvoke(prompt)
        data = _parse_json(getattr(result, "content", None) or "")
    except Exception as e:  # noqa: BLE001
        log.warning(f"[{str(conv.id)[:8]}] brain LLM failed: {e}")
        return done
    if not data:
        return done

    # 1) стадия вперёд
    new_stage = _stage_forward(conv.lead_stage or "new_lead", str(data.get("stage") or ""))
    if new_stage and new_stage != "on_call":  # on_call ставит бронь ниже
        from_stage = conv.lead_stage
        conv.lead_stage = new_stage
        db.add(StageTransition(
            conversation_id=conv.id, customer_id=conv.customer_id,
            from_stage=from_stage, to_stage=new_stage, by="bot-brain",
        ))
        db.commit()
        done["stage"] = new_stage
        if settings.crm_enabled and new_stage in funnel_store.BUILTIN_KEYS:
            try:
                from services.crm_dispatch import dispatch_stage_change
                dispatch_stage_change(
                    customer_id=str(conv.customer_id), crm_deal_id=conv.crm_deal_id,
                    new_stage=new_stage, lost_reason=None, conversation_id=str(conv.id),
                )
            except Exception as e:  # noqa: BLE001
                log.warning(f"[{str(conv.id)[:8]}] brain CRM mirror failed: {e}")

    # 2) бронь созвона
    if data.get("call_agreed") and data.get("call_datetime_utc"):
        try:
            raw = str(data["call_datetime_utc"]).replace("Z", "+00:00")
            new_dt = datetime.fromisoformat(raw)
            if new_dt.tzinfo is None:
                new_dt = new_dt.replace(tzinfo=timezone.utc)
            new_dt = new_dt.astimezone(timezone.utc)
            prof = cust.profile_data or {}
            existing = prof.get("booked_call_at")
            # не дублируем, если уже забронировано ~то же время (±10 мин)
            dup = False
            if existing:
                try:
                    ex = datetime.fromisoformat(str(existing).replace("Z", "+00:00"))
                    if ex.tzinfo is None:
                        ex = ex.replace(tzinfo=timezone.utc)
                    dup = abs((ex - new_dt).total_seconds()) < 600
                except (ValueError, TypeError):
                    dup = False
            if new_dt > now_utc and not dup:
                await _book(db, conv, cust, settings, new_dt, data.get("call_medium"))
                done["booked"] = new_dt.isoformat()
        except (ValueError, TypeError) as e:
            log.warning(f"[{str(conv.id)[:8]}] brain bad call_datetime: {e}")

    # 3) сигнал владельцу (один раз на эпизод «просит человека», пока нет брони)
    if data.get("wants_human") and not done["booked"]:
        prof = dict(cust.profile_data or {})
        if not prof.get("brain_signaled"):
            name = cust.name or conv.channel_conversation_id or "лид"
            await _signal_owner(
                settings,
                f"🔔 Лид {name} просит связаться/созвон.\n"
                f"Причина: {str(data.get('reason') or '')[:200]}\n"
                f"Открыть карточку в панели → одобрить ответ / поставить время.",
            )
            prof["brain_signaled"] = now_utc.isoformat()
            cust.profile_data = prof
            db.commit()
            done["signaled"] = True

    # Держим предложенный ответ свежим — генерим В ФОНЕ (не в GET-пути карточки!),
    # если черновика нет или он устарел. Так панель всегда показывает актуальный
    # ответ, а conversation_detail остаётся без LLM (не вешает пул).
    try:
        if refresh_draft and not bool(getattr(conv, "wa_autonomous", False)):
            from services import wa_drafts
            if not conv.pending_wa_draft or wa_drafts.is_stale(db, conv):
                payload = await wa_drafts.generate_for_conv(
                    db, conv, cust, llm, source="brain_refresh",
                )
                if payload:
                    db.commit()
                    done["draft"] = True
    except Exception as e:  # noqa: BLE001
        db.rollback()
        log.warning(f"[{str(conv.id)[:8]}] brain draft refresh failed: {e}")

    # Запомнить «проанализировано до этого числа реплик» — периодический sweep
    # пропускает диалоги без новых сообщений (не жжёт LLM зря).
    try:
        prof = dict(cust.profile_data or {})
        prof["brain_last_count"] = _dialog_count(db, conv)
        cust.profile_data = prof
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
    return done


async def sweep_recent(llm: Any, settings: Any, since_minutes: int = 360,
                       limit: int = 10, force: bool = False) -> dict:
    """Периодическая проверка актуальности: пройтись по недавно активным
    WhatsApp-диалогам и до-применить решения, если появились новые реплики
    (в т.ч. РУЧНОЙ ответ оператора с телефона, который мог не прийти вебхуком).

    ВАЖНО для прода: НЕ держим одно DB-соединение на весь проход. Сначала
    КОРОТКОЙ сессией собираем id диалогов, которым нужен анализ (дешёвый
    count-чек), сессию закрываем. Затем каждый диалог обрабатываем В СВОЕЙ
    короткой сессии — соединение возвращается в пул МЕЖДУ LLM-вызовами, а не
    держится все минуты разом (иначе пул исчерпывается → вис, инцидент 06-02).
    Анализирует только диалоги с НОВЫМИ сообщениями с прошлого разбора."""
    from datetime import timedelta
    from db.connection import session_scope
    out = {"examined": 0, "analyzed": 0, "stage": 0, "booked": 0, "signaled": 0}
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=since_minutes)

    # Фаза 1 — короткая сессия: собрать кандидатов (без LLM, соединение освобождаем).
    candidates: list[str] = []
    with session_scope() as db:
        rows = (
            db.query(Conversation, Customer)
            .join(Customer, Conversation.customer_id == Customer.id)
            .filter(Conversation.channel == "whatsapp")
            .filter(Conversation.last_message_at >= cutoff)
            .order_by(Conversation.last_message_at.desc())
            .limit(limit).all()
        )
        for conv, cust in rows:
            out["examined"] += 1
            if (conv.lead_stage or "new_lead") in ("lost", "completed_won"):
                continue
            if not force:
                last_seen = (cust.profile_data or {}).get("brain_last_count")
                cur = _dialog_count(db, conv)
                if last_seen is not None and cur <= int(last_seen):
                    continue  # новых сообщений нет — пропускаем (без LLM)
            candidates.append(str(conv.id))

    # Фаза 2 — по кандидату СВОЯ короткая сессия (LLM не держит общий коннект).
    for cid in candidates:
        try:
            with session_scope() as db:
                row = (
                    db.query(Conversation, Customer)
                    .join(Customer, Conversation.customer_id == Customer.id)
                    .filter(Conversation.id == cid).first()
                )
                if not row:
                    continue
                conv, cust = row
                # В bulk-проходе НЕ перегенерируем черновик (LLM+KB-эмбед тяжёлые —
                # исчерпывали пул, инцидент 06-15). Стадия/бронь/сигнал — да.
                res = await analyze_and_advance(db, conv, cust, llm, settings, refresh_draft=False)
            out["analyzed"] += 1
            import asyncio as _a
            await _a.sleep(0.4)  # уступаем loop между LLM-вызовами (health не виснет)
            if res.get("stage"):
                out["stage"] += 1
            if res.get("booked"):
                out["booked"] += 1
            if res.get("signaled"):
                out["signaled"] += 1
        except Exception as e:  # noqa: BLE001
            log.warning(f"[{cid[:8]}] sweep analyze failed: {e}")
    return out
