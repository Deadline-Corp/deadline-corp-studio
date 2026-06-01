# -*- coding: utf-8 -*-
"""Регрессия call-booking движка (services/scheduling) — чистая логика, без БД."""
from datetime import datetime, timedelta, timezone

from services import scheduling as S

UTC = timezone.utc
# Понедельник, 1 июня 2026, 10:00 локального (03:00 UTC).
NOW = datetime(2026, 6, 1, 3, 0, tzinfo=UTC)
TAKEN_14 = datetime(2026, 6, 1, 7, 0, tzinfo=UTC)  # 14:00 локального занято


def _slots():
    return S.compute_free_slots(NOW, taken=[TAKEN_14], n=2)


def test_two_slots_in_window_skipping_taken():
    slots = _slots()
    assert len(slots) == 2
    assert all(S._in_window(s) for s in slots)
    assert all(s != TAKEN_14 for s in slots)
    assert all(s >= NOW + timedelta(hours=S.LEAD_TIME_HOURS) for s in slots)


def test_parse_choice_ordinal_words():
    slots = _slots()
    assert S.parse_slot_choice("давайте первый", slots) == slots[0]
    assert S.parse_slot_choice("второй вариант удобнее", slots) == slots[1]


def test_parse_choice_explicit_time():
    slots = _slots()
    hhmm = S._to_local(slots[1]).strftime("%H:%M")
    assert S.parse_slot_choice(f"можно в {hhmm}?", slots) == slots[1]


def test_parse_choice_unclear_is_none():
    assert S.parse_slot_choice("а попозже можно?", _slots()) is None


def test_detect_call_medium():
    assert S.detect_call_medium("давайте по whatsapp") == "WhatsApp"
    assert S.detect_call_medium("лучше в зуме") == "Zoom"
    assert S.detect_call_medium("google meet ок") == "Google Meet"
    assert S.detect_call_medium("можно в тг") == "Telegram"
    assert S.detect_call_medium("ок, договорились") is None


def test_reminder_schedule_three_offsets_future_call():
    call_at = S.compute_free_slots(NOW + timedelta(days=2), n=1)[0]
    sched = S.reminder_schedule(call_at, NOW)
    assert [label for _, label in sched] == ["завтра", "через 3 часа", "через час"]
    assert all(fire > NOW for fire, _ in sched)


def test_reminder_schedule_drops_past():
    soon = S.compute_free_slots(NOW, n=1)[0]
    sched = S.reminder_schedule(soon, NOW)
    assert all(fire > NOW for fire, _ in sched)


def test_no_false_booking_on_question_turns():
    """Бот не должен «случайно» забронировать, пока лид задаёт вопросы."""
    slots = _slots()
    questions = [
        "а сколько это стоит примерно?",
        "вы делаете мобильные приложения?",
        "а сроки какие?",
        "интересно, расскажите подробнее",
        "нужно 5 страниц и интеграция с CRM",
    ]
    for q in questions:
        assert S.parse_slot_choice(q, slots) is None, f"ложная бронь на: {q!r}"
    # И только явный выбор — бронирует.
    assert S.parse_slot_choice("ок, давайте второй вариант", slots) == slots[1]


def test_reminder_texts_contain_key_info():
    call_at = S.compute_free_slots(NOW + timedelta(days=2), n=1)[0]
    lead = S.lead_reminder_text(call_at, "через час", medium="Zoom")
    admin = S.admin_reminder_text(call_at, "Иван", "через час", medium="Zoom", contact="ivan@x.com")
    assert "Zoom" in lead and S.format_slot_human(call_at) in lead
    assert "Иван" in admin and "Zoom" in admin
