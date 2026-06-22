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
import re
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select, func as _sqlfunc, update as _sqlupdate
from sqlalchemy.orm import Session

from db.models import Conversation, ConversationStatusEnum, Customer, Message
from channels.waha import (
    fetch_waha_chats,
    fetch_waha_chat_messages,
    fetch_waha_session_status,
    normalize_waha_history_message,
    chat_id_from_waha_id,
    _digits,
    _is_group,
    WahaHistoryUnavailable,
)
from services.identity import resolve_or_create_customer
from services.conversations import get_or_create_conversation
from services.lead_classifier import classify_whatsapp_conversation

log = logging.getLogger(__name__)


def cleanup_wa_artifacts() -> dict:
    """Дешёвая (только БД, без WAHA/LLM) авто-чистка для постоянной актуальности
    панели = WhatsApp. Безопасно гонять часто (в кроне):

    1. ФАНТОМЫ — assistant в whatsapp без признаков доставки (waha_id/approved_via/
       delivered): бот сгенерил, но в WhatsApp их не было.
    2. ЭХО-ДУБЛИ — operator-сообщение (наш же исходящий, отражённый WAHA как fromMe:
       source=manual_wa / approved_via=manual_phone), чей текст совпадает с
       assistant-сообщением в том же диалоге → дубль, удаляем эхо (assistant-копию
       оставляем — это запись нашей отправки). Настоящие ручные ответы с телефона
       (без совпадающего assistant) НЕ трогаем.
    """
    from db.connection import session_scope
    from datetime import timedelta as _td
    out = {"phantoms": 0, "echo_dupes": 0, "sfx_dupes": 0}
    _floor = datetime.min.replace(tzinfo=timezone.utc)
    # СВЕЖИЕ авто-ответы (<15 мин) НЕ трогаем: бот отправил, но fromMe-эхо (придёт с
    # waha_id) ещё не дошло — преждевременное удаление даёт мигание «пропало-вернулось»
    # в панели, а при редком «эхо не пришло вовсе» — потерю реально отправленного.
    # Старше 15 мин без признаков доставки = настоящий фантом (бот сгенерил, но не послал).
    _phantom_cutoff = datetime.now(timezone.utc) - _td(minutes=15)
    with session_scope() as db:
        # 1) фантомы
        rows = (
            db.query(Message).join(Conversation, Message.conversation_id == Conversation.id)
            .filter(Conversation.channel == "whatsapp", Message.role == "assistant").all()
        )
        for m in rows:
            meta = m.extra_meta or {}
            if meta.get("waha_id") or meta.get("approved_via") or meta.get("delivered"):
                continue
            _ts = m.created_at or _floor
            if _ts.tzinfo is None:
                _ts = _ts.replace(tzinfo=timezone.utc)
            if _ts > _phantom_cutoff:
                continue  # свежий — ждём его эхо
            db.delete(m)
            out["phantoms"] += 1
        db.flush()
        # 2) ЭХО-ДУБЛИ: одно сообщение хранится ДВАЖДЫ — assistant (наша запись отправки,
        #    approved_via/delivered) И operator (то же самое, затянутое обратно из WhatsApp
        #    как fromMe-эхо, source=waha_history_sync/webhook). Кейс T-Group. Оставляем ОДНУ
        #    копию: предпочитаем ту, у которой есть waha_id (реальная запись WhatsApp) — иначе
        #    history-sync переимпортирует её снова (бесконечный цикл). Удаляем дубль без waha_id.
        wa_msgs = (
            db.query(Message).join(Conversation, Message.conversation_id == Conversation.id)
            .filter(Conversation.channel == "whatsapp",
                    Message.role.in_(["assistant", "operator"])).all()
        )
        def _rs(m):
            return m.role.value if hasattr(m.role, "value") else str(m.role)
        by_key: dict = {}
        for m in wa_msgs:
            content = re.sub(r"\s+", " ", (m.content or "")).strip().lower()
            if content:
                by_key.setdefault((m.conversation_id, content), []).append(m)
        for (cid, content), group in by_key.items():
            if len(group) < 2:
                continue
            # Дубль нашей же отправки: смешанная пара assistant+operator (эхо, любой длины)
            # ЛИБО все «наши» с содержательным текстом (≥15 симв — длинное сообщение мы
            # дважды не шлём; короткие «Хорошо»/«Да» могут законно повторяться → не трогаем).
            roles = {_rs(m) for m in group}
            mixed_echo = {"assistant", "operator"} <= roles
            our_side = roles <= {"assistant", "operator"}
            if not (mixed_echo or (our_side and len(content) >= 15)):
                continue
            group.sort(key=lambda m: (
                0 if (m.extra_meta or {}).get("waha_id") else 1,
                m.created_at or _floor,
            ))
            for dup in group[1:]:  # оставляем первый (с waha_id), остальные — дубли
                db.delete(dup)
                out["echo_dupes"] += 1
        db.flush()
        # 3) ДЕДУП по СУФФИКСУ message-id (любые роли): одно и то же сообщение WhatsApp,
        #    сохранённое дважды — operator+operator (ручное+история), user+user (@lid/@c.us,
        #    вебхук+история) и т.п. Суффикс message-id глобально уникален → одинаковый
        #    суффикс = ОДНО сообщение. Эхо-дедуп выше берёт лишь assistant+operator по тексту;
        #    здесь — всё остальное. Оставляем самую раннюю запись.
        sfx_rows = (
            db.query(Message).join(Conversation, Message.conversation_id == Conversation.id)
            .filter(Conversation.channel == "whatsapp").all()
        )
        by_sfx: dict = {}
        for m in sfx_rows:
            meta = m.extra_meta or {}
            sfx = _wa_msgid_suffix(meta.get("waha_id"))
            if sfx:
                by_sfx.setdefault((m.conversation_id, sfx), []).append(m)
        for grp in by_sfx.values():
            if len(grp) < 2:
                continue
            grp.sort(key=lambda m: (m.created_at or _floor))
            for dup in grp[1:]:
                db.delete(dup)
                out["sfx_dupes"] += 1
    return out


def _msg_id_key(meta: Any) -> Optional[str]:
    """Канонический id входящего сообщения — для КАНАЛ-НЕЗАВИСИМОГО дедупа. У каждого
    канала свой нативный message-id; берём его (WhatsApp — по суффиксу, чтобы @lid/@c.us
    копии одного сообщения схлопнулись)."""
    if not isinstance(meta, dict):
        return None
    w = meta.get("waha_id")
    if w:
        return "wa:" + (_wa_msgid_suffix(w) or str(w))
    for k in ("telegram_msg_id", "wamid", "mid", "greenapi_msg_id", "comment_id"):
        v = meta.get(k)
        if v:
            return f"{k}:{v}"
    return None


def dedup_messages_global(db: Optional[Session] = None) -> dict:
    """КАНАЛ-НЕЗАВИСИМЫЙ дедуп: одно и то же сообщение, сохранённое дважды (ретрай
    вебхука, история+живой приём, @lid/@c.us-копии), по нативному message-id. Покрывает
    ВСЕ каналы (WhatsApp/Telegram/Instagram/Messenger) — под видение «одно окно»: новый
    канал НЕ принесёт дубль-хаос. Чисто БД, безопасно в кроне. Оставляем самую раннюю."""
    from db.connection import session_scope
    out = {"removed": 0}
    _floor = datetime.min.replace(tzinfo=timezone.utc)

    def _run(s: Session) -> dict:
        rows = s.execute(select(Message)).scalars().all()
        groups: dict = {}
        for m in rows:
            key = _msg_id_key(m.extra_meta)
            if key:
                groups.setdefault((m.conversation_id, key), []).append(m)
        for grp in groups.values():
            if len(grp) < 2:
                continue
            grp.sort(key=lambda m: (m.created_at or _floor))
            for dup in grp[1:]:
                s.delete(dup)
                out["removed"] += 1
        s.flush()
        return out

    if db is not None:
        return _run(db)
    with session_scope() as s:
        return _run(s)


# Порядок стадий воронки — для слияния «только вперёд» (не откатываем стадию).
_STAGE_ORDER = [
    "new_lead", "in_dialog", "qualified", "nda", "on_call", "tz_approved",
    "proposal", "prepayment", "in_work", "completed_won", "post_sale", "lost",
]


def _norm_phone(p: Optional[str]) -> str:
    return "".join(ch for ch in (p or "") if ch.isdigit())


def dedup_wa_by_phone(db: Optional[Session] = None) -> dict:
    """Дедуп WhatsApp-карточек по РЕАЛЬНОМУ телефону (чисто БД, без сети/LLM —
    безопасно гонять в кроне). Рекламные лиды приходят под скрытым `@lid` и
    заводят свою карточку; на входящем мы резолвим их телефон через WAHA LID API
    и штампуем `customer.phone` (main._handle_message). Здесь: если ДВЕ+ активные
    whatsapp-карточки имеют ОДИН и тот же штампованный телефон (@lid-карточка + та
    же, импортированная/синкнутая по номеру) — схлопываем в одну, чтобы панель не
    плодила дубли рекламных лидов.

    Канон = самая свежеактивная карточка (туда падают живые сообщения от лида);
    остальные → ARCHIVED (ОБРАТИМО, не удаляем — правило never-delete). Переносим
    на канон: стадию (только вперёд), pending_wa_draft и summary, если у канона
    пусто. Идемпотентно (архивные исключены из выборки)."""
    from db.connection import session_scope
    from db.models import ConversationStatusEnum

    def _run(_db: Session) -> dict:
        out: dict = {"groups": 0, "archived": 0, "pairs": []}
        rows = (
            _db.query(Conversation, Customer)
            .join(Customer, Conversation.customer_id == Customer.id)
            .filter(Conversation.channel == "whatsapp",
                    Conversation.status != ConversationStatusEnum.ARCHIVED)
            .all()
        )
        by_phone: dict[str, list] = {}
        for conv, cust in rows:
            phone = _norm_phone(getattr(cust, "phone", None))
            if len(phone) < 8:  # нет надёжного номера — не угадываем, пропускаем
                continue
            by_phone.setdefault(phone, []).append((conv, cust))

        _floor = datetime.min.replace(tzinfo=timezone.utc)
        for phone, group in by_phone.items():
            if len(group) < 2:
                continue
            out["groups"] += 1
            # канон = самая свежеактивная (живые сообщения приходят туда)
            group.sort(
                key=lambda gc: getattr(gc[0], "last_message_at", None) or _floor,
                reverse=True,
            )
            canon = group[0][0]
            canon_cust = group[0][1]
            from db.models import ScheduledAction as _SA
            for src, _scust in group[1:]:
                if src.id == canon.id:
                    continue
                # стадия только вперёд
                try:
                    si = _STAGE_ORDER.index(getattr(src, "lead_stage", None) or "new_lead")
                    ci = _STAGE_ORDER.index(getattr(canon, "lead_stage", None) or "new_lead")
                    if si > ci:
                        canon.lead_stage = src.lead_stage
                except ValueError:
                    pass
                if getattr(src, "pending_wa_draft", None) and not getattr(canon, "pending_wa_draft", None):
                    canon.pending_wa_draft = src.pending_wa_draft
                # Не теряем предложение созвона при слиянии — иначе кнопка «Создать
                # событие» на каноне покажет «нет предложения».
                if getattr(src, "pending_call_suggestion", None) and not getattr(canon, "pending_call_suggestion", None):
                    canon.pending_call_suggestion = src.pending_call_suggestion
                if (getattr(src, "summary", None) or "").strip() and not (getattr(canon, "summary", None) or "").strip():
                    canon.summary = (src.summary or "")[:2000]
                # Не теряем созвон: бронь+напоминания дубля → канон ДО архива (иначе
                # cancel_orphan_scheduled_actions погасит их у архивной карточки).
                _db.execute(
                    _sqlupdate(_SA)
                    .where(_SA.conversation_id == src.id,
                           _SA.status.in_(("pending", "processing")),
                           _SA.action_type.in_(("call_booked", "call_reminder")))
                    .values(conversation_id=canon.id, customer_id=canon_cust.id)
                )
                _sp = _scust.profile_data or {}
                if _sp.get("booked_call_at") and not (canon_cust.profile_data or {}).get("booked_call_at"):
                    _cp = dict(canon_cust.profile_data or {})
                    _cp["booked_call_at"] = _sp["booked_call_at"]
                    if _sp.get("call_medium"):
                        _cp["call_medium"] = _sp["call_medium"]
                    canon_cust.profile_data = _cp
                src.status = ConversationStatusEnum.ARCHIVED
                src.summary = ((src.summary or "") + f" → дубль слит в {canon.id} (по тел. {phone})").strip()[:2000]
                out["archived"] += 1
                out["pairs"].append({"phone": phone, "archived": str(src.id), "canon": str(canon.id)})
        _db.flush()
        return out

    if db is not None:
        return _run(db)
    with session_scope() as _db:
        return _run(_db)


def _is_lid_key(cid: Optional[str]) -> bool:
    """`@lid` скрытый id рекламного лида — длинная цифра 13+ знаков (НЕ телефон)."""
    return len(_norm_phone(cid)) >= 13


def _carry_and_archive(canon: Conversation, src: Conversation, reason: str) -> None:
    """Перенести на канон стадию(вперёд)/черновик/предложение-созвона/summary и
    заархивировать src (обратимо). Общая логика для дедупа по телефону и по имени."""
    try:
        si = _STAGE_ORDER.index(getattr(src, "lead_stage", None) or "new_lead")
        ci = _STAGE_ORDER.index(getattr(canon, "lead_stage", None) or "new_lead")
        if si > ci:
            canon.lead_stage = src.lead_stage
    except ValueError:
        pass
    if getattr(src, "pending_wa_draft", None) and not getattr(canon, "pending_wa_draft", None):
        canon.pending_wa_draft = src.pending_wa_draft
    if getattr(src, "pending_call_suggestion", None) and not getattr(canon, "pending_call_suggestion", None):
        canon.pending_call_suggestion = src.pending_call_suggestion
    if (getattr(src, "summary", None) or "").strip() and not (getattr(canon, "summary", None) or "").strip():
        canon.summary = (src.summary or "")[:2000]
    # Перенести АКТИВНУЮ бронь созвона + напоминания src → канон. Иначе после слияния
    # call_booked-строка висит на архивном src и ПРОПАДАЕТ с календаря (он читает
    # call_booked по conversation_id НЕархивных диалогов). booked_call_at в profile_data
    # переносит вызывающий дедуп; здесь синхронизируем строки. Best-effort.
    try:
        from sqlalchemy.orm import object_session as _osess
        from sqlalchemy import update as _upd
        from db.models import ScheduledAction as _SA
        _db = _osess(src)
        if _db is not None:
            _db.execute(
                _upd(_SA)
                .where(_SA.conversation_id == src.id,
                       _SA.action_type.in_(("call_booked", "call_reminder")),
                       _SA.status == "pending")
                .values(conversation_id=canon.id)
            )
    except Exception:  # noqa: BLE001 — перенос строк не валит дедуп
        pass
    src.status = ConversationStatusEnum.ARCHIVED
    src.summary = ((src.summary or "") + f" → дубль слит в {canon.id} ({reason})").strip()[:2000]


def dedup_wa_by_name(db: Optional[Session] = None) -> dict:
    """Дедуп «@lid-тени» по ИМЕНИ (чисто БД). Один контакт приходит И под реальным
    телефоном, И под скрытым `@lid` — имя WhatsApp идентично → ДВЕ карточки. Phone-
    дедуп их не ловит (у @lid телефон не разрезолвлен). Сливаем активные whatsapp-
    карточки с ОДИНАКОВЫМ именем, когда ключи РАЗНЫЕ и хотя бы один — `@lid` (именно
    паттерн «телефон + тень», а не два разных лида). Имя ≥4 симв. Обратимо (ARCHIVED)."""
    from db.connection import session_scope
    from db.models import ConversationStatusEnum

    def _run(_db: Session) -> dict:
        out: dict = {"groups": 0, "archived": 0, "pairs": []}
        rows = (
            _db.query(Conversation, Customer)
            .join(Customer, Conversation.customer_id == Customer.id)
            .filter(Conversation.channel == "whatsapp",
                    Conversation.status != ConversationStatusEnum.ARCHIVED)
            .all()
        )
        by_name: dict[str, list] = {}
        for conv, cust in rows:
            name = (getattr(cust, "name", None) or "").strip().lower()
            if len(name) < 4:  # короткие/пустые имена — не угадываем
                continue
            by_name.setdefault(name, []).append((conv, cust))

        _floor = datetime.min.replace(tzinfo=timezone.utc)
        for name, group in by_name.items():
            if len(group) < 2:
                continue
            keys = {_norm_phone(c.channel_conversation_id) for c, _ in group}
            has_lid = any(_is_lid_key(c.channel_conversation_id) for c, _ in group)
            if len(keys) < 2 or not has_lid:  # одинаковый ключ или нет @lid — не наш случай
                continue
            out["groups"] += 1
            group.sort(key=lambda gc: getattr(gc[0], "last_message_at", None) or _floor, reverse=True)
            canon = group[0][0]
            for src, _sc in group[1:]:
                if src.id == canon.id:
                    continue
                _carry_and_archive(canon, src, "по имени")
                out["archived"] += 1
                out["pairs"].append({"name": name, "archived": str(src.id), "canon": str(canon.id)})
        _db.flush()
        return out

    if db is not None:
        return _run(db)
    with session_scope() as _db:
        return _run(_db)


def dedup_empty_customer_stubs(db: Optional[Session] = None) -> dict:
    """Чистка ПУСТЫХ дублей-клиентов: customer с телефоном, у которого 0 conversations
    (merge_wa_split перенёс диалоги на канон → остался пустой stub) И есть ДРУГОЙ customer
    с ТЕМ ЖЕ телефоном, у которого диалоги ЕСТЬ. Помечаем stub archived_stub в profile_data
    (НЕ удаляем — правило never-delete). Идемпотентно (помеченные пропускаем). Эффективный
    подзапрос, без полного скана."""
    from db.connection import session_scope
    out = {"archived_stubs": 0}

    def _run(s: Session) -> dict:
        with_conv = select(Conversation.customer_id).distinct()
        stubs = s.execute(
            select(Customer).where(
                Customer.phone.isnot(None),
                ~Customer.id.in_(with_conv),
            )
        ).scalars().all()
        for st in stubs:
            pd = st.profile_data or {}
            if pd.get("archived_stub"):
                continue
            twin = s.execute(
                select(Customer.id).where(
                    Customer.phone == st.phone,
                    Customer.id != st.id,
                    Customer.id.in_(with_conv),
                ).limit(1)
            ).first()
            if not twin:
                continue  # одиночка без диалогов (напр. свежая форма) — не трогаем
            npd = dict(pd)
            npd["archived_stub"] = True
            npd["archived_stub_at"] = datetime.now(timezone.utc).isoformat()
            st.profile_data = npd
            out["archived_stubs"] += 1
        return out

    if db is not None:
        return _run(db)
    with session_scope() as s:
        return _run(s)


def dedup_scheduled_actions(db: Optional[Session] = None) -> dict:
    """Дедуп задач (чисто БД): один pending/processing action на (диалог, тип, текст).
    Бот/брейн/массовые прогоны плодили ОДИНАКОВЫЕ задачи («Лид завис — связаться лично»
    по 2-3 на лида) → засор задачника/календаря. Оставляем САМУЮ СВЕЖУЮ, остальные →
    'superseded'. call_reminder: 3 легитимных (1d/3h/1h) имеют РАЗНЫЙ due_at и переживают;
    гасим только ДУБЛИ одного слота (после слияния @lid+@c.us карточек одного человека)."""
    from db.connection import session_scope
    from db.models import ScheduledAction

    def _run(_db: Session) -> dict:
        rows = (
            _db.query(ScheduledAction)
            .filter(ScheduledAction.status.in_(("pending", "processing")),
                    ScheduledAction.conversation_id.isnot(None))
            .order_by(ScheduledAction.created_at.desc().nullslast())
            .all()
        )
        seen: set = set()
        n = 0
        # Поручения, привязанные к ЧЕЛОВЕКУ, а не к конкретному диалогу («связаться/
        # дожать»): дубль = повтор по КОНТАКТУ. У человека с N диалогами/карточками
        # была N-кратная одинаковая задача (Нонна ×3). Ключ — (customer, тип), без текста
        # и без диалога. Для остальных типов — как было (диалог, тип, текст).
        NON_CONV_TYPES = {"operator_callback", "warming_touch"}
        for a in rows:
            payload = a.payload or {}
            text = (payload.get("text") or payload.get("title") or "")[:100]
            if a.action_type == "call_reminder":
                # дубль = тот же слот созвона одному лиду одной аудитории (до минуты);
                # 1d/3h/1h имеют разный due_at → НЕ схлопнутся.
                due_min = a.due_at.replace(second=0, microsecond=0).isoformat() if a.due_at else ""
                key = (str(a.conversation_id), a.action_type, payload.get("audience") or "", due_min)
            elif a.action_type in NON_CONV_TYPES:
                # +источник (payload.by): winback / stuck / automation / warming / ручная
                # задача оператора — разный смысл, нельзя схлопывать в одну. Раньше ключ был
                # (customer, type) → осмысленная задача оператора молча исчезала под winback.
                key = (str(a.customer_id), a.action_type, (payload.get("by") or ""))
            else:
                key = (str(a.conversation_id), a.action_type, text)
            if key in seen:
                a.status = "superseded"  # дубль (старее свежей) → гасим
                n += 1
            else:
                seen.add(key)
        _db.flush()
        return {"superseded": n}

    if db is not None:
        return _run(db)
    with session_scope() as _db:
        return _run(_db)


def _wa_msgid_suffix(wid: Any) -> Optional[str]:
    """Истинный WhatsApp message-id из waha_id. У @lid и @c.us РАЗНЫЙ префикс
    (`false_<lid>@lid_ABC` vs `false_<phone>@c.us_ABC`), но СУФФИКС (сам msg-id
    `ABC`) один → дедуп пересечений @lid/@c.us-копий одного сообщения."""
    if not wid:
        return None
    s = str(wid)
    return s.rsplit("_", 1)[-1] if "_" in s else s


def _canon_real_phone(conv: Conversation, cust: Customer) -> str:
    """Реальный телефон карточки для группировки слияния: сперва штампованный
    customer.phone (разрезолвленный из @lid), иначе channel_conversation_id, ЕСЛИ
    он сам — реальный номер (@c.us, 8-12 цифр), а не скрытый @lid (≥13)."""
    p = _norm_phone(getattr(cust, "phone", None))
    if 9 <= len(p) <= 13 and not _is_lid_key(p):
        return p
    cid = _norm_phone(getattr(conv, "channel_conversation_id", None))
    if 8 <= len(cid) <= 12:  # @c.us-стиль реальный номер (lid длиннее)
        return cid
    return ""


def merge_wa_split(db: Optional[Session] = None, *, execute: bool = True) -> dict:
    """СТРУКТУРНОЕ слияние РАЗОРВАННЫХ WhatsApp-карточек одного человека (чисто БД).

    Корень рассинхрона (кейс Zaal): рекламный лид приходит И под скрытым `@lid`
    (живой вебхук), И под реальным телефоном `@c.us` (history-sync) → ДВА customer
    + ДВЕ conversation с РАЗБИТОЙ и частично ДУБЛИРОВАННОЙ историей. `resolve_lid_phone`
    знает реальный номер, но раньше он только косметически штамповался. Здесь номер =
    КАНОН, и склейка идёт ПО ДЕЛУ:
      • группируем whatsapp-карточки (вкл. архивные) по реальному телефону;
      • канон = самая свежеактивная; ПЕРЕНОСИМ в него все сообщения остальных
        (дедуп по WA-msg-id — и полному, и суффиксу), фрагмент → ARCHIVED (обратимо,
        НЕ удаляем — правило never-delete; дубль-сообщения остаются на архивной тени);
      • перенацеливаем идентичности (@lid + телефон) и сам фрагмент на customer
        канона → впредь ЛЮБОЙ идентификатор резолвится в ОДНУ карточку;
      • канону ставим channel_conversation_id = телефон (phone-canonical) → живой
        вебхук (после ingestion-фикса) попадает В НЕГО, без новых фрагментов;
      • осиротевшие задачи фрагмента → superseded.

    execute=False — СУХОЙ ПРОГОН: только считает, что слилось бы, ничего не меняет.
    Идемпотентно: полностью слитые фрагменты (архив + тот же customer + нечего
    переносить) пропускаются."""
    from db.connection import session_scope
    from db.models import ConversationStatusEnum, ChannelIdentity, ScheduledAction

    _ARCH = ConversationStatusEnum.ARCHIVED.value

    def _wa_ids(_db: Session, conv_id) -> tuple[set, set]:
        full: set = set()
        suf: set = set()
        for meta in _db.execute(
            select(Message.extra_meta).where(Message.conversation_id == conv_id)
        ).scalars().all():
            if isinstance(meta, dict):
                w = meta.get("waha_id")
                if w:
                    full.add(str(w))
                    s = _wa_msgid_suffix(w)
                    if s:
                        suf.add(s)
        return full, suf

    def _run(_db: Session) -> dict:
        out: dict = {
            "groups": 0, "merged_convs": 0, "moved_msgs": 0, "skipped_dupes": 0,
            "archived": 0, "relabeled": 0, "pairs": [], "dry": not execute,
        }
        rows = (
            _db.query(Conversation, Customer)
            .join(Customer, Conversation.customer_id == Customer.id)
            .filter(Conversation.channel == "whatsapp")
            .all()
        )
        by_phone: dict[str, list] = {}
        for conv, cust in rows:
            ph = _canon_real_phone(conv, cust)
            if len(ph) < 8:
                continue
            by_phone.setdefault(ph, []).append((conv, cust))

        _floor = datetime.min.replace(tzinfo=timezone.utc)
        for phone, group in by_phone.items():
            if len(group) < 2:
                # Латентный край: ОДИНОЧНАЯ активная @lid-карточка, у которой телефон
                # уже разрезолвлен (но пары-дубля ещё нет). Следующее сообщение под
                # @lid с известным телефоном маршрутизируется на phone-ключ → форкнет
                # новую карточку. Перекеиваем заранее в phone-canonical + вешаем
                # (whatsapp, телефон) идентичность. Безопасно: раз группа из одного —
                # на этот телефон больше никакая активная карточка не завязана.
                conv0, cust0 = group[0]
                cid0 = _norm_phone(getattr(conv0, "channel_conversation_id", None))
                if cid0 != phone and _is_lid_key(cid0) and conv0.status != _ARCH:
                    out["relabeled"] += 1
                    if execute:
                        conv0.channel_conversation_id = phone
                        if not _norm_phone(getattr(cust0, "phone", None)):
                            cust0.phone = ("+" + phone)[:50]
                        ex0 = _db.execute(
                            select(ChannelIdentity).where(
                                ChannelIdentity.channel == "whatsapp",
                                ChannelIdentity.external_id == phone,
                            )
                        ).scalar_one_or_none()
                        if ex0 is None:
                            _db.add(ChannelIdentity(customer_id=cust0.id, channel="whatsapp", external_id=phone))
                        elif ex0.customer_id != cust0.id:
                            ex0.customer_id = cust0.id
                continue
            # канон = самая свежеактивная НЕархивная (иначе самая свежая вообще)
            active = [g for g in group if g[0].status != _ARCH]
            pool = active or group
            pool.sort(key=lambda gc: getattr(gc[0], "last_message_at", None) or _floor, reverse=True)
            canon, canon_cust = pool[0]
            frags = [g for g in group if g[0].id != canon.id]
            if not frags:
                continue

            full, suf = _wa_ids(_db, canon.id)
            did_something = False
            for fconv, fcust in frags:
                msgs = _db.execute(
                    select(Message).where(Message.conversation_id == fconv.id)
                ).scalars().all()
                movable = []
                for m in msgs:
                    meta = m.extra_meta or {}
                    w = meta.get("waha_id")
                    s = _wa_msgid_suffix(w)
                    if w and (str(w) in full or (s and s in suf)):
                        out["skipped_dupes"] += 1
                        continue
                    movable.append(m)
                already_merged = (
                    fconv.status == _ARCH
                    and fcust.id == canon_cust.id
                    and not movable
                )
                if already_merged:
                    continue
                did_something = True
                out["merged_convs"] += 1
                out["pairs"].append({
                    "phone": phone, "canon": str(canon.id), "frag": str(fconv.id),
                    "move": len(movable), "dry": not execute,
                })
                out["moved_msgs"] += len(movable)
                out["archived"] += 1
                if not execute:
                    continue
                # перенос сообщений (raw — без ORM-каскадов)
                for m in movable:
                    m.conversation_id = canon.id
                    meta = m.extra_meta or {}
                    w = meta.get("waha_id")
                    if w:
                        full.add(str(w))
                        s = _wa_msgid_suffix(w)
                        if s:
                            suf.add(s)
                # перенос стадии/черновика/предложения + архивирование фрагмента
                _carry_and_archive(canon, fconv, f"split→merge по тел. {phone}")
                # фрагмент и его идентичности → customer канона (одна личность)
                if fcust.id != canon_cust.id:
                    # Бронь созвона хранится в profile_data клиента — переносим на канон,
                    # если у него её ещё нет (иначе перенесённый call_booked-таск и стадия
                    # on_call разойдутся с пустым чипом «созвон» в карточке).
                    _fp = fcust.profile_data or {}
                    if _fp.get("booked_call_at") and not (canon_cust.profile_data or {}).get("booked_call_at"):
                        _cp = dict(canon_cust.profile_data or {})
                        _cp["booked_call_at"] = _fp["booked_call_at"]
                        if _fp.get("call_medium"):
                            _cp["call_medium"] = _fp["call_medium"]
                        canon_cust.profile_data = _cp
                    _db.execute(
                        _sqlupdate(ChannelIdentity)
                        .where(ChannelIdentity.customer_id == fcust.id)
                        .values(customer_id=canon_cust.id)
                    )
                    fconv.customer_id = canon_cust.id
                # Созвон и его напоминания НЕ гасим — ПЕРЕНОСИМ на канон (иначе бронь
                # пропадает из задачника/календаря и никто не напомнит — кейс слияния
                # разорванной карточки с уже забронированным созвоном).
                _db.execute(
                    _sqlupdate(ScheduledAction)
                    .where(ScheduledAction.conversation_id == fconv.id,
                           ScheduledAction.status.in_(("pending", "processing")),
                           ScheduledAction.action_type.in_(("call_booked", "call_reminder")))
                    .values(conversation_id=canon.id, customer_id=canon_cust.id)
                )
                # Остальные осиротевшие задачи фрагмента (followup/прочее, привязаны к
                # архив-карточке) — гасим. Созвон уже унесён выше, под этот UPDATE не попадёт.
                _db.execute(
                    _sqlupdate(ScheduledAction)
                    .where(ScheduledAction.conversation_id == fconv.id,
                           ScheduledAction.status.in_(("pending", "processing")))
                    .values(status="superseded")
                )

            if did_something and execute:
                out["groups"] += 1
                # phone-canonical: впредь живой трафик попадает В канон
                canon.channel_conversation_id = phone
                if not _norm_phone(getattr(canon_cust, "phone", None)):
                    canon_cust.phone = ("+" + phone)[:50]
                mx = _db.execute(
                    select(_sqlfunc.max(Message.created_at)).where(Message.conversation_id == canon.id)
                ).scalar()
                if mx is not None:
                    if mx.tzinfo is None:
                        mx = mx.replace(tzinfo=timezone.utc)
                    canon.last_message_at = mx
                # идентичность (whatsapp, телефон) → customer канона
                ex = _db.execute(
                    select(ChannelIdentity).where(
                        ChannelIdentity.channel == "whatsapp",
                        ChannelIdentity.external_id == phone,
                    )
                ).scalar_one_or_none()
                if ex is None:
                    _db.add(ChannelIdentity(customer_id=canon_cust.id, channel="whatsapp", external_id=phone))
                elif ex.customer_id != canon_cust.id:
                    ex.customer_id = canon_cust.id
            elif did_something:
                out["groups"] += 1
        _db.flush()
        return out

    if db is not None:
        return _run(db)
    with session_scope() as _db:
        return _run(_db)


def cancel_orphan_scheduled_actions(db: Optional[Session] = None) -> dict:
    """Погасить осиротевшие задачи/напоминания (чисто БД). Когда карточку
    архивируют (дедуп дублей), её pending/processing scheduled_actions остаются —
    и висят в «Мой день»/Календаре как просрочка/дубли созвонов, хотя карточки
    уже нет. Здесь: все pending/processing действия, чья карточка ARCHIVED →
    status='superseded' (терминальный, /today их не показывает). Так задачник и
    календарь сами актуализируются под слияния. Идемпотентно."""
    from db.connection import session_scope
    from db.models import ScheduledAction, ConversationStatusEnum

    def _run(_db: Session) -> dict:
        archived_ids = [
            r[0] for r in _db.query(Conversation.id)
            .filter(Conversation.status == ConversationStatusEnum.ARCHIVED).all()
        ]
        if not archived_ids:
            return {"superseded": 0}
        n = (
            _db.query(ScheduledAction)
            .filter(ScheduledAction.conversation_id.in_(archived_ids),
                    ScheduledAction.status.in_(("pending", "processing")))
            .update({"status": "superseded"}, synchronize_session=False)
        )
        _db.flush()
        return {"superseded": int(n or 0)}

    if db is not None:
        return _run(db)
    with session_scope() as _db:
        return _run(_db)


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


async def reconcile_wa_conversation(settings: Any, conv_id: Any, *, limit: int = 60) -> dict:
    """Сверить ОДНУ WhatsApp-карточку с реальным чатом в WhatsApp (WAHA = источник правды).

    КОРЕНЬ рассинхрона «панель ≠ WhatsApp»: живой вебхук WAHA NOWEB ловит НЕ 100%
    сообщений (даунтайм, сообщения с телефона напрямую, потери NOWEB) → история в
    панели со временем расходится с реальной перепиской. Здесь тянем последние
    сообщения чата НАПРЯМУЮ (fetch_waha_chat_messages работает по запросу даже когда
    history-store пуст) и ДОБАВЛЯЕМ недостающие — дедуп по waha_id (полному И суффиксу,
    чтобы @lid/@c.us-копии одного сообщения не задвоить). Карточка становится = WhatsApp.

    СЕССИЮ НЕ держим во время сети (урок висов 06-15): короткая сессия на сбор данных →
    сеть вне сессии → короткая сессия на запись. Возвращает {ok, added, fetched, chat_id}."""
    from db.connection import session_scope
    out: dict = {"ok": False, "added": 0, "restamped": 0, "deduped": 0,
                 "removed": 0, "restored": 0, "fetched": 0, "chat_id": None}
    base = getattr(settings, "waha_base_url", None)
    key = getattr(settings, "waha_api_key", None) or ""
    session = getattr(settings, "waha_session", None) or "default"
    if not base:
        out["reason"] = "WAHA не настроен"
        return out

    # 1) короткая сессия: определить chatId (JID) + снимок существующих waha_id
    chat_id = None
    existing_full: set = set()
    with session_scope() as db:
        conv = db.get(Conversation, conv_id)
        if conv is None or conv.channel != "whatsapp":
            out["reason"] = "карточка не WhatsApp"
            return out
        # JID точнее всего — из waha_id последних сообщений (реальный @lid/@c.us)
        metas = db.execute(
            select(Message.extra_meta).where(Message.conversation_id == conv_id)
            .order_by(Message.created_at.desc().nullslast()).limit(40)
        ).scalars().all()
        for meta in metas:
            if isinstance(meta, dict):
                # wa_chat_id (полный JID) надёжнее парсинга waha_id и НЕ зависит от движка
                # (у NOWEB и GOWS разный формат waha_id, а wa_chat_id кладёт парсер всегда).
                jid = meta.get("wa_chat_id") or chat_id_from_waha_id(meta.get("waha_id"))
                if jid and "@" in jid:
                    chat_id = jid
                    break
        if not chat_id:  # фолбэк: из ключа карточки (телефон @c.us / скрытый @lid)
            d = _norm_phone(getattr(conv, "channel_conversation_id", None))
            if d:
                chat_id = (d + "@lid") if _is_lid_key(d) else (d + "@c.us")
        existing_full = _existing_waha_ids(db, conv_id)
    if not chat_id or "@" not in chat_id:
        out["reason"] = "не определить chat id"
        return out
    out["chat_id"] = chat_id

    # 2) СЕТЬ — строго вне сессии
    try:
        sess = await fetch_waha_session_status(base, key, session)
        self_id = _digits(str((sess.get("me") or {}).get("id") or ""))
        raw = await fetch_waha_chat_messages(base, key, session, chat_id, limit=limit)
    except Exception as e:  # noqa: BLE001
        out["reason"] = f"WAHA: {e}"
        return out
    items = [m for m in (normalize_waha_history_message(x, self_id) for x in raw) if m]
    items.sort(key=lambda x: x.get("ts") or 0)
    out["fetched"] = len(items)

    # 3) короткая сессия: СВЕРКА (WAHA = источник правды). Делаем ДВА:
    #    (а) дописать недостающие сообщения;
    #    (б) ПЕРЕШТАМПОВАТЬ время существующих под РЕАЛЬНЫЙ порядок WhatsApp.
    # КОРЕНЬ рассинхрона «панель ≠ WhatsApp» оказался НЕ в потере сообщений (они на
    # месте), а в КРИВОМ created_at: живой вебхук и ручной ответ с телефона штампуют
    # now() (время прихода эха), а НЕ время сообщения в WhatsApp; плюс реплики в одну
    # секунду сортировались произвольно. Итог — те же реплики, но В ДРУГОМ ПОРЯДКЕ.
    # Здесь выравниваем время по WAHA-порядку: монотонно возрастающее, реальное там,
    # где известно (max(real_ts, пред+1мс) — одинаковые секунды не схлопываются и не
    # переворачиваются). Идемпотентно: повторный проход даёт те же времена → без чурна.
    from datetime import timedelta as _td
    import re as _re

    def _norm_txt(s: Any) -> str:
        return _re.sub(r"\s+", " ", (s or "")).strip().lower()

    def _role_str(m: Any) -> str:
        return m.role.value if hasattr(m.role, "value") else str(m.role)

    with session_scope() as db:
        rows = db.execute(
            select(Message).where(Message.conversation_id == conv_id)
        ).scalars().all()
        by_full: dict = {}
        by_suf: dict = {}
        # Строки БЕЗ waha_id (исходящие бота/ручные) по нормализованному тексту — чтобы
        # эхо из WAHA «ЗАБРАЛО» их (проставило waha_id), а не плодило дубль. КОРЕНЬ
        # ДУБЛЕЙ: бот-отправка сохранялась без waha_id → reconcile матчил только по
        # waha_id → добавлял эхо как новую строку.
        nowid_by_text: dict = {}
        # Engine-agnostic индекс (роль, нормализованный текст) → строки. Повторный импорт
        # того же сообщения под ДРУГИМ движком (NOWEB→GOWS дают разные waha_id) матчим по
        # содержимому+роли+времени, а не только по id — иначе переезд плодит дубли.
        by_text_role: dict = {}
        _consumed_text: set = set()
        for r in rows:
            meta = r.extra_meta if isinstance(r.extra_meta, dict) else {}
            wid = meta.get("waha_id")
            if wid:
                by_full[str(wid)] = r
                suf = _wa_msgid_suffix(wid)
                if suf:
                    by_suf.setdefault(suf, r)
            else:
                nowid_by_text.setdefault(_norm_txt(r.content), []).append(r)
            by_text_role.setdefault((_role_str(r), _norm_txt(r.content)), []).append(r)
        prev_dt = None
        last_ts = None
        for it in items:
            wid = it.get("waha_id")
            real = _ts_to_dt(it.get("ts"))
            if real is None:
                real = (prev_dt + _td(milliseconds=1)) if prev_dt else datetime.now(timezone.utc)
            # строго возрастающее в порядке WhatsApp, сохраняя реальные интервалы
            assigned = real if (prev_dt is None or real > prev_dt) else prev_dt + _td(milliseconds=1)
            prev_dt = assigned
            existing = by_full.get(str(wid)) if wid else None
            if existing is None and wid:
                existing = by_suf.get(_wa_msgid_suffix(wid))
            if existing is None and wid:
                # нет совпадения по waha_id → возможно это ЭХО нашей же отправки,
                # сохранённой БЕЗ waha_id. Забрать её (проставить waha_id), НЕ плодить дубль.
                cands = nowid_by_text.get(_norm_txt(it["content"]))
                if cands:
                    for r in list(cands):
                        rc = r.created_at
                        if rc is not None and rc.tzinfo is None:
                            rc = rc.replace(tzinfo=timezone.utc)
                        if rc is None or abs((rc - assigned).total_seconds()) <= 180:
                            mm = dict(r.extra_meta or {})
                            mm["waha_id"] = str(wid)
                            mm["delivered"] = True
                            r.extra_meta = mm
                            by_full[str(wid)] = r
                            _s = _wa_msgid_suffix(wid)
                            if _s:
                                by_suf.setdefault(_s, r)
                            cands.remove(r)
                            existing = r
                            break
            if existing is None:
                # Cross-engine дедуп: то же сообщение могло уже лежать под ДРУГИМ waha_id
                # (переезд NOWEB→GOWS) или из живого вебхука. Матчим по (роль, текст, ±30с),
                # чтобы повторный импорт НЕ плодил дубль. Каждую строку забираем ≤1 раза за
                # проход (_consumed_text) — легитимные повторы текста сохраняются.
                _ctxt = _norm_txt(it["content"])
                if _ctxt:
                    for r in by_text_role.get((it["role"], _ctxt), []):
                        if id(r) in _consumed_text:
                            continue
                        rc = r.created_at
                        if rc is not None and rc.tzinfo is None:
                            rc = rc.replace(tzinfo=timezone.utc)
                        if rc is None or abs((rc - assigned).total_seconds()) <= 30:
                            _consumed_text.add(id(r))
                            if wid:  # перецепляем на актуальный (GOWS) id — впредь матч by_full
                                mm = dict(r.extra_meta or {})
                                mm["waha_id"] = str(wid)
                                r.extra_meta = mm
                                by_full[str(wid)] = r
                                _s2 = _wa_msgid_suffix(wid)
                                if _s2:
                                    by_suf.setdefault(_s2, r)
                            existing = r
                            out["deduped"] = out.get("deduped", 0) + 1
                            break
            if existing is not None:
                cur = existing.created_at
                if cur is not None and cur.tzinfo is None:
                    cur = cur.replace(tzinfo=timezone.utc)
                if cur is None or abs((cur - assigned).total_seconds()) >= 0.001:
                    existing.created_at = assigned  # выровнять под порядок WhatsApp
                    out["restamped"] += 1
            else:
                m = Message(
                    conversation_id=conv_id, role=it["role"], content=it["content"],
                    extra_meta={"waha_id": wid, "source": "wa_resync", "wa_type": it.get("type")},
                    created_at=assigned,
                )
                db.add(m)
                if wid:
                    by_full[str(wid)] = m
                    suf = _wa_msgid_suffix(wid)
                    if suf:
                        by_suf.setdefault(suf, m)
                out["added"] += 1
            if last_ts is None or assigned > last_ts:
                last_ts = assigned
        db.flush()
        _floor = datetime.min.replace(tzinfo=timezone.utc)
        # ДЕДУП-1 (по СУФФИКСУ message-id): одно и то же сообщение WhatsApp, сохранённое
        # дважды (разные префиксы waha_id @lid/@c.us, или ручное+история, или
        # operator+operator) — суффикс message-id одинаков → это буквально ОДНО
        # сообщение. Ловит дубли, которые text+role-дедуп не берёт (operator+operator,
        # user+user). Суффикс message-id глобально уникален → безопасно.
        all_msgs = db.execute(
            select(Message).where(Message.conversation_id == conv_id)
        ).scalars().all()
        by_sfx: dict = {}
        for r in all_msgs:
            meta = r.extra_meta if isinstance(r.extra_meta, dict) else {}
            sfx = _wa_msgid_suffix(meta.get("waha_id"))
            if sfx:
                by_sfx.setdefault(sfx, []).append(r)
        for sgrp in by_sfx.values():
            if len(sgrp) < 2:
                continue
            sgrp.sort(key=lambda m: (m.created_at or _floor))  # оставляем самую раннюю
            for dup in sgrp[1:]:
                db.delete(dup)
                out["deduped"] += 1
        db.flush()
        # ДЕДУП-2 (по ТЕКСТУ, assistant+operator): эхо нашей же отправки, где у одной из
        # строк НЕТ waha_id (бот-отправка ещё не «заклеймлена») → суффикс не сматчить.
        # Одинаковый текст у assistant И operator → оставить ОДНУ (с waha_id).
        fresh = db.execute(
            select(Message).where(Message.conversation_id == conv_id,
                                  Message.role.in_(["assistant", "operator"]))
        ).scalars().all()
        groups: dict = {}
        for r in fresh:
            t = _norm_txt(r.content)
            if t:
                groups.setdefault(t, []).append(r)
        for t, grp in groups.items():
            if len(grp) < 2:
                continue
            roles = {_role_str(m) for m in grp}
            # Дубль нашей же отправки: либо смешанная пара assistant+operator (эхо, любой
            # длины), либо все «наши» (assistant/operator) с СОДЕРЖАТЕЛЬНЫМ текстом (≥15
            # симв — одно и то же длинное сообщение мы дважды не шлём; короткие «Хорошо»/
            # «Да» могут законно повторяться → их не трогаем).
            mixed_echo = {"assistant", "operator"} <= roles
            our_side = roles <= {"assistant", "operator"}
            if not (mixed_echo or (our_side and len(t) >= 15)):
                continue
            grp.sort(key=lambda m: (
                0 if (m.extra_meta or {}).get("waha_id") else 1,
                (m.created_at or _floor),
            ))
            for dup in grp[1:]:
                db.delete(dup)
                out["deduped"] += 1
        # ДЕЛИШН (WAHA = источник правды): сообщение, которое РАНЬШЕ пришло из WhatsApp
        # (есть waha_id) и попадает в ОКНО подтянутой выборки, но в текущем чате WhatsApp
        # его БОЛЬШЕ НЕТ → его удалили в WhatsApp («удалить у всех»). Гасим ОБРАТИМО
        # (wa_deleted=True в extra_meta — строку НЕ удаляем, правило never-delete; тред её
        # прячет). Само-лечение: вернулось в выборку WAHA → флаг снимаем. Гасим ТОЛЬКО в
        # окне [floor..ceil] подтянутых ts (вне окна — просто за пределом limit, НЕ удалено)
        # и только при здоровой выборке (≥2), чтобы единичный сбой WAHA не погасил живое.
        if len(items) >= 2:
            _wa_full: set = set()
            _wa_suf: set = set()
            _ts_vals: list = []
            for it in items:
                _w = it.get("waha_id")
                if _w:
                    _wa_full.add(str(_w))
                    _sf = _wa_msgid_suffix(_w)
                    if _sf:
                        _wa_suf.add(_sf)
                _t = _ts_to_dt(it.get("ts"))
                if _t is not None:
                    _ts_vals.append(_t)
            if _wa_suf and _ts_vals:
                _floor_ts, _ceil_ts = min(_ts_vals), max(_ts_vals)
                surv = db.execute(
                    select(Message).where(Message.conversation_id == conv_id)
                ).scalars().all()
                for r in surv:
                    meta = r.extra_meta if isinstance(r.extra_meta, dict) else {}
                    wid = meta.get("waha_id")
                    if not wid:
                        continue  # бот-черновик/ручное без waha_id — не из стора WhatsApp
                    sfx = _wa_msgid_suffix(wid)
                    present = (str(wid) in _wa_full) or bool(sfx and sfx in _wa_suf)
                    rc = r.created_at
                    if rc is not None and rc.tzinfo is None:
                        rc = rc.replace(tzinfo=timezone.utc)
                    if present:
                        if meta.get("wa_deleted"):  # вернулось → снять флаг (само-лечение)
                            mm = dict(meta)
                            mm.pop("wa_deleted", None)
                            mm.pop("wa_deleted_at", None)
                            r.extra_meta = mm
                            out["restored"] = out.get("restored", 0) + 1
                        continue
                    if (rc is not None and _floor_ts <= rc <= _ceil_ts
                            and not meta.get("wa_deleted")):
                        mm = dict(meta)
                        mm["wa_deleted"] = True
                        mm["wa_deleted_at"] = datetime.now(timezone.utc).isoformat()
                        r.extra_meta = mm
                        out["removed"] = out.get("removed", 0) + 1
        if last_ts is not None:
            conv = db.get(Conversation, conv_id)
            if conv is not None and (not conv.last_message_at or last_ts > conv.last_message_at):
                conv.last_message_at = last_ts
    # «ok» = реально были изменения; иначе честный «уже синхронизировано» вместо фейкового ✅
    out["ok"] = any(out.get(k, 0) > 0 for k in ("added", "restamped", "deduped", "removed", "restored"))
    if not out["ok"]:
        out["reason"] = out.get("reason") or "уже синхронизировано"
    return out


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
                    # Стадия воронки из содержания — ТОЛЬКО заполняем дефолтный
                    # new_lead, не перетираем курированные/ручные стадии (импорт,
                    # оператор). Так 44 синхронизированных лида распределятся по
                    # воронке по смыслу, а 8 импортных сохранят свои стадии.
                    stg = result.get("stage")
                    _active = {"in_dialog", "qualified", "on_call", "proposal",
                               "prepayment", "completed_won"}
                    if stg in _active and (conversation.lead_stage or "new_lead") == "new_lead":
                        conversation.lead_stage = stg
                        stats.setdefault("stages_assigned", 0)
                        stats["stages_assigned"] += 1
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
