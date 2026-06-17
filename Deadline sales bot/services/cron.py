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
_CRON_CYCLE = [0]   # счётчик циклов (для разреженных задач — авто-сверки раз в ~час)
_LAST_BACKUP_DATE = [None]   # дата последнего авто-бэкапа БД в Telegram (раз в день)


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


def run_wa_maintenance() -> dict:
    """Дешёвая (ТОЛЬКО БД, без WAHA/LLM) поддержка актуальности WhatsApp-панели:
    чистка фантомов + эхо-дублей, структурное слияние разорванных карточек одного
    телефона (@lid живой вебхук + @c.us history-sync → одна карточка, кейс Zaal),
    дедуп @lid-теней по телефону/имени, гашение осиротевших задач/напоминаний,
    авто-архив «Не сложилось» старше N дней. Идемпотентно и безопасно часто.
    Вызывается из крон-цикла И из кнопки «Проверить сейчас» (/admin/api/cron/sweep).
    Возвращает сводку по каждому шагу."""
    summary: dict = {}
    try:
        from services.whatsapp_sync import (
            cleanup_wa_artifacts, dedup_wa_by_phone, dedup_wa_by_name,
            cancel_orphan_scheduled_actions, dedup_scheduled_actions,
            merge_wa_split,
        )
        _cl = cleanup_wa_artifacts()
        summary["cleanup"] = _cl
        if _cl.get("phantoms") or _cl.get("echo_dupes"):
            logger.info("[cron] wa cleanup: %s", _cl)
        # СТРУКТУРНОЕ слияние разорванных карточек по реальному телефону: @lid
        # (живой вебхук) + @c.us (history-sync) одного человека → ОДНА карточка с
        # ПЕРЕНЕСЁННОЙ историей (кейс Zaal). Чисто БД, идемпотентно. ДО лёгких
        # дедупов — он сильнее (переносит сообщения, а не только архивит).
        _mg = merge_wa_split()
        summary["merge_split"] = _mg
        if _mg.get("moved_msgs") or _mg.get("archived"):
            logger.info("[cron] wa merge-split: groups=%s convs=%s moved=%s archived=%s",
                        _mg.get("groups"), _mg.get("merged_convs"),
                        _mg.get("moved_msgs"), _mg.get("archived"))
        # Дедуп @lid-дублей: по штампованному телефону + по имени (@lid-тень того
        # же контакта, у которой телефон не разрезолвлен). Чисто БД, без сети.
        _dd = dedup_wa_by_phone()
        _dn = dedup_wa_by_name()
        summary["dedup_phone"] = _dd
        summary["dedup_name"] = _dn
        if _dd.get("archived") or _dn.get("archived"):
            logger.info("[cron] wa dedup: by_phone=%s by_name=%s", _dd, _dn)
        # Гасим осиротевшие задачи/напоминания архивных карточек + дедуп ОДИНАКОВЫХ
        # задач (один «Лид завис — связаться» на лида) → чистый задачник/календарь.
        _orf = cancel_orphan_scheduled_actions()
        _ds = dedup_scheduled_actions()
        summary["orphan_actions"] = _orf
        summary["dedup_actions"] = _ds
        if _orf.get("superseded") or _ds.get("superseded"):
            logger.info("[cron] actions: orphan=%s dups=%s", _orf, _ds)
        # Авто-архивация «Не сложилось» старше N дней (если задано в настройках) —
        # старая база не засоряет активный вид. Обратимо (status=ARCHIVED, не удаляем).
        try:
            from services import bot_settings as _bs2, funnel_store as _fs2
            from db.connection import session_scope as _ss2
            _days = _bs2.get("lost_auto_archive_days")
            if isinstance(_days, int) and _days > 0:
                with _ss2() as _db2:
                    _al = _fs2.archive_lost_leads(_db2, older_than_days=_days)
                summary["lost_archive"] = _al
                if _al.get("archived"):
                    logger.info("[cron] lost auto-archive (>%dд): %s", _days, _al)
        except Exception as _ae:  # noqa: BLE001
            logger.warning("[cron] lost auto-archive failed: %s", _ae)
        # Чистка таблицы идемпотентности входящих: ключи старше 3д не нужны (окно
        # ретраев платформ — минуты/часы), иначе processed_updates растёт без предела.
        try:
            from db.connection import session_scope as _ss3
            from sqlalchemy import text as _txt
            with _ss3() as _db3:
                _pr = _db3.execute(_txt(
                    "DELETE FROM processed_updates "
                    "WHERE created_at < now() - interval '3 days'"))
                if _pr.rowcount:
                    summary["pruned_dedup"] = _pr.rowcount
                    logger.info("[cron] pruned %s old processed_updates", _pr.rowcount)
        except Exception as _pe:  # noqa: BLE001
            logger.warning("[cron] processed_updates prune failed: %s", _pe)
        # Журнал активности: хранение 30 дней (не растёт без предела).
        try:
            from services.activity_log import prune as _alp
            _n = _alp(days=30)
            if _n:
                summary["pruned_activity_log"] = _n
        except Exception as _ape:  # noqa: BLE001
            logger.warning("[cron] activity_log prune failed: %s", _ape)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[cron] wa maintenance failed (non-fatal): %s", exc)
        summary["error"] = str(exc)
    return summary


async def resolve_lid_backlog(*, limit: int = 8) -> dict:
    """Добить РЕАЛЬНЫЙ телефон у `@lid`-карточек (рекламные лиды), у которых он ещё не
    разрезолвлен. Кейс Heinrich: history-synced `@lid` приходит БЕЗ телефона, живой
    вебхук его не трогает → номер `null`, хотя это WhatsApp. Берём ВСЮ карту
    `@lid → телефон` у WAHA ОДНИМ запросом (fetch_lid_map) и сопоставляем со ВСЕМИ
    нашими нерешёнными карточками — вместо точечных вызовов по одной (быстрее, без
    лимита, ловит всё знаемое разом). СЕТЬ — вне сессии (урок висов 06-15): сначала
    карта, потом сбор кандидатов в короткой сессии, апдейт — в коротких сессиях.
    После резолва merge_wa_split (тот же цикл) перекеит карточку в phone-canonical.
    `hidden` = карточки, чей номер WhatsApp прячет (приватность рекламы) — это норма,
    в UI помечаются «номер скрыт». Идемпотентно. `limit` оставлен для совместимости."""
    out: dict = {"resolved": [], "checked": 0, "hidden": 0, "known_map": 0}
    try:
        import re as _re
        import main as _main
        from channels.waha import fetch_lid_map
        from db.connection import session_scope
        from db.models import Conversation, Customer, ConversationStatusEnum
        st = _main.settings
        if not getattr(st, "waha_base_url", None):
            return out  # WAHA не настроен — нечего резолвить
        # ОДИН сетевой вызов: вся карта @lid→телефон, что знает WAHA.
        lid_map = await fetch_lid_map(
            st.waha_base_url, st.waha_api_key or "", st.waha_session or "default")
        out["known_map"] = len(lid_map)
        if not lid_map:
            return out
        # Кандидаты: активные whatsapp-карточки под @lid без телефона.
        cands: list = []
        with session_scope() as db:
            rows = (
                db.query(Conversation, Customer)
                .join(Customer, Conversation.customer_id == Customer.id)
                .filter(Conversation.channel == "whatsapp",
                        Conversation.status != ConversationStatusEnum.ARCHIVED)
                .all()
            )
            for _conv, _cust in rows:
                if (_cust.phone or "").strip():
                    continue
                d = _re.sub(r"\D", "", _conv.channel_conversation_id or "")
                if len(d) >= 13:
                    cands.append((str(_cust.id), d))
        out["checked"] = len(cands)
        # Сопоставляем с картой; чего WAHA не знает — «скрытый» (норма для рекламы).
        to_set: list = []
        for _cust_id, d in cands:
            pn = lid_map.get(d)
            if pn:
                to_set.append((_cust_id, pn))
            else:
                out["hidden"] += 1
        for _cust_id, pn in to_set:  # апдейт короткими сессиями
            with session_scope() as db:
                _c = db.query(Customer).filter(Customer.id == _cust_id).first()
                if _c is not None and not (_c.phone or "").strip():
                    _c.phone = ("+" + pn)[:50]
                    out["resolved"].append({"customer": _cust_id, "phone": pn})
        if out["resolved"] or out["hidden"]:
            logger.info("[cron] lid-resolve: map=%s resolved=%s hidden=%s",
                        out["known_map"], len(out["resolved"]), out["hidden"])
    except Exception as exc:  # noqa: BLE001
        logger.warning("[cron] lid-resolve failed (non-fatal): %s", exc)
        out["error"] = str(exc)
    return out


async def _worker_loop(*, tenant_config: dict, interval_sec: int) -> None:
    """Run one sweep, sleep, repeat. Cancellation-friendly."""
    logger.info("[cron] worker loop entered")
    # Стартовая пауза: не нагружаем контейнер тяжёлым sweep, пока он прогревается
    # (загрузка bge-m3 + KB + первые запросы). Иначе коллизия на старте → пул под
    # давлением → /health не отвечает (вис, инцидент 06-15). Даём 60с осесть.
    try:
        await asyncio.sleep(60)
    except asyncio.CancelledError:
        return
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
        # @lid-лиды (реклама): добить реальный телефон через WAHA (СЕТЬ, bounded 8) —
        # history-synced карточки сами не резолвятся (кейс Heinrich). ДО maintenance,
        # чтобы merge_wa_split тут же перекеил разрезолвленную карточку в phone-canonical.
        try:
            await resolve_lid_backlog(limit=8)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("[cron] lid-resolve loop failed (non-fatal): %s", exc)
        # Постоянная актуальность панели = WhatsApp: дешёвая (только БД) авто-чистка
        # фантомов/эхо-дублей + слияние разорванных карточек + дедуп каждый цикл.
        # Тот же код доступен по кнопке «Проверить сейчас» (POST /admin/api/cron/sweep).
        run_wa_maintenance()
        # АВТО-БЭКАП БД раз в день → владельцу в Telegram (offsite-копия на случай
        # потери системы/номера; Telegram хранит файл). Дамп в потоке — не держит loop.
        # Выключить: env DB_BACKUP_TG=0.
        try:
            import os as _osb
            if _osb.getenv("DB_BACKUP_TG", "1").strip() in ("1", "true", "yes"):
                _today = datetime.now(timezone.utc).date().isoformat()
                if _LAST_BACKUP_DATE[0] != _today:
                    import main as _mb
                    from services import bot_settings as _bs
                    _chat = (_bs.get("manager_chat_id") or "").strip() \
                        or (getattr(_mb.settings, "telegram_chat_id", None) or "")
                    _token = getattr(_mb.settings, "telegram_bot_token", None)
                    if _token and _chat:
                        from services.db_backup import build_export
                        from channels.telegram import send_telegram_document
                        _fn, _blob = await asyncio.to_thread(build_export)
                        if await send_telegram_document(_token, str(_chat), _fn, _blob,
                                                        caption="💾 Авто-бэкап базы DEADLINE"):
                            _LAST_BACKUP_DATE[0] = _today
                            logger.info("[cron] db backup → telegram: %s (%d КБ)", _fn, len(_blob) // 1024)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[cron] db backup failed (non-fatal): %s", exc)
        # ПОЛНАЯ авто-сверка с WhatsApp раз в ~час (каждый 6-й цикл): WAHA = источник
        # правды, убирает локальные сообщения, которых в WhatsApp НЕТ (в т.ч. ложно-
        # «delivered», что обычная чистка не ловит), подтягивает новые. БЕЗ LLM
        # (classify=False) + bounded. Сетка безопасности — вотчдог (если зависнет,
        # авто-рестарт). Выключить: env WA_AUTO_RECONCILE=0.
        import os as _osr
        _CRON_CYCLE[0] += 1
        if (_osr.getenv("WA_AUTO_RECONCILE", "1").strip() in ("1", "true", "yes")
                and _CRON_CYCLE[0] % 6 == 0):
            try:
                from services.whatsapp_sync import sync_waha_history
                from db.connection import session_scope
                import main as _mr
                with session_scope() as _rdb:
                    _r = await sync_waha_history(
                        _rdb, _mr.settings, llm=None,
                        max_chats=150, per_chat_messages=40,
                        classify=False, reconcile=True,
                    )
                logger.info("[cron] auto-reconcile: matched=%s mismatched=%s removed=%s",
                            _r.get("chats_matched"), _r.get("chats_mismatched"),
                            _r.get("phantoms_removed"))
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning("[cron] auto-reconcile failed (non-fatal): %s", exc)
        # Умное авто-ведение WhatsApp — периодическая проверка актуальности:
        # ловит ручные договорённости/новую инфу, которые могли не прийти вебхуком,
        # двигает воронку и ставит созвон в календарь. ОПАСНО на едином процессе с
        # sync-SQLAlchemy: LLM-вызовы держат коннекты → пул может исчерпаться (вис,
        # инцидент 06-02). Поэтому ПО УМОЛЧАНИИ ВЫКЛ — включается env WA_BRAIN_SWEEP=1
        # только после подтверждённой стабильности (sweep_recent уже отпускает коннект
        # между диалогами). Реальное время (вебхук/ручной ответ) работает всегда.
        import os as _os
        if _os.getenv("WA_BRAIN_SWEEP", "").strip() in ("1", "true", "yes"):
            try:
                from services.conversation_brain import sweep_recent
                import main as _m
                res = await sweep_recent(_m.primary_llm, _m.settings)
                if res.get("analyzed"):
                    logger.info("[cron] brain sweep: %s", res)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning("[cron] brain sweep failed (non-fatal): %s", exc)
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
