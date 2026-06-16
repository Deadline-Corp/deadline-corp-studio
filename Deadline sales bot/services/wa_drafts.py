"""Подготовка предложенных ответов (pending_wa_draft) для WhatsApp-лидов.

Один источник правды для всех мест, где рождается черновик:
- пакетная подготовка   (admin_api `/whatsapp/prepare-drafts`)
- ручная кнопка         (admin_api `/whatsapp/.../suggest-reply`)
- удержание ответа бота  (main `_wa_route_answer`, режим наблюдения/черновика)
- АВТО-ОБНОВЛЕНИЕ устаревшего черновика, когда в переписке появились новые
  реплики лида ИЛИ ручной ответ оператора с телефона.

Зачем «based_on_count»: черновик отвечает на состояние диалога на момент
генерации. Если после этого лид написал ещё, или менеджер сам ответил вручную
(fromMe), старый текст уже неактуален — он отвечает не на последнее сообщение.
`based_on_count` фиксирует число реплик на момент генерации; `is_stale`
сравнивает с текущим. Старые черновики без поля считаем устаревшими, чтобы
они обновились при первом открытии карточки.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy.orm import Session

from db.models import Message

log = logging.getLogger(__name__)

# Роли, которые двигают диалог и потому влияют на «правильный следующий ответ».
_DIALOG_ROLES = ("user", "assistant", "operator")


def count_dialog_messages(db: Session, conversation_id) -> int:
    """Сколько реплик лида/нас/оператора в диалоге — мера «свежести» черновика."""
    return (
        db.query(Message)
        .filter(Message.conversation_id == conversation_id)
        .filter(Message.role.in_(_DIALOG_ROLES))
        .count()
    )


def is_stale(db: Session, conv: Any) -> bool:
    """True, если черновик есть, но после его создания появились новые реплики
    (или у черновика нет отметки based_on_count — значит сделан до этой логики)."""
    draft = getattr(conv, "pending_wa_draft", None)
    if not draft:
        return False
    based = draft.get("based_on_count")
    if based is None:
        return True
    return count_dialog_messages(db, conv.id) > int(based)


def _build_dialog(db: Session, conv: Any) -> tuple[str, str]:
    """Последние 8 реплик → (текст диалога для промпта, последнее сообщение лида)."""
    recent = (
        db.query(Message)
        .filter(Message.conversation_id == conv.id)
        .order_by(Message.created_at.desc())
        .limit(8)
        .all()
    )
    dialog = "\n".join(
        f"{'Лид' if m.role == 'user' else 'Мы'}: {m.content[:300]}"
        for m in reversed(recent)
        if m.role in _DIALOG_ROLES
    )
    last_user = next((m.content for m in recent if m.role == "user"), "")
    return dialog, last_user


_PRICES = (
    ""
)

_DRAFT_BAD_MARKERS = (
    "when the type", "since the call", "maybe it's better",
    "here is", "draft:", "вот сообщение", "вот ответ", "вот текст",
)


def _clean_draft(text: str) -> str:
    """Подчистить ответ LLM: снять обрамляющие кавычки и редкие случаи, когда
    модель приклеила свои размышления/инструкции (эхо промпта). Если виден
    англоязычный мусор-маркер — отбраковываем (вернём '', вызывающий не запишет)."""
    t = (text or "").strip()
    # снять одинарную пару обрамляющих кавычек
    if len(t) >= 2 and t[0] in "\"'«" and t[-1] in "\"'»":
        t = t[1:-1].strip()
    # АНТИ-УТЕЧКА мета-анализа: модель иногда возвращает СВОЙ разбор вместо реплики
    # («**Tone:** … * **Goal (...):** …»). Восстанавливаем реплику или гасим — клиент
    # не должен видеть внутренние рассуждения (services.reply_polish, тесты test_meta_*).
    from services.reply_polish import strip_meta_analysis
    t = strip_meta_analysis(t)
    if not t:
        return ""
    low = t.lower()
    if any(m in low for m in _DRAFT_BAD_MARKERS):
        # эхо промпта / размышления — лучше пусто (черновик не покажем кривой)
        return ""
    return t


def _kb_context(query: str, k: int = 3) -> str:
    """Достать релевантные факты из базы знаний (pgvector) под запрос лида.
    Best-effort: пусто, если KB не настроена / ошибка. Тяжёлый embed — в потоке
    у вызывающего (здесь sync, но вызывается из to_thread-обёрток выше)."""
    q = (query or "").strip()
    if not q:
        return ""
    try:
        from db.vector import similarity_search
        docs = similarity_search(q, k=k)
        return "\n".join(f"- {d.page_content.strip()}" for d in docs if d.page_content.strip())
    except Exception:  # noqa: BLE001
        return ""


def _corrections_context(query: str, channel: Optional[str] = None, k: int = 3) -> str:
    """Релевантные ПРАВИЛА студии (training_corrections) под сообщение лида —
    чтобы WhatsApp-движок СОБЛЮДАЛ правила из вкладки «Мозг» (часовые пояса,
    воронка-воркфлоу и т.д.), а не игнорировал их. Своя короткая сессия (безопасно
    из to_thread). Best-effort: пусто при любой ошибке."""
    q = (query or "").strip()
    try:
        from services.training import retrieve_corrections
        from db.connection import session_scope
        from db.models import TrainingCorrection
        from sqlalchemy import select
        lines: list = []
        seen: set = set()
        with session_scope() as _db:
            # 1) ВСЕГДА — общие быстрые правила («часовые пояса», «воронка-воркфлоу»):
            #    их мало, это ГЛОБАЛЬНЫЕ инструкции; ретрив по близости их пропускал
            #    (текст правила далёк от сообщения лида) → бот «не слушался».
            quick = _db.execute(
                select(TrainingCorrection.id, TrainingCorrection.correct_guidance)
                .where(TrainingCorrection.is_active.is_(True),
                       TrainingCorrection.created_by == "admin-ui-quick")
                .limit(15)
            ).all()
            for rid, g in quick:
                if g and rid not in seen:
                    seen.add(rid); lines.append(g)
            # 2) + релевантные ситуативные правила (тренер-цикл) по сообщению лида
            if q:
                for r in retrieve_corrections(_db, q, k=k, channel=channel):
                    if r.get("guidance") and r.get("id") not in seen:
                        seen.add(r.get("id")); lines.append(r["guidance"])
        return "\n".join(f"- {g}" for g in lines)
    except Exception:  # noqa: BLE001
        return ""


def _is_silent(dialog: str) -> bool:
    """Лид молчит = ПОСЛЕДНЯЯ строка диалога от НАС («Мы:») — мы написали последними."""
    d = (dialog or "").rstrip()
    return bool(d) and d.rsplit("\n", 1)[-1].startswith("Мы:")


def _active_offer() -> str:
    """Текущий оффер активной рекламы из настроек (bot_settings, кэш 60с)."""
    try:
        from services import bot_settings as _bs
        return (_bs.get_all() or {}).get("wa_active_offer") or ""
    except Exception:  # noqa: BLE001
        return ""


def _prompt(name: str, stage: str, dialog: str, kb: str = "", offer: str = "",
            corrections: str = "", silent: bool = False) -> str:
    kb_block = (
        f"\nФАКТЫ О НАШЕЙ СТУДИИ (фон — про НАС, НЕ про задачу лида; опирайся, не "
        f"выдумывай сверх):\n{kb}\n"
        if kb else ""
    )
    corr_block = (
        f"\nПРАВИЛА СТУДИИ (обязательно соблюдай в ответе):\n{corrections}\n"
        if corrections else ""
    )
    # Первый контакт = в переписке ещё НЕ было нашего ответа («Мы:»).
    first_contact = "Мы:" not in (dialog or "")
    if first_contact and (offer or "").strip():
        # Лид пришёл по рекламе с конкретным оффером — встречаем в его контексте.
        intro_rule = (
            "Это ПЕРВЫЙ наш ответ, и лид пришёл ПО РЕКЛАМЕ с таким оффером:\n"
            f"«{offer.strip()}»\n"
            "Поприветствуй тепло и живо В КОНТЕКСТЕ этого оффера (покажи, что мы как "
            "раз про это и поможем), затем СРАЗУ спроси, что именно он хочет "
            "реализовать / какая у него идея. От лица «мы», по-человечески, можно 1 "
            "эмодзи 🙂. НЕ копируй оффер дословно — обыграй своими словами.\n"
        )
    elif first_contact:
        intro_rule = (
            "Это ПЕРВЫЙ наш ответ — начни тепло и живо от лица студии («Здравствуйте! "
            "Мы в Deadline воплощаем в код любые идеи — сайты, боты, AI, "
            "автоматизацию»), затем СРАЗУ спроси, что именно хочет реализовать. "
            "По-человечески, НЕ сухо, можно 1 эмодзи 🙂\n"
        )
    elif silent:
        intro_rule = (
            "Лид ЗАМОЛЧАЛ после нашего сообщения — не ответил (возможно, оставил "
            "заявку и «уснул»). Напиши КОРОТКОЕ (1-2 предложения) лёгкое, тёплое "
            "сообщение-БУДИЛЬНИК, чтобы по-доброму вернуть его в диалог и мягко "
            "позвать рассказать, что он хочет реализовать. Можно живая человеческая "
            "нотка или уместный МЯГКИЙ юмор — БЕЗ давления, без упрёков («вы "
            "пропали»/«вы не ответили»). Формулируй КАЖДЫЙ раз ПО-РАЗНОМУ, не шаблон. "
            "Опирайся ТОЛЬКО на то, что он реально писал — не придумывай за него.\n"
        )
    else:
        intro_rule = "Диалог уже идёт — без «здравствуйте», продолжай по сути.\n"
    return (
        "Ты — ГОЛОС студии Deadline (сайты, боты, AI, автоматизация) в WhatsApp. "
        "Говори от лица КОМПАНИИ — «МЫ» (мы делаем, у нас был кейс, можем). НЕ "
        "называй себя ботом/AI/агентом и НЕ говори «свести вас с командой» — это "
        "запрещено, звучит как автоответчик. Напиши ОДНО следующее сообщение лиду: "
        "тёплое, СТРОГО на «Вы» (уважительно, формально), 2-4 предложения, живым "
        "человеческим языком (не шаблон). НЕ пиши «привет»/«приветик» — здоровайся "
        "ТОЛЬКО «Здравствуйте» или «Добрый день».\n"
        f"{intro_rule}"
        "❗КРИТИЧНО ПРО КОНТЕКСТ: опирайся ТОЛЬКО на то, что ЛИД РЕАЛЬНО написал в "
        "переписке ниже. НИКОГДА не приписывай лиду проект/нишу/задачу, которую он "
        "САМ НЕ называл. Если он написал лишь общее («можно подробнее?») — НЕ "
        "придумывай «онлайн-курсы»/«магазин»/«платформу» и т.п., а мягко уточни, что "
        "именно он хочет. Факты о студии ниже — это про НАС, НЕ выдавай их за запрос лида.\n"
        "Логика (цель — сначала собрать БРИФ, потом созвон): пойми задачу проекта — "
        "если неясна, задай 1 уточняющий вопрос (что за проект → цель для бизнеса → "
        "объём/интеграции → сроки); когда понятен тип — можешь назвать стартовую цену "
        "«от $X» и предложить короткий созвон со специалистом за точным расчётом; "
        "если лид сам просит звонок — согласись и уточни удобное время. Точную сумму "
        "НЕ называй, фактов не выдумывай. Если уместно — сошлись на наш релевантный "
        "кейс из фактов ниже (коротко, без хвастовства). Если лид ПРЯМО спросит «ты "
        "бот?» — честно: «да, я ассистент Deadline, собираю бриф, чтобы специалист "
        "точнее понял запрос; можем организовать звонок с человеком».\n"
        "Стартовые цены: лендинг от $300, интернет-магазин от $700, Telegram-бот "
        "от $300, Telegram Mini App от $500, AI-бот от $300.\n"
        "⚠️ ЯЗЫК — СТРОГО: отвечай на языке ПОСЛЕДНЕГО сообщения ЛИДА (не оператора, "
        "не предыдущей переписки). Грузинский → по-грузински (ქართულად), английский → "
        "по-английски, русский → по-русски, казахский → по-казахски, турецкий → "
        "по-турецки — НА ЛЮБОМ. Если лид пишет НЕ по-русски — НЕ переключайся на русский. "
        "Зеркаль именно язык лида.\n"
        f"{corr_block}"
        f"{kb_block}\n"
        f"Лид: {name}. Стадия: {stage}.\n"
        f"Переписка:\n{dialog or '(пусто)'}\n\n"
        "Ответь ТОЛЬКО готовым текстом сообщения лиду — на ЯЗЫКЕ ЛИДА, без кавычек, "
        "без пояснений и без своих размышлений."
    )


def make_payload(
    conv: Any,
    text: str,
    last_user: str,
    source: str,
    based_on_count: int,
    phone_number_id: str = "",
) -> dict:
    """Единый формат pending_wa_draft со штампом свежести."""
    return {
        "text": text,
        "phone_number_id": phone_number_id,
        "to_wa_id": conv.channel_conversation_id,
        "client_msg": (last_user or "")[:500],
        "source": source,
        "based_on_count": based_on_count,
        "ts": datetime.now(timezone.utc).isoformat(),
    }


async def generate_for_conv(
    db: Session,
    conv: Any,
    cust: Any,
    llm: Any,
    source: str,
) -> Optional[dict]:
    """Сгенерировать черновик ответа для диалога и записать в conv.pending_wa_draft.
    Возвращает payload или None (LLM вернул пусто). НЕ коммитит — это делает
    вызывающий (у фоновой задачи и запроса разные транзакции)."""
    dialog, last_user = _build_dialog(db, conv)
    name = (getattr(cust, "name", None) or "клиент")
    stage = conv.lead_stage or "new_lead"
    import asyncio as _aio
    kb = await _aio.to_thread(_kb_context, last_user or dialog)
    corr = await _aio.to_thread(_corrections_context, last_user or dialog, getattr(conv, "channel", None))
    result = await llm.ainvoke(_prompt(name, stage, dialog, kb, _active_offer(),
                                       corrections=corr, silent=_is_silent(dialog)))
    text = _clean_draft((getattr(result, "content", None) or ""))
    if not text:
        return None
    payload = make_payload(
        conv, text, last_user, source,
        based_on_count=count_dialog_messages(db, conv.id),
    )
    conv.pending_wa_draft = payload
    return payload


async def generate_reply_text(db: Session, conv: Any, cust: Any, llm: Any) -> Optional[str]:
    """Сгенерировать ТЕКСТ следующего ответа лиду тем же «хорошим» движком, что
    и предлагаемые черновики (собрать задачу → «от $X» → созвон). НЕ трогает
    pending_wa_draft и БД — просто возвращает чистый текст (или None). Нужен,
    чтобы автопилот (бот пишет сам) отвечал так же качественно, как черновики."""
    dialog, _last = _build_dialog(db, conv)
    name = (getattr(cust, "name", None) or "клиент")
    stage = conv.lead_stage or "new_lead"
    import asyncio as _aio
    kb = await _aio.to_thread(_kb_context, _last or dialog)
    corr = await _aio.to_thread(_corrections_context, _last or dialog, getattr(conv, "channel", None))
    try:
        result = await llm.ainvoke(_prompt(name, stage, dialog, kb, _active_offer(),
                                           corrections=corr, silent=_is_silent(dialog)))
    except Exception:  # noqa: BLE001
        return None
    return _clean_draft(getattr(result, "content", None) or "") or None


async def refresh_if_stale(db: Session, conv: Any, cust: Any, llm: Any) -> bool:
    """Если черновик устарел — перегенерировать под последнюю переписку.
    Возвращает True, если обновили. НЕ коммитит."""
    if not is_stale(db, conv):
        return False
    payload = await generate_for_conv(db, conv, cust, llm, source="auto_refresh")
    return payload is not None
