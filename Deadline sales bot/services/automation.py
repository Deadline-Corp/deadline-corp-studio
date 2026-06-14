"""Automation engine — исполнение правил «Когда → Если → То» (Admin UI).

Вызывается из крон-свипа (services/cron.py) раз в проход. V1 триггер —
time-based «лид молчит N часов» (`lead_silent`): он покрывает прогрев,
дожим и контроль зависших сделок, и при этом НЕ трогает hot-path обработки
сообщений (нулевой риск для живого бота). Событийные триггеры (new_lead,
stage_changed) — следующая итерация, хуки в main.

Дедуп: automation_runs. cooldown_hours=0 → одно срабатывание на диалог
навсегда; N>0 → можно повторно через N часов, но максимум MAX_FIRES раз
(анти-спам: правило не должно долбить лида бесконечно).

Действия:
  bot_message  — бот пишет лиду СЕЙЧАС (через scheduled_action due=now;
                 исполняет run_due_followups → пока только Telegram-лиды,
                 для прочих каналов действие пропускается с пометкой)
  create_task  — задача человеку в «Мой день» (operator_callback)
  set_stage    — перевод по воронке (+ stage_transitions, + CRM-зеркало
                 для встроенных ключей)
  notify_admin — сообщение админу в Telegram (settings.telegram_chat_id)
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

MAX_FIRES = 5          # потолок повторов одного правила на один диалог
MAX_FIRES_PER_SWEEP = 30  # потолок срабатываний за один проход (анти-взрыв)

TRIGGER_TYPES = ("lead_silent", "new_lead", "sequence", "stage_changed")
NEW_LEAD_WINDOW_H = 48  # new_lead срабатывает только для диалогов моложе 48ч (анти-ретро)
ACTION_TYPES = ("bot_message", "create_task", "set_stage", "notify_admin")
MAX_SEQUENCE_STEPS = 5


def validate_rule(trigger: dict, conditions: Optional[dict], actions: list) -> list[str]:
    """Список проблем (пусто = ок). Зовётся из admin_api ДО сохранения."""
    problems: list[str] = []
    t = (trigger or {}).get("type")
    if t not in TRIGGER_TYPES:
        problems.append(f"Неизвестный триггер {t!r} (доступно: {TRIGGER_TYPES})")
    if t == "lead_silent":
        try:
            h = float(trigger.get("hours", 0))
            if not (0.25 <= h <= 24 * 90):
                problems.append("hours: от 0.25 до 2160 (90 дней)")
        except (TypeError, ValueError):
            problems.append("hours: число часов")
    # new_lead: без параметров — срабатывает на следующем проходе крона
    # (≤10 мин) после появления диалога, один раз.
    if t == "sequence":
        steps = trigger.get("steps") or []
        if not (1 <= len(steps) <= MAX_SEQUENCE_STEPS):
            problems.append(f"Цепочка: от 1 до {MAX_SEQUENCE_STEPS} касаний")
        prev_h = 0.0
        for i, st in enumerate(steps):
            try:
                h = float(st.get("hours", 0))
            except (TypeError, ValueError):
                h = -1
            if h <= prev_h:
                problems.append(f"Касание #{i + 1}: часы должны расти (касание позже предыдущего)")
            prev_h = max(prev_h, h)
            if not (st.get("text") or "").strip():
                problems.append(f"Касание #{i + 1}: пустой текст")
        return problems  # actions у цепочки не используются
    if t == "stage_changed":
        # to_stage опционален: задан → правило срабатывает только при переходе
        # именно на эту стадию; пусто → на любую смену стадии.
        ws = trigger.get("to_stage")
        if ws is not None and not isinstance(ws, str):
            problems.append("to_stage: ключ стадии (строка) или пусто = на любую смену")
    if not actions:
        problems.append("Нужно хотя бы одно действие")
    for i, a in enumerate(actions or []):
        at = (a or {}).get("type")
        if at not in ACTION_TYPES:
            problems.append(f"Действие #{i + 1}: неизвестный тип {at!r}")
            continue
        if at in ("bot_message", "create_task", "notify_admin") and not (a.get("text") or "").strip():
            problems.append(f"Действие #{i + 1} ({at}): пустой текст")
        if at == "set_stage":
            if not a.get("stage"):
                problems.append(f"Действие #{i + 1}: не указана стадия")
            from services.funnel import LOST_REASONS
            if a.get("stage") == "lost" and a.get("lost_reason") not in LOST_REASONS:
                problems.append(f"Действие #{i + 1}: для lost нужна причина из {sorted(LOST_REASONS)}")
    return problems


def _passes_conditions(cond: Optional[dict], conv, cust) -> bool:
    if not cond:
        return True
    ch = (cond.get("channels") or [])
    if ch and (conv.channel or "").lower() not in [c.lower() for c in ch]:
        return False
    st = (cond.get("stages") or [])
    if st and conv.lead_stage not in st:
        return False
    tp = (cond.get("temperatures") or [])
    if tp and (cust.lead_temperature or "cold") not in tp:
        return False
    try:
        if int(cust.lead_score or 0) < int(cond.get("min_score") or 0):
            return False
    except (TypeError, ValueError):
        pass
    return True


async def run_automations() -> dict:
    """Один проход движка. Изолированно: ошибка одного правила/диалога не
    валит остальные. Возвращает stats для лога крона."""
    from db.connection import session_scope
    from db.models import AutomationRule, AutomationRun, Conversation, Customer

    stats: dict[str, Any] = {"rules": 0, "fired": 0, "skipped": 0, "errors": 0}
    now = datetime.now(timezone.utc)

    with session_scope() as s:
        rules = (
            s.query(AutomationRule)
            .filter(AutomationRule.enabled == True)  # noqa: E712
            .order_by(AutomationRule.position.asc())
            .all()
        )
        stats["rules"] = len(rules)
        if not rules:
            return stats

        fired_total = 0
        for rule in rules:
            if fired_total >= MAX_FIRES_PER_SWEEP:
                logger.warning("[automation] sweep cap reached (%d) — rest deferred", MAX_FIRES_PER_SWEEP)
                break
            try:
                trig = rule.trigger or {}
                ttype = trig.get("type")
                if ttype == "sequence":
                    fired_total += await _run_sequence_rule(s, rule, now, stats,
                                                            MAX_FIRES_PER_SWEEP - fired_total)
                    continue
                if ttype == "lead_silent":
                    hours = float(trig.get("hours", 24))
                    cutoff = now - timedelta(hours=hours)
                    # Кандидаты: открытые диалоги, молчат дольше порога.
                    candidates = (
                        s.query(Conversation, Customer)
                        .join(Customer, Conversation.customer_id == Customer.id)
                        .filter(Conversation.status == "open")
                        .filter(Conversation.last_message_at.isnot(None))
                        .filter(Conversation.last_message_at <= cutoff)
                        .limit(200)
                        .all()
                    )
                elif ttype == "new_lead":
                    # Свежесозданные диалоги (окно 48ч — чтобы включённое сегодня
                    # правило не ретро-стреляло по всей старой базе). Дедуп через
                    # automation_runs гарантирует «один раз на диалог».
                    candidates = (
                        s.query(Conversation, Customer)
                        .join(Customer, Conversation.customer_id == Customer.id)
                        .filter(Conversation.status == "open")
                        .filter(Conversation.created_at >= now - timedelta(hours=NEW_LEAD_WINDOW_H))
                        .limit(200)
                        .all()
                    )
                else:
                    continue
                for conv, cust in candidates:
                    if fired_total >= MAX_FIRES_PER_SWEEP:
                        break
                    if not _passes_conditions(rule.conditions, conv, cust):
                        continue
                    # Дедуп / cooldown.
                    runs = (
                        s.query(AutomationRun)
                        .filter(AutomationRun.rule_id == rule.id,
                                AutomationRun.conversation_id == conv.id)
                        .order_by(AutomationRun.fired_at.desc())
                        .all()
                    )
                    if runs:
                        if rule.cooldown_hours <= 0 or len(runs) >= MAX_FIRES:
                            continue
                        last = runs[0].fired_at
                        if last and last.tzinfo is None:
                            last = last.replace(tzinfo=timezone.utc)
                        if last and (now - last) < timedelta(hours=rule.cooldown_hours):
                            continue
                        # Повтор только если лид был активен ПОСЛЕ прошлого
                        # срабатывания не нужен: правило «молчит» — повтор
                        # допустим по cooldown (анти-спам через MAX_FIRES).

                    detail = await _execute_actions(s, rule, conv, cust)
                    s.add(AutomationRun(rule_id=rule.id, conversation_id=conv.id, detail=detail))
                    s.flush()
                    fired_total += 1
                    stats["fired"] += 1
                    logger.info("[automation] rule=%r fired conv=%s detail=%s",
                                rule.name, str(conv.id)[:8], detail)
            except Exception as e:  # noqa: BLE001
                stats["errors"] += 1
                logger.warning("[automation] rule=%r error: %s", getattr(rule, "name", "?"), e)

    return stats


async def run_automations_for_stage_change(conversation_id: str, new_stage: str) -> dict:
    """№13 — мгновенный триггер «стадия изменилась».

    Прогоняет ТОЛЬКО правила с триггером 'stage_changed' для одного диалога,
    не дожидаясь крон-прохода. Вызывается в фоне (asyncio.create_task) из
    main._handle_message ПОСЛЕ commit, со своей DB-сессией.

    Безопасность для hot-path: вся функция в try/except, ошибка лишь логируется
    (ответ лиду уже отправлен). До создания правила с триггером 'stage_changed'
    — полный no-op. trigger.to_stage (если задан) фильтрует по целевой стадии;
    пусто = на любую смену. Дедуп/cooldown — как в run_automations."""
    from db.connection import session_scope
    from db.models import AutomationRule, AutomationRun, Conversation, Customer

    stats: dict[str, Any] = {"fired": 0, "skipped": 0, "errors": 0}
    now = datetime.now(timezone.utc)
    try:
        with session_scope() as s:
            row = (
                s.query(Conversation, Customer)
                .join(Customer, Conversation.customer_id == Customer.id)
                .filter(Conversation.id == conversation_id)
                .first()
            )
            if not row:
                return stats
            conv, cust = row

            rules = (
                s.query(AutomationRule)
                .filter(AutomationRule.enabled == True)  # noqa: E712
                .order_by(AutomationRule.position.asc())
                .all()
            )
            for rule in rules:
                trig = rule.trigger or {}
                if trig.get("type") != "stage_changed":
                    continue
                want_stage = trig.get("to_stage")
                if want_stage and want_stage != new_stage:
                    continue
                if not _passes_conditions(rule.conditions, conv, cust):
                    continue
                # Дедуп / cooldown (идентично run_automations).
                runs = (
                    s.query(AutomationRun)
                    .filter(AutomationRun.rule_id == rule.id,
                            AutomationRun.conversation_id == conv.id)
                    .order_by(AutomationRun.fired_at.desc())
                    .all()
                )
                if runs:
                    if rule.cooldown_hours <= 0 or len(runs) >= MAX_FIRES:
                        continue
                    last = runs[0].fired_at
                    if last and last.tzinfo is None:
                        last = last.replace(tzinfo=timezone.utc)
                    if last and (now - last) < timedelta(hours=rule.cooldown_hours):
                        continue
                try:
                    detail = await _execute_actions(s, rule, conv, cust)
                    s.add(AutomationRun(rule_id=rule.id, conversation_id=conv.id, detail=detail))
                    s.flush()
                    stats["fired"] += 1
                    logger.info("[automation] stage_changed rule=%r fired conv=%s → %s detail=%s",
                                rule.name, str(conv.id)[:8], new_stage, detail)
                except Exception as e:  # noqa: BLE001
                    stats["errors"] += 1
                    logger.warning("[automation] stage_changed rule=%r error: %s",
                                   getattr(rule, "name", "?"), e)
    except Exception as e:  # noqa: BLE001
        logger.warning("[automation] run_for_stage_change(%s) error: %s",
                       str(conversation_id)[:8], e)
    return stats


async def _run_sequence_rule(s, rule, now, stats, budget: int) -> int:
    """Цепочка касаний (день 1→3→7). Семантика:
    - шаг 0: прошло steps[0].hours тишины ЛИДА (от его последнего сообщения;
      если лид ещё не писал — от создания диалога);
    - шаг k: прошло (steps[k].hours − steps[k-1].hours) от касания k−1
      И лид НЕ ответил после касания k−1 (ответил → цепочка завершена);
    - касание: Telegram-лиду пишет бот; прочим каналам — задача человеку
      с готовым текстом (website-лида ботом не достать).
    Дедуп: номер шага = количество прошлых runs этого правила на диалог."""
    from db.models import AutomationRun, Conversation, Customer, Message
    from sqlalchemy import func as _f

    steps = (rule.trigger or {}).get("steps") or []
    if not steps or budget <= 0:
        return 0

    fired = 0
    candidates = (
        s.query(Conversation, Customer)
        .join(Customer, Conversation.customer_id == Customer.id)
        .filter(Conversation.status == "open")
        .filter(Conversation.last_message_at.isnot(None))
        .limit(200)
        .all()
    )
    for conv, cust in candidates:
        if fired >= budget:
            break
        if not _passes_conditions(rule.conditions, conv, cust):
            continue
        runs = (
            s.query(AutomationRun)
            .filter(AutomationRun.rule_id == rule.id,
                    AutomationRun.conversation_id == conv.id)
            .order_by(AutomationRun.fired_at.asc())
            .all()
        )
        k = len(runs)
        if k >= len(steps):
            continue

        last_user = (
            s.query(_f.max(Message.created_at))
            .filter(Message.conversation_id == conv.id, Message.role == "user")
            .scalar()
        )
        if last_user is not None and last_user.tzinfo is None:
            last_user = last_user.replace(tzinfo=timezone.utc)

        if k == 0:
            base = last_user or conv.created_at
            if base is not None and base.tzinfo is None:
                base = base.replace(tzinfo=timezone.utc)
            if base is None or (now - base) < timedelta(hours=float(steps[0].get("hours", 24))):
                continue
        else:
            prev = runs[-1].fired_at
            if prev is not None and prev.tzinfo is None:
                prev = prev.replace(tzinfo=timezone.utc)
            if last_user is not None and prev is not None and last_user > prev:
                continue  # лид отозвался после касания — цепочка сделала своё
            delta_h = max(1.0, float(steps[k].get("hours", 0)) - float(steps[k - 1].get("hours", 0)))
            if prev is None or (now - prev) < timedelta(hours=delta_h):
                continue

        text = (steps[k].get("text") or "").strip()
        detail: dict[str, Any] = {"step": k + 1}
        try:
            if (conv.channel or "").lower() == "telegram" and conv.channel_conversation_id:
                from services.scheduled_actions import write_scheduled_action
                action_id, _ = write_scheduled_action(
                    customer_id=str(conv.customer_id),
                    conversation_id=str(conv.id),
                    channel=conv.channel,
                    chat_id=conv.channel_conversation_id,
                    due_at=datetime.now(timezone.utc),
                    text=text,
                )
                detail["bot_message"] = f"queued {action_id}"
            else:
                from db.models import ScheduledAction
                row = ScheduledAction(
                    customer_id=conv.customer_id,
                    conversation_id=conv.id,
                    channel=conv.channel,
                    chat_id=conv.channel_conversation_id,
                    action_type="operator_callback",
                    executor="human",
                    due_at=datetime.now(timezone.utc),
                    status="pending",
                    payload={"text": f"Касание {k + 1} цепочки «{rule.name}» — написать лиду: {text}",
                             "by": f"sequence:{rule.name}"},
                )
                s.add(row)
                s.flush()
                detail["create_task"] = str(row.id)
        except Exception as e:  # noqa: BLE001
            detail["error"] = str(e)
            logger.warning("[automation] sequence step failed: %s", e)

        s.add(AutomationRun(rule_id=rule.id, conversation_id=conv.id, detail=detail))
        s.flush()
        fired += 1
        stats["fired"] += 1
        logger.info("[automation] sequence %r step %d → conv=%s", rule.name, k + 1, str(conv.id)[:8])
    return fired


async def _execute_actions(s, rule, conv, cust) -> dict:
    """Выполнить действия правила для диалога. Возвращает detail-лог."""
    detail: dict[str, Any] = {}
    for a in (rule.actions or []):
        at = a.get("type")
        try:
            if at == "bot_message":
                if (conv.channel or "").lower() != "telegram":
                    detail[at] = f"skipped: канал {conv.channel} (бот-автоотправка пока Telegram)"
                    continue
                from services.scheduled_actions import write_scheduled_action
                action_id, _ = write_scheduled_action(
                    customer_id=str(conv.customer_id),
                    conversation_id=str(conv.id),
                    channel=conv.channel,
                    chat_id=conv.channel_conversation_id,
                    due_at=datetime.now(timezone.utc),
                    text=a.get("text", ""),
                )
                detail[at] = f"queued {action_id}"
            elif at == "create_task":
                from db.models import ScheduledAction
                due = datetime.now(timezone.utc) + timedelta(hours=float(a.get("due_in_hours") or 0))
                row = ScheduledAction(
                    customer_id=conv.customer_id,
                    conversation_id=conv.id,
                    channel=conv.channel,
                    chat_id=conv.channel_conversation_id,
                    action_type="operator_callback",
                    executor="human",
                    due_at=due,
                    status="pending",
                    payload={"text": a.get("text", ""), "by": f"automation:{rule.name}"},
                )
                s.add(row)
                s.flush()
                detail[at] = str(row.id)
            elif at == "set_stage":
                from db.models import StageTransition
                from services import funnel_store
                to_stage = a.get("stage")
                from_stage = conv.lead_stage
                if to_stage == from_stage:
                    detail[at] = "skipped: уже на стадии"
                    continue
                conv.lead_stage = to_stage
                conv.lost_reason = a.get("lost_reason") if to_stage == "lost" else None
                s.add(StageTransition(
                    conversation_id=conv.id, customer_id=conv.customer_id,
                    from_stage=from_stage, to_stage=to_stage, by="automation",
                ))
                s.flush()
                # CRM-зеркало (встроенные ключи), не блокирует.
                try:
                    import main as _main
                    if _main.settings.crm_enabled and to_stage in funnel_store.BUILTIN_KEYS:
                        from services.crm_dispatch import dispatch_stage_change
                        dispatch_stage_change(
                            customer_id=str(conv.customer_id),
                            crm_deal_id=conv.crm_deal_id,
                            new_stage=to_stage,
                            lost_reason=conv.lost_reason,
                            conversation_id=str(conv.id),
                        )
                except Exception as ce:  # noqa: BLE001
                    logger.warning("[automation] crm mirror failed: %s", ce)
                detail[at] = f"{from_stage}→{to_stage}"
            elif at == "notify_admin":
                import os
                import httpx
                token = os.getenv("TELEGRAM_BOT_TOKEN")
                chat_id = os.getenv("TELEGRAM_CHAT_ID")
                if not (token and chat_id):
                    detail[at] = "skipped: telegram admin chat не настроен"
                    continue
                name = cust.name or cust.email or str(cust.id)[:8]
                text = (f"⚡ Автоматизация «{rule.name}»\n"
                        f"Лид: {name} · {conv.channel} · стадия {conv.lead_stage}\n"
                        f"{a.get('text', '')}")
                async with httpx.AsyncClient(timeout=10) as client:
                    r = await client.post(
                        f"https://api.telegram.org/bot{token}/sendMessage",
                        json={"chat_id": chat_id, "text": text},
                    )
                detail[at] = f"sent={r.status_code == 200}"
        except Exception as e:  # noqa: BLE001
            detail[at] = f"error: {e}"
            logger.warning("[automation] action %s failed: %s", at, e)
    return detail
