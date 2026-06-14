"""Утренний AI-дайджест — система сама приходит к владельцу в Telegram.

Раз в день (digest_hour по локальному времени, дефолт 08:00 Бангкок UTC+7)
крон собирает сводку за сутки и шлёт в TELEGRAM_CHAT_ID:
  - новые лиды (по каналам), передачи команде, сработавшие автоматизации
  - «зависшие тёплые» — кого дожать сегодня (имя + сколько молчит)
  - просроченные задачи
  - один совет от LLM (best-effort: упал LLM → шлём без совета)

Конкуренты дают цифры в дашборде — мы приносим действия в карман.
Дедуп: digest_last_date в bot_settings (YYYY-MM-DD локальной даты).
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)


def _now_local(offset_h: int) -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=offset_h)


async def run_digest_if_due() -> dict:
    """Зовётся кроном каждые ~10 мин. Шлёт максимум раз в день, в свой час."""
    from services import bot_settings

    cfg = bot_settings.get_all()
    if not cfg.get("digest_enabled", True):
        return {"skipped": "disabled"}
    hour = int(cfg.get("digest_hour", 8))
    offset = int(cfg.get("digest_tz_offset", 7))
    local = _now_local(offset)
    if local.hour != hour:
        return {"skipped": "not the hour"}
    today = local.strftime("%Y-%m-%d")
    if cfg.get("digest_last_date") == today:
        return {"skipped": "already sent today"}

    result = await send_digest()
    if result.get("sent"):
        try:
            bot_settings.set_many({"digest_last_date": today})
        except Exception as e:  # noqa: BLE001
            logger.warning("digest: failed to store last_date: %s", e)
    return result


def _collect_data() -> dict:
    from db.connection import session_scope
    from db.models import Customer, Conversation, ScheduledAction, AutomationRun

    now = datetime.now(timezone.utc)
    day_ago = now - timedelta(hours=24)
    out: dict = {}

    with session_scope() as s:
        new_leads = (
            s.query(Customer).filter(Customer.created_at >= day_ago).all()
        )
        # Не считаем демо-лидов — дайджест про реальную работу.
        real_new = [c for c in new_leads if not (c.profile_data or {}).get("demo")]
        by_channel: dict = {}
        for c in real_new:
            ch = str(c.first_channel or "?").lower()
            by_channel[ch] = by_channel.get(ch, 0) + 1
        out["new_leads"] = len(real_new)
        out["by_channel"] = by_channel

        out["handoffs_24h"] = (
            s.query(Conversation)
            .filter(Conversation.handoff_done == True)  # noqa: E712
            .filter(Conversation.last_message_at >= day_ago)
            .count()
        )
        out["automation_fires_24h"] = (
            s.query(AutomationRun).filter(AutomationRun.fired_at >= day_ago).count()
        )

        # Зависшие тёплые: скор ≥40, открытый диалог, молчат 48ч+ (не демо).
        stuck_rows = (
            s.query(Conversation, Customer)
            .join(Customer, Conversation.customer_id == Customer.id)
            .filter(Conversation.status == "open")
            .filter(Customer.lead_score >= 40)
            .filter(Conversation.last_message_at.isnot(None))
            .filter(Conversation.last_message_at <= now - timedelta(hours=48))
            .order_by(Customer.lead_score.desc())
            .limit(8)
            .all()
        )
        stuck = []
        for conv, cust in stuck_rows:
            if (cust.profile_data or {}).get("demo"):
                continue
            lm = conv.last_message_at
            if lm and lm.tzinfo is None:
                lm = lm.replace(tzinfo=timezone.utc)
            days = round((now - lm).total_seconds() / 86400, 1) if lm else 0
            stuck.append({
                "name": cust.name or cust.email or "лид",
                "stage": conv.lead_stage,
                "score": cust.lead_score,
                "silent_days": days,
            })
        out["stuck"] = stuck[:5]

        out["overdue_tasks"] = (
            s.query(ScheduledAction)
            .filter(ScheduledAction.status == "pending",
                    ScheduledAction.executor == "human",
                    ScheduledAction.due_at < now)
            .count()
        )
    return out


def _format_message(d: dict, advice: str | None) -> str:
    ch_names = {"website": "сайт", "telegram": "TG", "instagram": "IG", "messenger": "FB"}
    ch_str = ", ".join(f"{ch_names.get(k, k)}: {v}" for k, v in d["by_channel"].items()) or "—"
    lines = [
        "☀️ Утренний дайджест продаж",
        "",
        f"За сутки: 🆕 {d['new_leads']} новых лидов ({ch_str}) · "
        f"🤝 {d['handoffs_24h']} передано команде · ⚡ {d['automation_fires_24h']} автоматизаций",
    ]
    if d["stuck"]:
        lines.append("")
        lines.append("🔥 Дожать сегодня (тёплые молчат):")
        for x in d["stuck"]:
            lines.append(f"  • {x['name']} — {x['silent_days']} дн тишины, скор {x['score']}, стадия {x['stage']}")
    if d["overdue_tasks"]:
        lines.append("")
        lines.append(f"🔴 Просроченных задач: {d['overdue_tasks']} — загляните в «Задачи»")
    if not d["stuck"] and not d["overdue_tasks"] and d["new_leads"] == 0:
        lines.append("")
        lines.append("Тихо. Хороший день, чтобы проверить рекламу или добавить правило в Мозг 🙂")
    if advice:
        lines.append("")
        lines.append(f"💡 {advice}")
    return "\n".join(lines)


async def send_digest() -> dict:
    """Собрать и отправить дайджест сейчас (используется кроном и кнопкой-тестом)."""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not (token and chat_id):
        return {"sent": False, "error": "TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID не настроены"}

    try:
        data = _collect_data()
    except Exception as e:  # noqa: BLE001
        logger.warning("digest: collect failed: %s", e)
        return {"sent": False, "error": f"collect: {e}"}

    # Совет от LLM — best-effort, без него дайджест всё равно уходит.
    advice = None
    try:
        import main as _main
        prompt = (
            "Ты — коуч отдела продаж. По сводке дай ОДИН короткий конкретный совет "
            "(1-2 предложения, по-русски, без воды, начни с глагола). Сводка: "
            f"{data}"
        )
        resp = await _main.primary_llm.ainvoke(prompt)
        advice = (resp.content or "").strip()[:300] or None
    except Exception as e:  # noqa: BLE001
        logger.warning("digest: LLM advice skipped: %s", e)

    text = _format_message(data, advice)
    try:
        import httpx
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": text},
            )
        ok = r.status_code == 200
        if not ok:
            logger.warning("digest: telegram %s: %s", r.status_code, r.text[:200])
        return {"sent": ok, "chars": len(text)}
    except Exception as e:  # noqa: BLE001
        logger.warning("digest: send failed: %s", e)
        return {"sent": False, "error": str(e)}
