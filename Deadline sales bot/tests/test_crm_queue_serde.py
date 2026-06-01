# -*- coding: utf-8 -*-
"""Round-trip сериализации payload CRM-очереди (для durable-persistence/recovery).
Проверяем: closures выкидываются, dataclasses+datetime кодируются JSON-safe и
точно восстанавливаются."""
import json
from datetime import datetime, timezone

from services import crm_queue as Q
from services.crm.base import Deal, Lead


def test_payload_roundtrip_drops_callables_keeps_dataclass_and_datetime():
    deal = Deal(lead_id="c1", conversation_id="cv1", title="Иван — web", stage="qualified")
    payload = {
        "deal": deal,
        "contact_id": "pending",
        "conversation_id": "cv1",
        "on_deal_id": lambda x: None,                       # closure → выкинуть
        "next_meeting_at": datetime(2026, 6, 4, 7, 0, tzinfo=timezone.utc),
    }
    ser = Q._serialize_payload(payload)
    assert "on_deal_id" not in ser                          # callable выкинут
    json.dumps(ser)                                         # JSON-safe (не падает)

    dec = Q._decode(ser)
    assert isinstance(dec["deal"], Deal)
    assert dec["deal"].title == "Иван — web" and dec["deal"].stage == "qualified"
    assert dec["contact_id"] == "pending"
    assert dec["next_meeting_at"] == datetime(2026, 6, 4, 7, 0, tzinfo=timezone.utc)


def test_lead_roundtrip_with_datetime_field():
    lead = Lead(
        id="c1", contact_name="Пётр", contact_handle="@p", channel="telegram",
        channel_user_id="123", first_message_at=datetime(2026, 6, 1, 10, 0, tzinfo=timezone.utc),
        source_url=None,
    )
    out = Q._decode(Q._serialize_payload({"lead": lead}))
    assert isinstance(out["lead"], Lead)
    assert out["lead"].contact_name == "Пётр"
    assert out["lead"].first_message_at == datetime(2026, 6, 1, 10, 0, tzinfo=timezone.utc)
