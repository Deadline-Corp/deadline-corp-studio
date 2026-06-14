"""Импорт существующих WhatsApp-переписок из WAHA-стора в нашу БД (2026-06-14).

Зачем: вебхук `message.any` ловит только НОВЫЕ входящие (с момента
подключения). Чтобы бот увидел ВСЕ уже идущие диалоги номера («пусть все
переписки увидит, добавит» — просьба пользователя), нужно один раз вытянуть
историю из WAHA-стора и разложить её в те же таблицы, что и живой трафик:
Customer + ChannelIdentity + Conversation + Message (channel='whatsapp').

Ключевые принципы:
- РЕЖИМ НАБЛЮДЕНИЯ. Импорт НЕ запускает LLM-ответы и ничего не шлёт клиенту —
  только сохраняет историю + классифицирует лид/не-лид. Бот «видит», но молчит.
- Идемпотентность. Повторный запуск не плодит дубли: дедуп по waha message-id
  (хранится в messages.extra_meta.waha_id). Уже импортированные сообщения
  пропускаются.
- Реальные таймстампы. created_at сообщения = время из WhatsApp (а не now()),
  чтобы порядок и «последняя активность» были корректны.
- Переиспользуем identity/conversation-хелперы — та же логика склейки, что и в
  _handle_message, никакого параллельного пути.

Точка входа: sync_waha_history(db, settings, llm, ...). Возвращает статистику.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import Conversation, Customer, Message
from channels.waha import (
    fetch_waha_chats,
    fetch_waha_chat_messages,
    fetch_waha_session_status,
    normalize_waha_history_message,
    _digits,
    _is_group,
    WahaHistoryUnavailable,
)
from services.identity import resolve_or_create_customer
from services.conversations import get_or_create_conversation
from services.lead_classifier import classify_whatsapp_conversation

log = logging.getLogger(__name__)


def _existing_waha_ids(db: Session, conversation_id) -> set[str]:
    """Все waha_id, уже сохранённые в этом диалоге — для дедупа."""
    rows = db.execute(
        select(Message.extra_meta).where(Message.conversation_id == conversation_id)
    ).scalars().all()
    out: set[str] = set()
    for meta in rows:
        if isinstance(meta, dict):
            wid = meta.get("waha_id")
            if wid:
                out.add(str(wid))
    return out


def _ts_to_dt(ts: int) -> Optional[datetime]:
    if not ts:
        return None
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None


def _build_transcript(items: list[dict]) -> str:
    """Компактный транскрипт для классификатора (последние ~30 реплик)."""
    lines = []
    for it in items[-30:]:
        who = "Я" if it.get("from_me") else "Клиент"
        lines.append(f"{who}: {it.get('content', '')}")
    return "\n".join(lines)


async def sync_waha_history(
    db: Session,
    settings: Any,
    llm: Any = None,
    *,
    max_chats: int = 300,
    per_chat_messages: int = 80,
    classify: bool = True,
    reconcile: bool = False,
) -> dict:
    """Вытянуть чаты + сообщения из WAHA и импортировать в БД.

    reconcile=True — режим СВЕРКИ: WAHA = источник правды. После импорта чистим
    «фантомные» локальные сообщения бота (role assistant/system без waha_id и без
    approved_via — несработавшие черновики, которых в WhatsApp нет), и сверяем, что
    число WhatsApp-сообщений в БД совпадает с WAHA. Так панель = зеркало WhatsApp.

    Возвращает dict со статистикой:
      {ok, chats_seen, chats_imported, groups_skipped, messages_imported,
       leads, non_leads, errors, session_status, phantoms_removed,
       chats_matched, chats_mismatched, mismatches[], reason?}
    """
    base = getattr(settings, "waha_base_url", None)
    key = getattr(settings, "waha_api_key", None) or ""
    session = getattr(settings, "waha_session", None) or "default"

    stats = {
        "ok": False, "chats_seen": 0, "chats_imported": 0, "groups_skipped": 0,
        "messages_imported": 0, "leads": 0, "non_leads": 0, "errors": 0,
        "session_status": None, "phantoms_removed": 0,
        "chats_matched": 0, "chats_mismatched": 0, "mismatches": [],
    }
    if not base:
        stats["reason"] = "WAHA не настроен (нет WAHA_BASE_URL)."
        return stats

    sess = await fetch_waha_session_status(base, key, session)
    stats["session_status"] = sess.get("status")
    if sess.get("status") and sess.get("status") != "WORKING":
        stats["reason"] = (
            f"Сессия WAHA в статусе {sess.get('status')} (нужно WORKING — "
            f"отсканируйте QR)."
        )
        return stats

    try:
        chats = await fetch_waha_chats(base, key, session, limit=max_chats)
    except WahaHistoryUnavailable as e:
        stats["reason"] = (
            "WAHA не отдаёт историю чатов — скорее всего не включён NOWEB-стор "
            f"(NOWEB_STORE_ENABLED). Детали: {e}"
        )
        return stats

    self_id = _digits(str((sess.get("me") or {}).get("id") or ""))
    stats["chats_seen"] = len(chats)

    for chat in chats:
        try:
            chat_id = str(chat.get("id") or chat.get("chatId") or "")
            if not chat_id or _is_group(chat_id):
                if _is_group(chat_id):
                    stats["groups_skipped"] += 1
                continue
            peer = _digits(chat_id)
            if not peer or peer == self_id:
                continue
            name = (chat.get("name") or chat.get("pushName") or "").strip() or None

            raw_msgs = await fetch_waha_chat_messages(
                base, key, session, chat_id, limit=per_chat_messages,
            )
            items = [
                m for m in (normalize_waha_history_message(x, self_id) for x in raw_msgs)
                if m
            ]
            items.sort(key=lambda x: x.get("ts") or 0)
            if not items:
                continue

            # --- identity + conversation (та же логика, что у живого трафика) ---
            customer = resolve_or_create_customer(
                db, channel="whatsapp", external_id=peer, username=name,
            )
            if name and not (customer.name or "").strip():
                customer.name = name[:200]
                db.flush()
            conversation = get_or_create_conversation(
                db, customer_id=customer.id, channel="whatsapp",
                channel_conversation_id=peer,
            )

            # --- импорт сообщений с дедупом по waha_id ---
            seen = _existing_waha_ids(db, conversation.id)
            last_ts_dt = None
            imported_here = 0
            for it in items:
                wid = it.get("waha_id")
                if wid and wid in seen:
                    continue
                created = _ts_to_dt(it.get("ts")) or datetime.now(timezone.utc)
                msg = Message(
                    conversation_id=conversation.id,
                    role=it["role"],
                    content=it["content"],
                    extra_meta={
                        "waha_id": wid, "source": "waha_history_sync",
                        "wa_type": it.get("type"),
                    },
                    created_at=created,
                )
                db.add(msg)
                if wid:
                    seen.add(wid)
                imported_here += 1
                if last_ts_dt is None or created > last_ts_dt:
                    last_ts_dt = created

            if imported_here == 0:
                # уже всё импортировано ранее — но переклассифицируем при classify
                pass
            else:
                stats["messages_imported"] += imported_here
                if last_ts_dt is not None:
                    conversation.last_message_at = last_ts_dt
                stats["chats_imported"] += 1
                db.flush()

            # --- СВЕРКА (reconcile): WAHA = источник правды ---
            if reconcile:
                # 1) убрать фантомные локальные сообщения бота, которых нет в WhatsApp
                local_msgs = db.execute(
                    select(Message).where(
                        Message.conversation_id == conversation.id,
                        Message.role.in_(["assistant", "system"]),
                    )
                ).scalars().all()
                for msg in local_msgs:
                    meta = msg.extra_meta or {}
                    if not meta.get("waha_id") and not meta.get("approved_via"):
                        db.delete(msg)
                        stats["phantoms_removed"] += 1
                db.flush()
                # 2) сверить: число WhatsApp-сообщений (с waha_id) в БД == число в WAHA
                db_wa = len(_existing_waha_ids(db, conversation.id))
                waha_n = len([1 for it in items if it.get("waha_id")])
                if db_wa == waha_n:
                    stats["chats_matched"] += 1
                else:
                    stats["chats_mismatched"] += 1
                    stats["mismatches"].append({
                        "peer": peer, "name": name, "db": db_wa, "waha": waha_n,
                    })

            # --- классификация лид/не-лид ---
            if classify:
                wa_labels = chat.get("labels") or []
                transcript = _build_transcript(items)
                # llm.invoke блокирующий — уводим в поток, чтобы не вешать loop
                result = await asyncio.to_thread(
                    classify_whatsapp_conversation,
                    llm=llm,
                    transcript=transcript,
                    contact_name=name or peer,
                    wa_labels=wa_labels,
                )
                result["classified_at"] = datetime.now(timezone.utc).isoformat()
                conversation.wa_classification = result
                if result.get("is_lead"):
                    stats["leads"] += 1
                    # не понижаем уже прогретых; поднимаем холодных до оценки
                    temp = result.get("temperature") or "cold"
                    order = {"frozen": 0, "cold": 1, "warm": 2, "hot": 3}
                    if order.get(temp, 1) > order.get(customer.lead_temperature or "cold", 1):
                        customer.lead_temperature = temp
                else:
                    stats["non_leads"] += 1
                db.flush()

            db.commit()  # per-chat: частичный прогресс сохраняется
        except Exception as e:  # noqa: BLE001
            db.rollback()
            stats["errors"] += 1
            log.error(f"waha sync chat error ({chat.get('id')}): {e}")
            continue

    stats["ok"] = True
    log.info(
        "[waha-sync] done: %s chats seen, %s imported, %s msgs, %s leads / %s non-leads, %s errors",
        stats["chats_seen"], stats["chats_imported"], stats["messages_imported"],
        stats["leads"], stats["non_leads"], stats["errors"],
    )
    return stats
