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


def resolve_or_create_customer(
    db: Session,
    channel: str,
    external_id: str,
    email: Optional[str] = None,
    username: Optional[str] = None,
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
        # Backfill email if the lead just provided one
        if email and not customer.email:
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


def update_email(db: Session, customer_id: UUID, email: str) -> Customer:
    """Set email on a customer. If email already belongs to another customer,
    MERGE both customers: re-point the other's identities to this one, then
    delete the orphan customer row.

    This implements cross-channel customer unification: lead writes from TG
    without email → customer A. Same lead writes from website with email →
    customer B. Later TG-customer A gets the same email → merge A+B.

    Returns the surviving Customer (always the one identified by `customer_id`
    when the email is new; if a merge happens, the surviving customer is the
    one that already had the email — i.e. `customer_id` may be deleted).
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

    # (4) delete the now-orphaned customer (its identities list is empty
    #     after the UPDATE in step 1, so cascade has nothing to delete)
    db.delete(other)
    db.flush()

    # Make sure target.identities reflects the freshly re-pointed rows
    db.expire(target, ["identities"])

    return target
