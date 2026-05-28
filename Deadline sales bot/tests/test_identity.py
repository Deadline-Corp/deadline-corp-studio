"""Unit tests for services/identity.py — the 4 scenarios from week_1_plan.md.

Scenarios (mirroring plan):
  (1) New TG lead → customer created, identity{tg} attached
  (2) Lead later gives email → email saved on the customer
  (3) Same email shows up via website → website identity links to the
      SAME customer (email-anchor cross-channel merge)
  (4) Different channel, no email yet → new separate customer

Plus a few edge cases worth catching.

Run from project root:
    PYTHONIOENCODING=utf-8 venv/Scripts/python.exe -m pytest tests/test_identity.py -v
"""

from __future__ import annotations

import uuid

import pytest

from db.models import Customer, ChannelIdentity
from services.identity import (
    find_customer_by_email,
    find_customer_by_identity,
    link_identity,
    resolve_or_create_customer,
    resolve_or_create_customer_with_meta,
    update_email,
)


# Use random external_ids per test run so collisions never happen even if
# rollback misfires for some reason.
def _eid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


# ============================================================================
# Scenario 1 — New TG lead → customer + identity created
# ============================================================================


def test_new_tg_lead_creates_customer_and_identity(db):
    ext_id = _eid("tg")
    customer = resolve_or_create_customer(
        db, channel="telegram", external_id=ext_id, username="@kolya"
    )

    assert customer.id is not None
    assert customer.email is None  # not given yet
    assert customer.first_channel == "telegram"

    identities = customer.identities
    assert len(identities) == 1
    assert identities[0].channel == "telegram"
    assert identities[0].external_id == ext_id
    assert identities[0].username == "@kolya"


def test_resolve_is_idempotent_for_same_identity(db):
    ext_id = _eid("tg")
    c1 = resolve_or_create_customer(db, channel="telegram", external_id=ext_id)
    c2 = resolve_or_create_customer(db, channel="telegram", external_id=ext_id)
    assert c1.id == c2.id
    assert len(c2.identities) == 1  # not duplicated


# ============================================================================
# Scenario 2 — Lead gives email → it saves on the customer
# ============================================================================


def test_email_saves_on_customer_via_update_email(db):
    ext_id = _eid("tg")
    customer = resolve_or_create_customer(db, channel="telegram", external_id=ext_id)
    assert customer.email is None

    updated = update_email(db, customer.id, "ivan@example.com")
    assert updated.id == customer.id
    assert updated.email == "ivan@example.com"


def test_email_passed_at_resolve_backfills_existing_customer(db):
    """If the identity already exists but customer.email was empty, providing
    email on a later resolve_or_create call backfills it."""
    ext_id = _eid("tg")
    c1 = resolve_or_create_customer(db, channel="telegram", external_id=ext_id)
    assert c1.email is None

    c2 = resolve_or_create_customer(
        db, channel="telegram", external_id=ext_id, email="ivan@example.com"
    )
    assert c2.id == c1.id
    assert c2.email == "ivan@example.com"


# ============================================================================
# Scenario 3 — Same email cross-channel → links to same customer
# ============================================================================


def test_same_email_cross_channel_links_to_same_customer(db):
    """Lead writes from TG, gives email → customer A.
    Same email shows up via website (different external_id) → must attach
    the website identity to customer A, NOT create a new one."""
    tg_id = _eid("tg")
    website_sid = _eid("sess")

    c1 = resolve_or_create_customer(
        db, channel="telegram", external_id=tg_id, email="ivan@example.com"
    )
    c2 = resolve_or_create_customer(
        db, channel="website", external_id=website_sid, email="ivan@example.com"
    )

    assert c1.id == c2.id, "email anchor must merge across channels"

    channels = sorted(i.channel for i in c2.identities)
    assert channels == ["telegram", "website"]
    assert len(c2.identities) == 2


def test_update_email_merges_two_customers(db):
    """Customer A from TG (no email). Customer B from website (with email).
    Later A gets the same email → A and B must merge into one row, with the
    surviving customer holding both identities."""
    tg_id = _eid("tg")
    website_sid = _eid("sess")
    email = "merge-test@example.com"

    a = resolve_or_create_customer(db, channel="telegram", external_id=tg_id)
    b = resolve_or_create_customer(db, channel="website", external_id=website_sid, email=email)
    assert a.id != b.id  # at this point they are separate

    survivor = update_email(db, a.id, email)

    # Survivor must have BOTH identities; the other row must be gone.
    channels = sorted(i.channel for i in survivor.identities)
    assert channels == ["telegram", "website"]
    assert survivor.email == email


# ============================================================================
# Scenario 4 — Different channel, no email → new separate customer
# ============================================================================


def test_different_channels_no_email_remain_separate(db):
    """No email means no anchor — different (channel, external_id) pairs must
    produce distinct customers."""
    tg_id = _eid("tg")
    ig_id = _eid("ig")

    c1 = resolve_or_create_customer(db, channel="telegram", external_id=tg_id)
    c2 = resolve_or_create_customer(db, channel="instagram", external_id=ig_id)

    assert c1.id != c2.id
    assert c1.email is None
    assert c2.email is None


# ============================================================================
# Lookups
# ============================================================================


def test_find_customer_by_email_returns_none_when_missing(db):
    result = find_customer_by_email(db, "nobody@example.com")
    assert result is None


def test_find_customer_by_identity_returns_customer(db):
    ext_id = _eid("tg")
    created = resolve_or_create_customer(db, channel="telegram", external_id=ext_id)

    found = find_customer_by_identity(db, channel="telegram", external_id=ext_id)
    assert found is not None
    assert found.id == created.id


# ============================================================================
# link_identity edge cases
# ============================================================================


def test_link_identity_idempotent_on_same_customer(db):
    ext_id = _eid("tg")
    customer = resolve_or_create_customer(db, channel="telegram", external_id=ext_id)

    # Calling link_identity for the same pair returns the existing identity
    identity = link_identity(
        db, customer_id=customer.id, channel="telegram", external_id=ext_id
    )
    assert identity.customer_id == customer.id

    # Still only one identity row
    db.refresh(customer)
    assert len(customer.identities) == 1


def test_link_identity_raises_when_pointing_to_other_customer(db):
    ext_id = _eid("tg")
    c1 = resolve_or_create_customer(db, channel="telegram", external_id=ext_id)
    c2 = resolve_or_create_customer(db, channel="website", external_id=_eid("sess"))

    with pytest.raises(ValueError, match="already linked"):
        link_identity(db, customer_id=c2.id, channel="telegram", external_id=ext_id)


# ============================================================================
# resolve_or_create_customer_with_meta — returning-lead flag
# ============================================================================


def test_resolve_with_meta_flags_returning_email_match(db):
    """When email matches an existing Customer (different channel/external_id),
    the meta wrapper returns was_returning_match=True."""
    # First contact via website — fresh customer with email
    c1 = resolve_or_create_customer(
        db, channel="website", external_id="sess_old", email="ada@example.com"
    )
    db.commit()

    # Second contact via website with a new session id but same email — should match c1
    c2, was_returning = resolve_or_create_customer_with_meta(
        db, channel="website", external_id="sess_new", email="ada@example.com"
    )
    db.commit()

    assert c2.id == c1.id
    assert was_returning is True


def test_resolve_with_meta_flags_fresh_lead_as_not_returning(db):
    """Brand new email + channel → was_returning_match=False."""
    _, was_returning = resolve_or_create_customer_with_meta(
        db, channel="website", external_id="sess_brand_new", email="newbie@example.com"
    )
    db.commit()
    assert was_returning is False


def test_resolve_with_meta_known_identity_is_not_returning(db):
    """Same (channel, external_id) lookup returns the same customer but is
    NOT a 'returning' event — it's just normal session continuity."""
    resolve_or_create_customer(db, channel="website", external_id="sess_same", email="x@y.com")
    db.commit()

    _, was_returning = resolve_or_create_customer_with_meta(
        db, channel="website", external_id="sess_same", email="x@y.com"
    )
    assert was_returning is False


def test_resolve_with_meta_no_email_is_not_returning(db):
    """Without email, the wrapper cannot detect a returning match —
    must return False even for a brand-new identity."""
    _, was_returning = resolve_or_create_customer_with_meta(
        db, channel="telegram", external_id="tg_99", email=None
    )
    db.commit()
    assert was_returning is False
