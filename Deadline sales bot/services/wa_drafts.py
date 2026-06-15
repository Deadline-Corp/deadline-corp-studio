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


def _prompt(name: str, stage: str, dialog: str) -> str:
    return (
        "Ты — менеджер веб-студии Deadline (сайты, автоматизация, AI-боты). "
        "ГЛАВНОЕ: сначала ВЫЯСНИ задачу проекта, не торопись на созвон. По переписке "
        "напиши ЛУЧШИЙ следующий ответ лиду по такому порядку:\n"
        "1) Если задача ещё не ясна — задай 1 конкретный вопрос, чтобы понять проект "
        "(что нужно сделать, цель, что есть сейчас, сроки). Собирай детали по одному.\n"
        "2) Когда задача в целом понятна — кратко подытожь, что понял, и ТОЛЬКО ТОГДА "
        "предложи короткий созвон, чтобы обсудить оценку и детали.\n"
        "3) Если лид САМ просит созвон/звонок/«свяжитесь» — согласись, уточни удобное "
        "время и скажи, что менеджер свяжется (созвон поставит система).\n"
        "Тон тёплый, человеческий, на «вы», коротко (2-4 предложения), без «здравствуйте» "
        "если диалог уже шёл, без выдуманных цен/фактов и без обещаний точной суммы. "
        "Только текст сообщения лиду.\n\n"
        f"Имя лида: {name}\nСтадия: {stage}\nПереписка:\n{dialog or '(пусто)'}"
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
    result = await llm.ainvoke(_prompt(name, stage, dialog))
    text = (getattr(result, "content", None) or "").strip()
    if not text:
        return None
    payload = make_payload(
        conv, text, last_user, source,
        based_on_count=count_dialog_messages(db, conv.id),
    )
    conv.pending_wa_draft = payload
    return payload


async def refresh_if_stale(db: Session, conv: Any, cust: Any, llm: Any) -> bool:
    """Если черновик устарел — перегенерировать под последнюю переписку.
    Возвращает True, если обновили. НЕ коммитит."""
    if not is_stale(db, conv):
        return False
    payload = await generate_for_conv(db, conv, cust, llm, source="auto_refresh")
    return payload is not None
