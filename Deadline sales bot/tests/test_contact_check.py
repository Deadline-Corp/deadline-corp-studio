"""Unit tests for services/contact_check.py (no network).

The t.me HTML fixtures below are the real Open Graph tags captured on
2026-06-09 from:
  - @saswee21      → does NOT exist (Nikolay confirmed manually)
  - @durov         → exists (Pavel Durov)
  - @deadline_corp → exists (Deadline Team)
"""

import pytest

from services.contact_check import (
    classify_contact,
    _tme_exists_from_html,
)


class TestClassifyContact:
    def test_email(self):
        assert classify_contact("ivan@example.com") == ("email", "ivan@example.com")

    def test_telegram_at_handle(self):
        assert classify_contact("@saswee21") == ("telegram", "saswee21")

    def test_telegram_bare_handle_defaults_to_telegram(self):
        # form label is "Telegram или WhatsApp" → bare handle = Telegram
        assert classify_contact("saswee21") == ("telegram", "saswee21")

    def test_telegram_url(self):
        assert classify_contact("https://t.me/durov") == ("telegram", "durov")

    def test_telegram_url_no_scheme(self):
        assert classify_contact("t.me/deadline_corp") == ("telegram", "deadline_corp")

    def test_instagram_url(self):
        assert classify_contact("https://instagram.com/nasa") == ("instagram", "nasa")

    def test_instagram_hint_ru(self):
        t, _ = classify_contact("инст foo_bar")
        assert t == "instagram"

    def test_phone_intl(self):
        t, v = classify_contact("+66 81 234 5678")
        assert t == "phone"
        assert v.startswith("+")

    def test_phone_plain(self):
        t, _ = classify_contact("89161234567")
        assert t == "phone"

    def test_empty(self):
        assert classify_contact("") == ("unknown", "")

    def test_whitespace_only(self):
        assert classify_contact("   ") == ("unknown", "")


# --- real og-tag fixtures (2026-06-09) ---
_HTML_MISSING = (
    '<meta property="og:title" content="Telegram: Contact @saswee21">'
    '<meta property="og:image" content="https://telegram.org/img/t_logo_2x.png">'
)
_HTML_EXISTS_DUROV = (
    '<meta property="og:title" content="Pavel Durov">'
    '<meta property="og:image" content="https://cdn4.telesco.pe/file/VLmfc7xI">'
)
_HTML_EXISTS_TEAM = (
    '<meta property="og:title" content="Deadline Team">'
    '<meta property="og:image" content="https://cdn5.telesco.pe/file/bkAcC4qT">'
)


class TestTmeParser:
    def test_missing_username_is_false(self):
        assert _tme_exists_from_html(_HTML_MISSING, "saswee21") is False

    def test_existing_user_is_true(self):
        assert _tme_exists_from_html(_HTML_EXISTS_DUROV, "durov") is True

    def test_existing_team_is_true(self):
        assert _tme_exists_from_html(_HTML_EXISTS_TEAM, "deadline_corp") is True

    def test_handle_compare_is_case_insensitive(self):
        # the generic stub matches regardless of how the user typed the handle
        assert _tme_exists_from_html(_HTML_MISSING, "SasWee21") is False

    def test_handle_with_at_prefix(self):
        assert _tme_exists_from_html(_HTML_MISSING, "@saswee21") is False

    def test_unparseable_html_is_none(self):
        assert _tme_exists_from_html("<html><body>nope</body></html>", "x") is None

    def test_default_logo_image_is_missing(self):
        html = (
            '<meta property="og:title" content="Some Title">'
            '<meta property="og:image" content="https://telegram.org/img/t_logo.png">'
        )
        assert _tme_exists_from_html(html, "whoever") is False
