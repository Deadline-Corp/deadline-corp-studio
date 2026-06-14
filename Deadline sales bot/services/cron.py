"""Periodic cron worker (Phase 9d, 2026-05-27).

A single async background task that wakes every CRON_INTERVAL_SEC and
sweeps silent customers/conversations:

  1. Apply Notion §7 temperature decay on customer.lead_temperature
     (14 days silence → step down; 21+ days → frozen).
  2. Apply Notion §5 score decay on customer.lead_score (-1 / 48h).
  3. Notion §13 pause-strategy + §14 warming — produce an operator task
     in CRM (via dispatch_operator_task) when a lead needs re-engagement.
  4. Notion §20 funnel silence rule — conversations stuck in 'in_dialog'
     past `silence_lost_threshold_d` get auto-transitioned to lost(delayed).

All side-effects are gated by Settings.crm_enabled. Like every other CRM
piece, this is best-effort — failures get logged and swallowed; the bot's
own Postgres remains the source of truth and the next cycle retries.

Started from main.startup() when CRM is enabled, drained on shutdown.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from services.crm_dispatch import (
    dispatch_operator_task,
    dispatch_stage_change,
    dispatch_temperature_change,
)
from services.funnel import (
    can_auto_transition,
    decide_on_silence,
    decide_post_sale_window,
)
from services.pause_strategy import classify_pause
from services.scoring import apply_decay as score_decay
from services.temperature import apply_decay as temperature_decay
from services.warming import plan_warming


logger = logging.getLogger(__name__)


# How often the worker wakes. 10 минут: прогрев/декей идемпотентны (повторный
# прогон безопасен, warming-cadence сам не пере-шлёт), но КРИТИЧНО для своевременной
# отправки follow-up и напоминаний о созвоне (за сутки/3ч/1ч) — на часовом интервале
# «за 1 час» могло прийти за 0-60 мин. 10 мин = напоминания точны в пределах 10 минут.
DEFAULT_CRON_INTERVAL_SEC: int = 10 * 60   # 10 минут

# How many customers to process per cycle. Bounded so a single bad cycle
# never lasts longer than ~5 minutes even with HubSpot at 100ms/request.
MAX_CUSTOMERS_PER_CYCLE: int = 200


_worker_task: Optional[asyncio.Task] = None
_running: bool = False


def is_running() -> bool:
    return _worker_task is not None and not _worker_task.done()


async def start_cron_worker(
    *,
    tenant_config: dict,
    interval_sec: int = DEFAULT_CRON_INTERVAL_SEC,
) -> None:
    """Start the periodic worker. Idempotent."""
    global _worker_task, _running
    if is_running():
        return
    _running = True
    _worker_task = asyncio.create_task(
        _worker_loop(tenant_config=tenant_config, interval_sec=interval_sec)
    )
    logger.info("[cron] worker started — interval=%ds", interval_sec)


async def stop_cron_worker(timeout: float = 5.0) -> None:
    """Stop the periodic worker. Best-effort drain within timeout."""
    global _worker_task, _running
    if not is_running():
        return
    _running = False
    if _worker_task is not None:
        _worker_task.cancel()
        try:
            await asyncio.wait_for(_worker_task, timeout=timeout)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
    _worker_task = None
    logger.info("[cron] worker stopped")


async def _worker_loop(*, tenant_config: dict, interval_sec: int) -> None:
    """Run one sweep, sleep, repeat. Cancellation-friendly."""
    logger.info("[cron] worker loop entered")
    while _running:
        try:
            await sweep_once(tenant_config=tenant_config)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("[cron] sweep failed (non-fatal): %s", exc)
        # Task Engine B2 — само-исполнение отложенных действий бота. Отдельный
        # try/except: баг здесь НЕ должен ломать прогрев/основной sweep.
        try:
            from services.scheduled_actions import (
                run_due_followups, run_due_call_reminders, run_due_recurring,
            )
            # P6 — постоянные клиенты: ставим плановые напоминания ДО доставки
            # followup'ов, чтобы они ушли в этот же проход.
            await run_due_recurring()
            await run_due_followups(tenant_config=tenant_config)
            # Созвоны — напоминания лиду и админу (за день / 3ч / 1ч до созвона).
            await run_due_call_reminders(tenant_config=tenant_config)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("[cron] run_due_followups/call_reminders failed (non-fatal): %s", exc)
        try:
            await asyncio.sleep(interval_sec)
        except asyncio.CancelledError:
            break
    logger.info("[cron] worker loop exited")


async def sweep_once(*, tenant_config: dict) -> dict:
    """One sweep. Returns a stats dict — useful in tests and admin UI later.

    Reads silent customers from DB, applies decay + warming logic, dispatches
    CRM updates, commits DB. Doesn't take a session as input — opens its own
    via session_scope so it isolates from request handlers.
    """
    from db.connection import session_scope
    from db.models import Customer, Conversation

    stats = {
        "examined": 0,
        "temperature_changes": 0,
        "score_changes": 0,
        "warming_tasks_enqueued": 0,
        "funnel_lost_transitions": 0,
    }

    scoring_cfg = (tenant_config or {}).get("scoring", {}) or {}
    temperature_cfg = (tenant_config or {}).get("temperature", {}) or {}
    warming_cfg = (tenant_config or {}).get("warming", {}) or {}
    funnel_cfg = (tenant_config or {}).get("funnel", {}) or {}

    # Admin UI оверрайды (bot_settings, TTL-кэш 60с) поверх config.yaml —
    # прогрев/нудж настраивается из панели без деплоя. Ошибка чтения → дефолты.
    try:
        from services import bot_settings as _bot_settings
        _ui_overrides = _bot_settings.get_all()
    except Exception:  # noqa: BLE001
        _ui_overrides = {}
    if _ui_overrides:
        warming_cfg = dict(warming_cfg)
        for _k in ("nudge_after_hours", "nudge_max_hours"):
            if _k in _ui_overrides:
                warming_cfg[_k] = _ui_overrides[_k]
        if "silence_lost_days" in _ui_overrides:
            funnel_cfg = {**funnel_cfg, "silence_lost_threshold_d": _ui_overrides["silence_lost_days"]}
    _nudge_enabled = bool(_ui_overrides.get("nudge_enabled", True))
    _nudge_text_override = _ui_overrides.get("nudge_text") or None
    decay_per_48h = int(scoring_cfg.get("decay_per_48h", -1))
    temp_decay_days = int(temperature_cfg.get("decay_days", 14))
    temp_frozen_after = int(temperature_cfg.get("frozen_after_days", 21))
    silence_lost_threshold_d = int(funnel_cfg.get("silence_lost_threshold_d", 7))

    now = datetime.now(timezone.utc)

    with session_scope() as s:
        # Candidates: customers with at least one open conversation whose
        # last_message_at is more than CRON_INTERVAL_SEC old — these are
        # the only ones where decay/warming can matter.
        # We deliberately don't filter by crm_contact_id presence — customer
        # rows without a CRM sync still want their lead_temperature decayed
        # in our own Postgres so it's correct when (if) they later sync.
        threshold = now - timedelta(hours=1)
        rows = (
            s.query(Customer, Conversation)
            .join(Conversation, Conversation.customer_id == Customer.id)
            .filter(Conversation.last_message_at != None)  # noqa: E711
            .filter(Conversation.last_message_at < threshold)
            .filter(Conversation.handoff_done == False)  # noqa: E712 — don't warm closed convs
            .order_by(Conversation.last_message_at.asc())
            .limit(MAX_CUSTOMERS_PER_CYCLE)
            .all()
        )

        for customer, conversation in rows:
            stats["examined"] += 1
            last_msg = conversation.last_message_at
            silent_seconds = (now - last_msg).total_seconds()
            silent_days = silent_seconds / 86400.0
            silent_hours = silent_seconds / 3600.0

            # 1. Temperature decay
            old_temp = customer.lead_temperature or "cold"
            new_temp = temperature_decay(
                current_temperature=old_temp,
                silent_days=silent_days,
                decay_days=temp_decay_days,
                frozen_after_days=temp_frozen_after,
            )
            if new_temp != old_temp:
                customer.lead_temperature = new_temp
                conversation.last_temperature_update_at = now
                stats["temperature_changes"] += 1
                logger.info(
                    "[cron] customer=%s temperature %s → %s (silent %.1fd)",
                    customer.id, old_temp, new_temp, silent_days,
                )
                if customer.crm_contact_id:
                    dispatch_temperature_change(
                        customer_id=str(customer.id),
                        crm_contact_id=customer.crm_contact_id,
                        new_temperature=new_temp,
                    )

            # 2. Score decay
            old_score = customer.lead_score or 0
            new_score = score_decay(
                current_score=old_score,
                hours_silent=silent_hours,
                decay_per_48h=decay_per_48h,
                min_score=0,
            )
            if new_score != old_score:
                customer.lead_score = new_score
                stats["score_changes"] += 1
                logger.debug(
                    "[cron] customer=%s score %d → %d (silent %.1fh)",
                    customer.id, old_score, new_score, silent_hours,
                )

            # 3. Funnel: in_dialog silent > N days → lost(delayed)
            #    Also: completed_won + 30d → post_sale (Phase 10c — upsell window).
            current_stage = conversation.lead_stage or "new_lead"
            funnel_decision = None
            if current_stage == "completed_won":
                # Look at how many days since the deal moved to completed_won.
                # last_temperature_update_at is the closest cron-touched timestamp;
                # if missing fall back to last_message_at as a proxy.
                ref = conversation.last_temperature_update_at or last_msg
                days_since_completed = (now - ref).days
                funnel_decision = decide_post_sale_window(
                    current_stage=current_stage,
                    days_since_completed=days_since_completed,
                )
            else:
                funnel_decision = decide_on_silence(
                    current_stage=current_stage,
                    silent_days=int(silent_days),
                    silence_lost_threshold_d=silence_lost_threshold_d,
                )
            if (
                funnel_decision is not None
                and funnel_decision.should_transition
                and funnel_decision.target_stage
                and can_auto_transition(current_stage, funnel_decision.target_stage)
            ):
                new_stage = funnel_decision.target_stage
                conversation.lead_stage = new_stage
                if new_stage == "lost":
                    conversation.lost_reason = funnel_decision.lost_reason
                stats["funnel_lost_transitions"] += 1
                logger.info(
                    "[cron] funnel: conv=%s %s → %s (%s)",
                    conversation.id, current_stage, new_stage, funnel_decision.reason,
                )
                dispatch_stage_change(
                    customer_id=str(customer.id),
                    crm_deal_id=conversation.crm_deal_id,
                    new_stage=new_stage,
                    lost_reason=funnel_decision.lost_reason,
                    conversation_id=str(conversation.id),
                )

            # 4. Warming — enqueue operator task when bucket says it's time.
            # Phase 10d: last_warmed_days_ago comes from conversation.last_warmed_at
            # so cadence properly dedups (no more duplicate tasks every hour).
            last_warmed_days_ago: Optional[float] = None
            if conversation.last_warmed_at is not None:
                last_warmed_days_ago = (now - conversation.last_warmed_at).total_seconds() / 86400.0
            warm_action = plan_warming(
                customer_id=str(customer.id),
                current_temperature=customer.lead_temperature or "cold",
                silent_days=silent_days,
                last_warmed_days_ago=last_warmed_days_ago,
                config_warming=warming_cfg,
                now=now,
            )
            if warm_action is not None and customer.crm_contact_id:
                # Classify why they paused so the task title is meaningful
                pause_type = classify_pause(
                    last_lead_message=None,  # we don't have it without an extra query
                    operator_paused=bool(conversation.operator_takeover),
                )
                title = (
                    f"Warm {customer.lead_temperature or 'cold'} lead "
                    f"({pause_type}) — {customer.name or customer.email or str(customer.id)[:8]}"
                )
                dispatch_operator_task(
                    customer_id=str(customer.id),
                    crm_contact_id=customer.crm_contact_id,
                    crm_deal_id=conversation.crm_deal_id,
                    conversation_id=str(conversation.id),
                    title=title,
                    category="warming",
                    due_in_minutes=0,
                    description=(
                        f"Lead silent for {silent_days:.1f} days. Format suggestion: "
                        f"{warm_action.format}. Reason: {warm_action.reason}"
                    ),
                )
                # Phase 10d — record dispatch time so next cron cycles
                # honour the bucket cadence and don't spam duplicates.
                conversation.last_warmed_at = now
                stats["warming_tasks_enqueued"] += 1

            # Проактивный бот-нудж: свежий вовлечённый лид в МЕССЕНДЖЕРЕ пропал
            # (не handoff, не бронь, не «позже»). Бот ОДИН раз мягко пингует —
            # чтобы не терять лида (для сейл-бота это важно). Дедуп: только если
            # у диалога ещё НЕТ ни одного bot-followup (включая «позже»/отправленные)
            # → один нудж за диалог, не спамим. Кадэнс/пороги — config warming.*
            try:
                _nudge_after = float(warming_cfg.get("nudge_after_hours", 1))
                _nudge_max = float(warming_cfg.get("nudge_max_hours", 36))
                _chan = (conversation.channel or "").lower()
                _chat = conversation.channel_conversation_id
                _booked = bool((customer.profile_data or {}).get("booked_call_at"))
                _engaged = int(customer.lead_score or 0) >= 40
                if (_nudge_enabled
                        and _chat and _chan in ("telegram", "whatsapp", "instagram", "messenger")
                        and _engaged and not _booked
                        and _nudge_after <= silent_hours <= _nudge_max):
                    from db.models import ScheduledAction
                    _exists = (
                        s.query(ScheduledAction.id)
                        .filter(ScheduledAction.conversation_id == conversation.id,
                                ScheduledAction.action_type == "followup_message",
                                ScheduledAction.executor == "bot")
                        .first()
                    )
                    if not _exists:
                        from services.scheduled_actions import write_scheduled_action
                        write_scheduled_action(
                            customer_id=str(customer.id),
                            conversation_id=str(conversation.id),
                            channel=conversation.channel,
                            chat_id=str(_chat),
                            due_at=now,
                            text=(_nudge_text_override or
                                  "Здравствуйте! Вы недавно интересовались — подскажите, "
                                  "актуально ещё? С радостью помогу с проектом 🙂 Если сейчас "
                                  "неудобно, просто скажите, когда вам написать."),
                        )
                        stats["bot_nudges"] = stats.get("bot_nudges", 0) + 1
                        logger.info("[cron] bot-nudge → silent %s lead conv=%s (%.1fh)",
                                    customer.lead_temperature, str(conversation.id)[:8], silent_hours)
            except Exception as _ne:  # noqa: BLE001
                logger.warning("[cron] bot-nudge skipped: %s", _ne)

        # session_scope commits on exit

    # Пользовательские автоматизации «Когда → Если → То» (Admin UI) — после
    # встроенной логики, изолированно: ошибка движка не валит свип.
    try:
        from services.automation import run_automations
        stats["automations"] = await run_automations()
    except Exception as _ae:  # noqa: BLE001
        logger.warning("[cron] automations skipped: %s", _ae)
        stats["automations"] = {"error": str(_ae)}

    # Утренний AI-дайджест владельцу (раз в день в свой час, дедуп внутри).
    try:
        from services.digest import run_digest_if_due
        _dg = await run_digest_if_due()
        if _dg.get("sent"):
            stats["digest"] = _dg
    except Exception as _de:  # noqa: BLE001
        logger.warning("[cron] digest skipped: %s", _de)

    if any(v for k, v in stats.items() if k != "examined") or stats["examined"] > 0:
        logger.info(
            "[cron] sweep complete — examined=%d temp_changes=%d score_changes=%d "
            "warming_tasks=%d funnel_lost=%d automations=%s",
            stats["examined"], stats["temperature_changes"], stats["score_changes"],
            stats["warming_tasks_enqueued"], stats["funnel_lost_transitions"],
            stats.get("automations"),
        )
    return stats
