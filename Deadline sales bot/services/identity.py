"""Identity resolver — maps a (channel, external_id) lead to a `customers` row.

Core rule: **email is the merge anchor**. Two leads who write from different
channels are the same customer iff they later share an email. Until an email
is provided, channel identities live in parallel.

Lifecycle:
1. Lead writes from Telegram with `tg_user_id=12345`.
   → New customer + channel_identity{telegram, 12345}.
2. Same lead writes from website with `session_id=abc`, gives email `ivan@x.com`.
   → resolve_or_create finds NO existing identity for (website, abc), but email
     `ivan@x.com` belongs to no one yet → new customer + identity{website, abc}.
   → Lead later gives the same email from Telegram. update_email() detects
     collision and MERGES the two customer rows into one, re-pointing identities.
3. Same lead writes from Instagram, no email yet.
   → New customer, lives parallel until email comes through.

This is MVP merging — production-grade dedup would also consider phone, name
fuzzy-match, and timing. Out of scope for Phase 1.
"""

from __future__ import annotations

import logging
from typing import Optional
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from db.models import Customer, ChannelIdentity


log = logging.getLogger(__name__)


def find_customer_by_email(db: Session, email: str) -> Optional[Customer]:
    """Look up customer by email. Returns None if not found."""
    if not email:
        return None
    return db.execute(
        select(Customer).where(Customer.email == email)
    ).scalar_one_or_none()


def find_customer_by_identity(
    db: Session, channel: str, external_id: str
) -> Optional[Customer]:
    """Look up customer by (channel, external_id) pair. Returns None if not found."""
    identity = db.execute(
        select(ChannelIdentity).where(
            ChannelIdentity.channel == channel,
            ChannelIdentity.external_id == external_id,
        )
    ).scalar_one_or_none()
    return identity.customer if identity else None


def _norm_handle(username: Optional[str]) -> str:
    if not username:
        return ""
    h = username.strip()
    return h if h.startswith("@") else ("@" + h if h else "")


def find_customer_by_telegram_username(db: Session, username: str) -> Optional[Customer]:
    """Найти клиента по telegram @username — для склейки МЕЖДУ каналами: лид дал
    @username на сайте, потом написал из самого Telegram. @username в Telegram
    глобально уникален, поэтому мёрж по нему безопасен (без ложных склеек).

    Ищем по ChannelIdentity.username И по Customer.identity_keys['tg_handle']
    (туда кладём @username, который лид назвал на сайте)."""
    handle = _norm_handle(username)
    if not handle:
        return None
    ident = db.execute(
        select(ChannelIdentity).where(ChannelIdentity.username == handle)
    ).scalars().first()
    if ident is not None:
        return ident.customer
    try:
        cust = db.execute(
            select(Customer).where(Customer.identity_keys["tg_handle"].astext == handle)
        ).scalars().first()
    except Exception:  # noqa: BLE001 — jsonb-запрос не должен ронять резолв
        cust = None
    return cust


def find_customer_by_phone(db: Session, phone: Optional[str]) -> Optional[Customer]:
    """Найти карточку по НОРМАЛИЗОВАННОМУ телефону (хвост-10 цифр). Только для нац.
    номеров ≥10 цифр (иначе риск ложной склейки коротких/служебных). Игнорирует уже
    слитые карточки (profile_data.merged_into). Возвращает самую раннюю (стабильный
    канон). Профилактика дублей: новые лиды доливаются в существующую карточку."""
    digits = "".join(ch for ch in (phone or "") if ch.isdigit())
    if len(digits) < 10:
        return None
    tail = digits[-10:]
    rows = db.execute(
        select(Customer).where(Customer.phone.isnot(None))
        .order_by(Customer.created_at.asc())
    ).scalars().all()
    for c in rows:
        if (c.profile_data or {}).get("merged_into"):
            continue
        cd = "".join(ch for ch in (c.phone or "") if ch.isdigit())
        if len(cd) >= 10 and cd[-10:] == tail:
            return c
    return None


def resolve_or_create_customer(
    db: Session,
    channel: str,
    external_id: str,
    email: Optional[str] = None,
    username: Optional[str] = None,
    phone: Optional[str] = None,
) -> Customer:
    """Resolve a lead from (channel, external_id) → Customer, creating as needed.

    Algorithm:
      1. If (channel, external_id) identity exists → return that customer.
         If email given and customer.email empty, set it (mild fill-in).
      2. Else if email given AND a customer with that email exists →
         attach a new identity to that customer and return.
      3. Else create a fresh customer and attach the new identity.

    Always returns a Customer with at least one ChannelIdentity in the session.
    Caller is responsible for commit (db.commit() or session_scope context).
    """
    # ----- Step 1: try identity lookup -----
    existing_identity = db.execute(
        select(ChannelIdentity).where(
            ChannelIdentity.channel == channel,
            ChannelIdentity.external_id == external_id,
        )
    ).scalar_one_or_none()

    if existing_identity is not None:
        customer = existing_identity.customer
        # Лид назвал email на УЖЕ известной идентичности. Если этот email
        # принадлежит ДРУГОМУ customer (писал с другого канала) — это сигнал
        # «один человек», нужно СЛИТЬ записи, а не просто записать email.
        # update_email() делает merge-aware апдейт (re-point identities + delete
        # orphan). Оборачиваем в try/except: сбой склейки НЕ должен ронять
        # обработку сообщения — в худшем случае останется как было.
        if email and customer.email != email:
            try:
                customer = update_email(db, customer.id, email)
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "[identity] email-merge failed for customer %s (email=%s): %s — "
                    "fallback: оставляю как есть", customer.id, email, exc,
                )
                if not customer.email:
                    customer.email = email
                    db.flush()
        # Backfill username on the identity if it was empty
        if username and not existing_identity.username:
            existing_identity.username = username
            db.flush()
        return customer

    # ----- Step 2: try email anchor -----
    customer = None
    if email:
        customer = find_customer_by_email(db, email)

    # ----- Step 2.5: try telegram @username anchor (глобально уникален) -----
    # Склейка между каналами: лид дал @username на сайте → пишет из своего TG.
    if customer is None and username:
        customer = find_customer_by_telegram_username(db, username)
        if customer is not None:
            log.info(
                "[identity] cross-channel merge via tg username %s → customer %s",
                _norm_handle(username), customer.id,
            )

    # ----- Step 2.7: phone anchor (нац. номер ≥10 цифр) — профилактика дублей -----
    # Новый лид с тем же номером доливается в существующую карточку, а не плодит вторую.
    if customer is None and phone:
        customer = find_customer_by_phone(db, phone)
        if customer is not None:
            log.info("[identity] phone-merge → customer %s", customer.id)

    # ----- Step 3: new customer if nothing matched -----
    if customer is None:
        customer = Customer(
            email=email,
            first_channel=channel,
        )
        db.add(customer)
        db.flush()  # populate customer.id before linking identity

    # ----- Attach the identity (we know it does not yet exist on customer) -----
    identity = ChannelIdentity(
        customer_id=customer.id,
        channel=channel,
        external_id=external_id,
        username=username,
    )
    db.add(identity)
    db.flush()

    return customer


def resolve_or_create_customer_with_meta(
    db: Session,
    channel: str,
    external_id: str,
    email: Optional[str] = None,
    username: Optional[str] = None,
) -> tuple[Customer, bool]:
    """Same as resolve_or_create_customer but also returns a flag
    indicating whether this call merged into a pre-existing Customer
    via email (i.e. a returning lead).

    Returns (customer, was_returning_match) where was_returning_match is True iff:
      - the (channel, external_id) identity did NOT exist beforehand
      - AND email was provided
      - AND a Customer with that email already existed prior to this call

    The function still creates / links identities just like the plain version.
    """
    # Pre-check identity — if (channel, external_id) already exists, this
    # is NOT a returning-lead event, it's continued use of a known identity.
    identity_existed = db.execute(
        select(ChannelIdentity.id).where(
            ChannelIdentity.channel == channel,
            ChannelIdentity.external_id == external_id,
        )
    ).first() is not None

    # Pre-check email — if no email provided OR no customer with this email
    # exists yet, the resolve call below cannot be a returning match.
    pre_existing_customer_for_email = None
    pre_existing_for_username = None
    if not identity_existed:
        if email:
            pre_existing_customer_for_email = find_customer_by_email(db, email)
        if username and pre_existing_customer_for_email is None:
            pre_existing_for_username = find_customer_by_telegram_username(db, username)

    customer = resolve_or_create_customer(
        db, channel=channel, external_id=external_id, email=email, username=username
    )

    was_returning_match = not identity_existed and (
        pre_existing_customer_for_email is not None
        or pre_existing_for_username is not None
    )
    return customer, was_returning_match


def link_identity(
    db: Session,
    customer_id: UUID,
    channel: str,
    external_id: str,
    username: Optional[str] = None,
) -> ChannelIdentity:
    """Attach (channel, external_id) to an existing customer.

    Idempotent: if the identity already exists, returns it. If it exists but
    points to a DIFFERENT customer, raises ValueError — that would be a data
    integrity issue worth surfacing rather than silently re-pointing.
    """
    existing = db.execute(
        select(ChannelIdentity).where(
            ChannelIdentity.channel == channel,
            ChannelIdentity.external_id == external_id,
        )
    ).scalar_one_or_none()

    if existing is not None:
        if existing.customer_id != customer_id:
            raise ValueError(
                f"Identity ({channel}, {external_id}) already linked to "
                f"customer {existing.customer_id}, refusing to re-point to {customer_id}. "
                f"Use update_email() to merge customers instead."
            )
        return existing

    identity = ChannelIdentity(
        customer_id=customer_id,
        channel=channel,
        external_id=external_id,
        username=username,
    )
    db.add(identity)
    db.flush()
    return identity


def _absorb_crm_contacts(survivor: Customer, dying: Customer) -> None:
    """При слиянии двух кастомеров согласовать crm_contact_id, чтобы в CRM не
    осталось ДВУХ карточек на одного человека (баг «дубль контакта при merge»).

      • у survivor карточки ещё нет, у dying есть → просто усыновляем id;
      • у обоих есть и они РАЗНЫЕ → помечаем survivor.profile_data['crm_merge_absorb']
        = dying_id. CRM-слой (dispatch_on_message_turn) на следующем ходе сольёт
        их через HubSpot Merge API (primary=survivor, secondary=dying).

    Чисто DB-операция — без импорта CRM-слоя, чтобы identity.py оставался
    свободным от сетевых зависимостей (склейка не должна ронять обработку)."""
    dying_cid = getattr(dying, "crm_contact_id", None)
    if not dying_cid:
        return
    surv_cid = getattr(survivor, "crm_contact_id", None)
    if not surv_cid:
        survivor.crm_contact_id = dying_cid
        return
    if str(surv_cid) != str(dying_cid):
        _p = dict(getattr(survivor, "profile_data", None) or {})
        _p["crm_merge_absorb"] = str(dying_cid)
        survivor.profile_data = _p
        log.info("[identity] CRM merge intent: contact %s ← %s (survivor customer %s)",
                 surv_cid, dying_cid, survivor.id)


def update_email(db: Session, customer_id: UUID, email: str) -> Customer:
    """Set email on a customer. If email already belongs to another customer,
    MERGE both customers: re-point the other's identities to this one, then
    delete the orphan customer row.

    This implements cross-channel customer unification: lead writes from TG
    without email → customer A. Same lead writes from website with email →
    customer B. Later TG-customer A gets the same email → merge A+B.

    Returns the surviving Customer. The customer identified by `customer_id`
    (the target) ALWAYS survives and gets the email; if a merge happens, the
    pre-existing customer that already had this email (`other`) has its
    identities re-pointed to the target and is then deleted.
    """
    if not email:
        raise ValueError("email is required")

    target = db.get(Customer, customer_id)
    if target is None:
        raise ValueError(f"Customer {customer_id} not found")

    # Already set to the same email — no-op
    if target.email == email:
        return target

    # Find any existing customer with this email
    other = db.execute(
        select(Customer).where(Customer.email == email)
    ).scalar_one_or_none()

    if other is None:
        # Email is new — just set it on target
        target.email = email
        db.flush()
        return target

    if other.id == target.id:
        # Defensive: same row, shouldn't happen because target.email != email
        return target

    # ----- MERGE: re-point other's identities to target, then delete the orphan -----
    # Two subtleties here:
    #
    # (a) UNIQUE(email) constraint — we must release email from `other` before
    #     assigning it to `target`, else the UPDATE hits a unique violation.
    #
    # (b) cascade="all, delete-orphan" on Customer.identities — assigning
    #     `identity.customer_id = target.id` via ORM does NOT update the
    #     in-memory `customer` relationship on the identity object. When we
    #     then call `db.delete(other)`, the ORM cascade still thinks
    #     `other.identities` includes those rows and DELETEs them as orphans.
    #     Bypass: emit a direct UPDATE statement (no ORM relationship), then
    #     expire `other` so subsequent `other.identities` reads from the DB.
    n_identities = len(other.identities)
    log.info(
        f"merging customer {other.id} (email={email}) into customer {target.id} "
        f"(taking over {n_identities} identities)"
    )

    # (1) re-point identities via raw UPDATE — avoids ORM cascade landmines
    db.execute(
        update(ChannelIdentity)
        .where(ChannelIdentity.customer_id == other.id)
        .values(customer_id=target.id)
    )
    # Force ORM to re-read `other.identities` from DB on next access
    db.expire(other, ["identities"])

    # (2) release email from `other`
    other.email = None
    db.flush()

    # (3) assign email to `target`
    target.email = email
    db.flush()

    # (3.5) CRM dedup: согласовать crm_contact_id, чтобы в HubSpot не осталось
    #       двух карточек на человека. ДО удаления other — иначе потеряем его id.
    _absorb_crm_contacts(target, other)
    db.flush()

    # (4) delete the now-orphaned customer (its identities list is empty
    #     after the UPDATE in step 1, so cascade has nothing to delete)
    db.delete(other)
    db.flush()

    # Make sure target.identities reflects the freshly re-pointed rows
    db.expire(target, ["identities"])

    return target


def merge_customers(db: Session, canon_id: Any, shadow_id: Any) -> dict:
    """РУЧНОЕ слияние двух карточек (Customer-уровень) — для кнопки «Объединить» в
    Настройках, когда оператор ВИДИТ, что две карточки = один человек, но авто-склейка
    не сработала (рекламный @lid без номера + карточка с номером и т.п.).

    NEVER-DELETE: shadow НЕ удаляется — помечается profile_data.merged_into (обратимо).
    Переносит identities/conversations/scheduled_actions/CRM на canon, дозаполняет пустые
    поля canon. Идемпотентно: повторный вызов на уже-слитой → no-op. Раздельные UPDATE
    (как в update_email) обходят ORM-каскад delete-orphan."""
    from datetime import datetime as _dt, timezone as _tz
    from db.models import Conversation as _Conv, ScheduledAction as _SA
    if str(canon_id) == str(shadow_id):
        raise ValueError("нельзя слить карточку саму с собой")
    canon = db.get(Customer, canon_id)
    shadow = db.get(Customer, shadow_id)
    if canon is None or shadow is None:
        raise ValueError("карточка не найдена")
    if (shadow.profile_data or {}).get("merged_into"):
        return {"ok": True, "already_merged": True, "canon": str(canon.id)}

    moved: dict = {}
    moved["identities"] = db.execute(
        update(ChannelIdentity).where(ChannelIdentity.customer_id == shadow.id)
        .values(customer_id=canon.id)).rowcount or 0
    db.expire(shadow, ["identities"])
    moved["conversations"] = db.execute(
        update(_Conv).where(_Conv.customer_id == shadow.id)
        .values(customer_id=canon.id)).rowcount or 0
    db.expire(shadow, ["conversations"])
    moved["tasks"] = db.execute(
        update(_SA).where(_SA.customer_id == shadow.id)
        .values(customer_id=canon.id)).rowcount or 0

    # email — UNIQUE-safe: освобождаем у shadow ДО присвоения canon
    shadow_email = shadow.email
    if shadow_email:
        shadow.email = None
        db.flush()
        if not (canon.email or "").strip():
            canon.email = shadow_email
            db.flush()
    # дозаполнить пустые поля canon (не перетирая существующие)
    if not (canon.name or "").strip() and (shadow.name or "").strip():
        canon.name = shadow.name[:200]
    if not (canon.phone or "").strip() and (shadow.phone or "").strip():
        canon.phone = shadow.phone
    try:
        if int(shadow.lead_score or 0) > int(canon.lead_score or 0):
            canon.lead_score = shadow.lead_score
    except (TypeError, ValueError):
        pass
    _pd = {**(shadow.profile_data or {}), **(canon.profile_data or {})}
    if (shadow.profile_data or {}).get("booked_call_at") and not _pd.get("booked_call_at"):
        _pd["booked_call_at"] = shadow.profile_data["booked_call_at"]
    canon.profile_data = _pd
    _absorb_crm_contacts(canon, shadow)

    # пометить shadow слитым (NEVER-DELETE) + откатные данные
    sp = dict(shadow.profile_data or {})
    sp["merged_into"] = str(canon.id)
    if shadow.phone:
        sp["_phone_before_merge"] = shadow.phone
    sp["_merged_at"] = _dt.now(_tz.utc).isoformat()
    shadow.profile_data = sp
    shadow.phone = None  # чтобы не попадал в выборки/анкоры/кандидаты повторно
    db.flush()
    log.info("[identity] manual merge: shadow %s → canon %s (%s)", shadow.id, canon.id, moved)
    return {"ok": True, "canon": str(canon.id), "shadow": str(shadow.id), **moved}


def find_duplicate_candidates(db: Session, limit: int = 40) -> list:
    """Вероятные ДУБЛИ карточек для ручного слияния в Настройках. Группирует активные
    (не слитые) карточки по: телефон(хвост-10) ЛИБО нормализованное имя. Возвращает
    группы по 2+ карточки с инфо для решения оператором. НИЧЕГО не меняет (только показ).
    Имя-группы намеренно показываем — это те случаи, что авто-склейка не берёт (риск
    разных людей), их подтверждает человек."""
    from db.models import Conversation as _Conv
    rows = db.execute(select(Customer)).scalars().all()
    convs = db.execute(
        select(_Conv).order_by(_Conv.last_message_at.desc().nullslast())
    ).scalars().all()
    last_conv: dict = {}
    conv_count: dict = {}
    for cv in convs:
        conv_count[cv.customer_id] = conv_count.get(cv.customer_id, 0) + 1
        last_conv.setdefault(cv.customer_id, cv)

    def _norm_name(n: Optional[str]) -> str:
        return " ".join((n or "").split()).strip().lower()

    def _ptail(p: Optional[str]) -> str:
        d = "".join(ch for ch in (p or "") if ch.isdigit())
        return d[-10:] if len(d) >= 10 else ""

    by_phone: dict = {}
    by_name: dict = {}
    for c in rows:
        if (c.profile_data or {}).get("merged_into"):
            continue
        if c.id not in last_conv and not (c.name or c.phone or c.email):
            continue  # пустышка без диалога и контактов — не предлагаем
        pt = _ptail(c.phone)
        if pt:
            by_phone.setdefault(pt, []).append(c)
        nm = _norm_name(c.name)
        if len(nm) >= 2:
            by_name.setdefault(nm, []).append(c)

    def _card(c: Customer) -> dict:
        cv = last_conv.get(c.id)
        return {
            "id": str(c.id),
            "name": c.name or c.email or (("+" + c.phone) if c.phone else "Лид"),
            "phone": c.phone,
            "channel": cv.channel if cv else None,
            "stage": cv.lead_stage if cv else None,
            "last_message_at": cv.last_message_at.isoformat() if (cv and cv.last_message_at) else None,
            "conversations": conv_count.get(c.id, 0),
            "lead_score": int(c.lead_score or 0),
        }

    seen_pairs: set = set()
    out: list = []

    def _emit(group: list, reason: str) -> None:
        ids = tuple(sorted(str(c.id) for c in group))
        if ids in seen_pairs:
            return
        seen_pairs.add(ids)
        g = sorted(group, key=lambda c: (0 if c.phone else 1, -int(c.lead_score or 0)))
        out.append({"reason": reason, "suggested_canon": str(g[0].id),
                    "cards": [_card(c) for c in g]})

    for grp in by_phone.values():
        if len(grp) >= 2:
            _emit(grp, "одинаковый телефон")
    for grp in by_name.values():
        if len(grp) >= 2:
            _emit(grp, "одинаковое имя")
        if len(out) >= limit:
            break
    return out[:limit]


def bridge_telegram_to_website_session(
    db: Session,
    tg_external_id: str,
    tg_username: Optional[str],
    session_token: str,
) -> Optional[Customer]:
    """Deep-link мост: лид нажал на сайте кнопку «Написать в Telegram» (ссылка
    t.me/bot?start=<session_id>) → Telegram прислал боту «/start <session_id>».
    Склеиваем телеграм-чат с тем же кастомером, что был на сайте → ОДНА карточка,
    телеграм-ник записывается в контакт. БЕЗ email.

    Возвращает website-кастомера (выживший) или None, если сессия не найдена
    (токен протух / невалиден — тогда обычное знакомство).
    """
    web_identity = db.execute(
        select(ChannelIdentity).where(
            ChannelIdentity.channel == "website",
            ChannelIdentity.external_id == session_token,
        )
    ).scalar_one_or_none()
    if web_identity is None:
        log.info("[identity] deep-link: website session %s не найдена (токен протух?)", session_token)
        return None
    W = web_identity.customer

    tg_identity = db.execute(
        select(ChannelIdentity).where(
            ChannelIdentity.channel == "telegram",
            ChannelIdentity.external_id == tg_external_id,
        )
    ).scalar_one_or_none()

    if tg_identity is None:
        # Самый частый случай: первый заход из телеги → просто вешаем
        # телеграм-идентичность на website-кастомера.
        db.add(ChannelIdentity(
            customer_id=W.id, channel="telegram",
            external_id=str(tg_external_id), username=tg_username,
        ))
        db.flush()
        log.info("[identity] deep-link bridge: TG chat %s → website customer %s (one card)",
                 tg_external_id, W.id)
    elif tg_identity.customer_id != W.id:
        # У телеги уже был отдельный кастомер T → сливаем T в W (история телеги
        # переезжает в карточку с сайта). Email освобождаем перед переносом
        # (UNIQUE(email)). Идентичности и диалоги перенаправляем, T удаляем.
        T = tg_identity.customer
        _tmail = getattr(T, "email", None)
        if _tmail and not W.email:
            T.email = None
            db.flush()
            W.email = _tmail
        if not getattr(W, "name", None) and getattr(T, "name", None):
            W.name = T.name
        if not getattr(W, "phone", None) and getattr(T, "phone", None):
            W.phone = T.phone
        db.flush()
        from db.models import Conversation
        db.execute(update(ChannelIdentity).where(ChannelIdentity.customer_id == T.id).values(customer_id=W.id))
        db.execute(update(Conversation).where(Conversation.customer_id == T.id).values(customer_id=W.id))
        # CRM dedup: у телеграм-кастомера T могла быть СВОЯ карточка в HubSpot.
        # Согласуем crm_contact_id ДО удаления T, иначе карточка осиротеет.
        _absorb_crm_contacts(W, T)
        db.expire(T, ["identities"])
        db.flush()
        db.delete(T)
        db.flush()
        db.expire(W, ["identities"])
        log.info("[identity] deep-link bridge: merged TG customer %s into website customer %s", T.id, W.id)
    else:
        # Уже на том же кастомере — просто добьём ник.
        if tg_username and not tg_identity.username:
            tg_identity.username = tg_username
            db.flush()

    return W
