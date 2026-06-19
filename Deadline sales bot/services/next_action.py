"""Умная СЛЕДУЮЩАЯ задача по лиду (CRM next-action).

Мозг (LLM) читает диалог + стадию и решает ОДИН следующий шаг:
  - reengage — лид замолчал/пропал (особенно после КП) → мягко дожать, узнать статус;
  - answer   — есть что ответить/уточнить, бот может сам;
  - human    — нужен ЧЕЛОВЕК (позвонить, выставить КП, переговоры, решение);
  - wait     — мяч у лида, недавно обещал ответить — ждём;
  - unclear  — непонятно что делать → пас администратору (он поможет/поставит сам).

Результат кладётся в conversation.next_action {mode,label,draft,reason,kind,ts}.
Режим показа в задачнике: bot_auto / needs_approval / human / reengage / wait / unclear.
Bounded; вызывать в СВОЕЙ короткой сессии (чтобы не держать пул при LLM)."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy.orm import Session

from db.models import Conversation, Customer, Message
from services.conversation_brain import _parse_json

log = logging.getLogger(__name__)


def _last_role(db: Session, conv: Conversation) -> Optional[str]:
    m = (
        db.query(Message)
        .filter(Message.conversation_id == conv.id,
                Message.role.in_(("user", "assistant", "operator")))
        .order_by(Message.created_at.desc())
        .first()
    )
    return m.role if m else None


def lead_is_silent(db: Session, conv: Conversation) -> bool:
    """Лид молчит = последняя реплика НЕ от лида (мы написали последними)."""
    return _last_role(db, conv) in ("assistant", "operator")


def _transcript(db: Session, conv: Conversation, limit: int = 14) -> str:
    rows = (
        db.query(Message)
        .filter(Message.conversation_id == conv.id,
                Message.role.in_(("user", "assistant", "operator")))
        .order_by(Message.created_at.desc())
        .limit(limit).all()
    )
    out = []
    for m in reversed(rows):
        who = "Лид" if m.role == "user" else "Мы"
        out.append(f"{who}: {(m.content or '')[:300]}")
    return "\n".join(out)


_KIND_DEFAULT_LABEL = {
    "reengage": "Дожать: лид замолчал — узнать статус",
    "answer": "Ответить лиду",
    "human": "Связаться лично",
    "wait": "Ждём ответа лида",
    "unclear": "Решить следующий шаг",
}


def maybe_create_stuck_task(db: Session, conv: Conversation,
                            cust: Optional[Customer], reason: str) -> bool:
    """Бот не справляется с лидом (непонятно / не распознал голос / нет прогресса) →
    ставим задачу ЧЕЛОВЕКУ помочь. Бот+человек в связке: бот сам сколько может, но если
    затупил — зовёт человека, а не молчит/тупит. Дедуп: НЕ плодим вторую открытую
    operator-задачу по этому диалогу. db.flush() — коммитит вызывающий. True если создал."""
    from db.models import ScheduledAction as _SA
    existing = db.query(_SA.id).filter(
        _SA.conversation_id == conv.id,
        _SA.action_type == "operator_callback",
        _SA.status.in_(("pending", "processing")),
    ).first()
    if existing:
        return False  # уже есть открытая задача человеку по лиду — не дублируем
    db.add(_SA(
        customer_id=conv.customer_id,
        conversation_id=conv.id,
        channel=conv.channel,
        chat_id=conv.channel_conversation_id,
        action_type="operator_callback",
        executor="human",
        due_at=datetime.now(timezone.utc),
        status="pending",
        payload={"text": f"🤖 Бот не справляется — помоги с лидом: {(reason or '')[:200]}",
                 "title": "Бот затупил — нужна помощь", "by": "bot-stuck"},
    ))
    db.flush()
    return True


async def generate_next_action(db: Session, conv: Conversation, cust: Customer,
                               llm: Any, hint: Optional[str] = None) -> dict:
    """Сгенерировать следующий шаг по лиду и записать в conv.next_action.

    hint — подсказка менеджера («объясни боту, что делать»): если бот завис/не понял,
    человек пишет пояснение, бот ПЕРЕразбирает лид с учётом подсказки (приоритетно)."""
    # Не трогаем АВТО-логикой денежные/юр/рабочие стадии — там решает человек (инвариант).
    # Иначе на платящем клиенте мог появиться черновик бота / задача «бот затупил» (находка ревью).
    if (conv.lead_stage or "") in ("nda", "tz_approved", "prepayment", "in_work"):
        return {}
    transcript = _transcript(db, conv)
    if not transcript.strip():
        return {}
    silent = lead_is_silent(db, conv)
    stage = conv.lead_stage or "new_lead"
    prompt = (
        "Ты — руководитель отдела продаж веб-студии. По переписке реши ОДИН "
        "следующий шаг по лиду и верни СТРОГО JSON (без пояснений):\n"
        '  "kind": "reengage"|"answer"|"human"|"wait"|"unclear";\n'
        "    reengage = лид замолчал/пропал (особенно после КП/предложения) — нужно "
        "мягко дожать, узнать статус, вернуть в диалог;\n"
        "    answer = есть на что ответить или что уточнить — бот может сам;\n"
        "    human = нужен ЧЕЛОВЕК: позвонить, выставить КП/счёт, переговоры, решение;\n"
        "    wait = мяч на стороне лида, он СОВСЕМ НЕДАВНО обещал ответить/подумать — ждём;\n"
        "    unclear = непонятно, что делать дальше — нужен администратор.\n"
        '  "draft": если kind=reengage или answer — ОДНО короткое сообщение лиду '
        "(тёплое, на «вы», по делу, 1-2 предложения, как живой человек, без шаблона), "
        'иначе "";\n'
        '  "label": очень коротко ЧТО сделать (до 60 символов, по-русски);\n'
        '  "reason": кратко почему именно это (до 100 символов).\n\n'
        f"Стадия воронки лида: {stage}. "
        f"Лид сейчас {'МОЛЧИТ — мы написали последними, он не ответил' if silent else 'ответил последним'}.\n"
        f"Переписка:\n{transcript}"
    )
    if hint and hint.strip():
        prompt += (
            "\n\nВАЖНО — менеджер ОБЪЯСНИЛ, что делать с этим лидом. Учти это в ПЕРВУЮ "
            "очередь при выборе шага и тексте сообщения (если просит написать лиду — "
            f"kind=answer/reengage с draft):\n«{hint.strip()[:500]}»"
        )
    try:
        result = await llm.ainvoke(prompt)
        data = _parse_json(getattr(result, "content", None) or "")
    except Exception as e:  # noqa: BLE001
        log.warning(f"[{str(conv.id)[:8]}] next_action LLM failed: {e}")
        return {}
    if not data:
        return {}

    kind = str(data.get("kind") or "unclear").lower().strip()
    if kind not in _KIND_DEFAULT_LABEL:
        kind = "unclear"
    draft = (data.get("draft") or "").strip()

    # Режим показа в задачнике.
    if kind == "human":
        mode = "human"
    elif kind == "unclear":
        mode = "unclear"
    elif kind == "wait":
        mode = "wait"
    else:  # reengage | answer — бот умеет; авто если разрешён автопилот диалога, иначе на одобрение
        mode = "bot_auto" if bool(getattr(conv, "wa_autonomous", False)) else "needs_approval"

    na = {
        "mode": mode,
        "kind": kind,
        "label": (data.get("label") or "").strip()[:120] or _KIND_DEFAULT_LABEL[kind],
        "draft": draft[:1500],
        "reason": (data.get("reason") or "").strip()[:200],
        "silent": silent,
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    if hint and hint.strip():
        na["hint"] = hint.strip()[:500]  # что объяснил менеджер — для прослеживаемости
    conv.next_action = na
    # Для «дожима»/«ответа» на одобрение — кладём черновик в pending_wa_draft, чтобы
    # он всплыл в карточке с кнопкой «✅ Отправить» (единый механизм одобрения, #5).
    if mode == "needs_approval" and draft:
        conv.pending_wa_draft = {
            "text": draft[:1500],
            "source": "next_action",
            "ts": na["ts"],
        }
    # Бот не понял что делать → задача человеку помочь (бот+человек в связке).
    if mode == "unclear":
        try:
            maybe_create_stuck_task(db, conv, cust,
                                    na.get("reason") or na.get("label") or "не понял следующий шаг")
        except Exception:  # noqa: BLE001
            pass
    db.commit()
    return na
