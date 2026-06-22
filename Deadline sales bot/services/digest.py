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


# Стадии, которые НЕ «тёплые молчат» — их нельзя дожимать как живых (исправляет «стадия lost»).
_DEAD_STAGES = ("lost", "completed_won", "post_sale", "archived")


def _norm_digits(s) -> str:
    return "".join(ch for ch in str(s or "") if ch.isdigit())


def _clean_name(s) -> str:
    """Схлопнуть пробелы/перевести в нормальную форму (убирает огрызки «Александр » с хвостом)."""
    return " ".join((s or "").split()).strip()


def _parse_exclude(raw) -> set:
    """CSV из digest_exclude → множество (имена/телефоны/почты владельца и внутренних)."""
    return {p.strip() for p in str(raw or "").split(",") if p.strip()}


def _excluded(c, exclude: set) -> bool:
    """Карточка — в exclude-списке владельца (имя/телефон/почта). Чтобы владелец и
    внутренние НЕ считались лидами и не лезли в «дожать» (исправляет «Александр Егоров в лидах»)."""
    if not exclude:
        return False
    name = (getattr(c, "name", None) or "").strip().lower()
    email = (getattr(c, "email", None) or "").strip().lower()
    phone = _norm_digits(getattr(c, "phone", None))
    for e in exclude:
        ed = _norm_digits(e)
        if ed and len(ed) >= 5 and phone and (ed in phone or phone in ed):
            return True
        el = e.strip().lower()
        if el and (el == name or (email and el == email)):
            return True
    return False


def _is_real_lead(c, exclude: set) -> bool:
    """Реальный лид для счётчиков: не demo, не тень-дубль (merged_into), не из exclude.
    Один предикат для «за сутки» и «за 7 дней» — чтобы счёт был ОДИНАКОВЫЙ (исправляет «90»)."""
    pd = c.profile_data or {}
    if pd.get("demo") or pd.get("merged_into"):
        return False
    return not _excluded(c, exclude)


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

    from services import bot_settings
    exclude = _parse_exclude(bot_settings.get("digest_exclude"))

    with session_scope() as s:
        new_leads = (
            s.query(Customer).filter(Customer.created_at >= day_ago).all()
        )
        # Реальные лиды: не demo, не тень-дубль (merged_into), не владелец/внутренние.
        real_new = [c for c in new_leads if _is_real_lead(c, exclude)]
        by_channel: dict = {}
        for c in real_new:
            # .value — иначе Python 3.11 str(enum) даёт «ChannelEnum.WHATSAPP» → «channelenum.whatsapp».
            ch = (getattr(c.first_channel, "value", None) or str(c.first_channel or "?")).lower()
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

        # Неделя-к-неделе (тренд): лиды за 7 дней vs прошлые 7 дней (без демо) +
        # выручка закрытых сделок за неделю — чтобы владелец видел динамику, не только сутки.
        week_ago = now - timedelta(days=7)
        two_weeks_ago = now - timedelta(days=14)

        def _real_count(rows):
            return sum(1 for c in rows if _is_real_lead(c, exclude))

        out["week_leads"] = _real_count(
            s.query(Customer).filter(Customer.created_at >= week_ago).all()
        )
        out["prev_week_leads"] = _real_count(
            s.query(Customer).filter(Customer.created_at >= two_weeks_ago,
                                     Customer.created_at < week_ago).all()
        )
        try:
            from sqlalchemy import func as _f
            from services import funnel_store as _fs
            _won = [st["key"] for st in _fs.get_stages(s) if st.get("kind") == "won"] \
                or ["completed_won", "post_sale"]
            _wv = (
                s.query(_f.coalesce(_f.sum(Conversation.deal_value), 0))
                .filter(Conversation.deal_value.isnot(None),
                        Conversation.lead_stage.in_(_won),
                        Conversation.last_message_at >= week_ago)
                .scalar()
            )
            out["week_won_value"] = float(_wv or 0)
        except Exception as _re:  # noqa: BLE001
            logger.debug("digest: weekly revenue skipped: %s", _re)
            out["week_won_value"] = 0.0

        # Зависшие тёплые: скор ≥40, открытый диалог, молчат 48ч+, НЕ мёртвая стадия
        # (lost/выигран/архив — это НЕ «дожать»). Берём с запасом (30) — отфильтруем в Python.
        from sqlalchemy import or_ as _or
        stuck_rows = (
            s.query(Conversation, Customer)
            .join(Customer, Conversation.customer_id == Customer.id)
            .filter(Conversation.status == "open")
            .filter(Customer.lead_score >= 40)
            .filter(Conversation.last_message_at.isnot(None))
            .filter(Conversation.last_message_at <= now - timedelta(hours=48))
            .filter(_or(Conversation.lead_stage.is_(None),
                        Conversation.lead_stage.notin_(list(_DEAD_STAGES))))
            .order_by(Customer.lead_score.desc())
            .limit(30)
            .all()
        )
        stuck = []
        for conv, cust in stuck_rows:
            if not _is_real_lead(cust, exclude):
                continue  # demo / тень-дубль / владелец-внутренний
            if (conv.lead_stage or "") in _DEAD_STAGES:
                continue  # страховка к SQL-фильтру
            nm = _clean_name(cust.name)
            if len(nm) < 2 and not cust.email:
                continue  # мусорное имя без почты — не показываем огрызок «a»
            lm = conv.last_message_at
            if lm and lm.tzinfo is None:
                lm = lm.replace(tzinfo=timezone.utc)
            days = round((now - lm).total_seconds() / 86400, 1) if lm else 0
            stuck.append({
                "name": nm or cust.email or "лид",
                "stage": conv.lead_stage,
                "score": cust.lead_score,
                "silent_days": days,
            })
            if len(stuck) >= 5:
                break
        out["stuck"] = stuck

        out["overdue_tasks"] = (
            s.query(ScheduledAction)
            .filter(ScheduledAction.status == "pending",
                    ScheduledAction.executor == "human",
                    ScheduledAction.due_at < now)
            .count()
        )
    return out


def _format_message(d: dict, advice: str | None) -> str:
    ch_names = {"website": "сайт", "telegram": "TG", "instagram": "IG", "messenger": "FB",
                "whatsapp": "WA", "email": "почта", "tiktok": "TikTok", "line": "LINE"}
    ch_str = ", ".join(f"{ch_names.get(k, k)}: {v}" for k, v in d["by_channel"].items()) or "—"
    lines = [
        "☀️ Утренний дайджест продаж",
        "",
        f"За сутки: 🆕 {d['new_leads']} новых лидов ({ch_str}) · "
        f"🤝 {d['handoffs_24h']} передано команде · ⚡ {d['automation_fires_24h']} автоматизаций",
    ]
    # Тренд неделя-к-неделе + выручка за 7 дней.
    wl, pl = int(d.get("week_leads", 0)), int(d.get("prev_week_leads", 0))
    if wl or pl:
        if pl > 0:
            pct = round((wl - pl) / pl * 100)
            arrow = "▲" if pct > 0 else ("▼" if pct < 0 else "■")
            delta = f"{arrow} {abs(pct)}% к прошлой неделе"
        else:
            delta = "на прошлой неделе лидов не было"
        lines.append(f"📊 За 7 дней: {wl} лидов ({delta})")
    wv = float(d.get("week_won_value", 0) or 0)
    if wv:
        lines.append(f"💰 Выручка за 7 дней (закрыто): {('{:,.0f}'.format(wv)).replace(',', ' ')}")
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
