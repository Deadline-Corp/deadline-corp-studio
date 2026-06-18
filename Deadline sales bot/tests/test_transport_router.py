"""Tests for services/scheduled_actions._send_by_channel — роутер транспорта.

КОРЕНЬ тихой потери лидов (LeadEngine Шаг 0): раньше дожимы/напоминания слались
жёстко через Telegram независимо от канала → WhatsApp/IG падали в failed. Эти тесты
проверяют, что проактивное сообщение уходит в ПРАВИЛЬНЫЙ канал лида.

Senders мокаются (sys.modules['main'] + monkeypatch channels.*), без сети/БД.
Run: pytest tests/test_transport_router.py
"""

import asyncio
import sys
import types

from services.scheduled_actions import _send_by_channel


def _fake_main(wa_calls, page_token="PTK"):
    """Подменный модуль main с async _wa_send и settings.meta_page_access_token."""
    m = types.ModuleType("main")

    async def _wa(peer, text):
        wa_calls.append((peer, text))
        return True

    m._wa_send = _wa
    m.settings = types.SimpleNamespace(meta_page_access_token=page_token)
    return m


def test_route_whatsapp_uses_wa_send(monkeypatch):
    wa = []
    monkeypatch.setitem(sys.modules, "main", _fake_main(wa))
    ok = asyncio.run(_send_by_channel("whatsapp", "+77012990880", "привет", tg_token="T"))
    assert ok is True
    assert wa == [("+77012990880", "привет")]  # ушло в WhatsApp, НЕ в Telegram


def test_route_telegram_uses_telegram(monkeypatch):
    import channels.telegram as tg
    calls = []

    async def fake_tg(token, chat, text):
        calls.append((token, chat, text))
        return True

    monkeypatch.setattr(tg, "send_telegram_reply", fake_tg)
    ok = asyncio.run(_send_by_channel("telegram", "12345", "hi", tg_token="TOK"))
    assert ok is True
    assert calls == [("TOK", "12345", "hi")]


def test_route_telegram_without_token_fails(monkeypatch):
    # Нет токена → не шлём (False), а не падаем.
    ok = asyncio.run(_send_by_channel("telegram", "12345", "hi", tg_token=None))
    assert ok is False


def test_route_messenger_uses_page_token(monkeypatch):
    import channels.messenger as mg
    monkeypatch.setitem(sys.modules, "main", _fake_main([]))
    calls = []

    async def fake_mg(token, rid, text):
        calls.append((token, rid, text))
        return True

    monkeypatch.setattr(mg, "send_messenger_reply", fake_mg)
    ok = asyncio.run(_send_by_channel("messenger", "PSID123", "hi", tg_token="T"))
    assert ok is True
    assert calls == [("PTK", "PSID123", "hi")]  # взял meta_page_access_token


def test_route_website_has_no_proactive_transport():
    # На website проактивной досылки нет → False (не «успех», но и не вечный ретрай).
    ok = asyncio.run(_send_by_channel("website", "x", "hi", tg_token="T"))
    assert ok is False


def test_route_accepts_enum_channel(monkeypatch):
    # ScheduledAction.channel хранится как ChannelEnum — .value должен обработаться.
    from db.models import ChannelEnum
    wa = []
    monkeypatch.setitem(sys.modules, "main", _fake_main(wa))
    ok = asyncio.run(_send_by_channel(ChannelEnum.WHATSAPP, "+7701", "hi", tg_token="T"))
    assert ok is True
    assert wa == [("+7701", "hi")]
