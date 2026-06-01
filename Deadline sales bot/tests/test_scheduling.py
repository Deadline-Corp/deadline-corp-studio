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


def test_live_bug_no_booking_on_clarifying_question():
    """Точный баг из ТГ-теста: вопрос «…только в 11 и 12?» НЕ должен бронировать."""
    thu = S._start_of_local_day(NOW, 3)  # четверг
    thu11 = S.compute_free_slots(NOW, n=1, not_before=thu, hour_min=11, hour_max=11)[0]
    thu12 = S.compute_free_slots(NOW, n=1, not_before=thu, hour_min=12, hour_max=12)[0]
    offered = [thu11, thu12]
    assert S.parse_slot_choice("В четверг тоже только в 11 и 12 ?", offered, NOW) is None
    assert S.parse_slot_choice("Что другого времени нет", offered, NOW) is None
    assert S.parse_slot_choice("Нет", offered, NOW) is None
    assert S.parse_slot_choice("Мне не удобно", offered, NOW) is None
    assert S.parse_slot_choice("Ладно не надо тогда", offered, NOW) is None
    # «в 11 и 12» без вопроса — тоже неоднозначно (два времени).
    assert S.parse_slot_choice("в 11 и 12", offered, NOW) is None


def test_live_bug_explicit_14_is_honored_not_rejected():
    """«Мне удобно в четверг в 14 часов» → 14:00 предлагается, а не отвергается."""
    nb, hmin, hmax = S.parse_time_preference("Мне удобно в четверг в 14 часов", NOW)
    assert S._to_local(nb).weekday() == 3            # четверг
    assert (hmin, hmax) == (14, 14)                  # явный час подхвачен
    slots = S.compute_free_slots(NOW, n=2, not_before=nb, hour_min=hmin, hour_max=hmax)
    assert slots and S._to_local(slots[0]).hour == 14
    assert S._to_local(slots[0]).weekday() == 3


def test_choose_slot_by_weekday():
    """Выбор по дню недели, когда слоты различаются датой."""
    thu = S._start_of_local_day(NOW, 3)
    fri = S._start_of_local_day(NOW, 4)
    thu14 = S.compute_free_slots(NOW, n=1, not_before=thu, hour_min=14, hour_max=14)[0]
    fri14 = S.compute_free_slots(NOW, n=1, not_before=fri, hour_min=14, hour_max=14)[0]
    offered = [thu14, fri14]
    assert S.parse_slot_choice("да четверг", offered, NOW) == thu14
    assert S.parse_slot_choice("давайте в пятницу", offered, NOW) == fri14
    assert S.parse_slot_choice("первый", offered, NOW) == thu14


def test_detect_cancel_intent():
    assert S.detect_cancel_intent("Ладно не надо тогда")
    assert S.detect_cancel_intent("Мне не удобно")
    assert S.detect_cancel_intent("давай перенесём созвон")
    assert S.detect_cancel_intent("давай без созвона пока")
    assert not S.detect_cancel_intent("Мне удобно в четверг в 14 часов")
    assert not S.detect_cancel_intent("да, давайте второй вариант")


def test_affirmative_question_still_books():
    """«можно в 15:00?» и «да, давайте в 13?» — это согласие, должно бронировать."""
    slots = _slots()  # [Пн 13:00, Пн 15:00]
    h0 = S._to_local(slots[0]).hour
    assert S.parse_slot_choice(f"да, давайте в {h0}?", slots, NOW) == slots[0]


def test_reminder_texts_contain_key_info():
    call_at = S.compute_free_slots(NOW + timedelta(days=2), n=1)[0]
    lead = S.lead_reminder_text(call_at, "через час", medium="Zoom")
    admin = S.admin_reminder_text(call_at, "Иван", "через час", medium="Zoom", contact="ivan@x.com")
    assert "Zoom" in lead and S.format_slot_human(call_at) in lead
    assert "Иван" in admin and "Zoom" in admin


def test_format_today_tomorrow_vs_weekday():
    today_slot = S.compute_free_slots(NOW, n=1)[0]  # сегодня (Пн 13:00)
    assert S.format_slot_human(today_slot, NOW).startswith("сегодня в")
    tomorrow_slot = S.compute_free_slots(
        NOW, n=1, not_before=S._start_of_local_day(NOW, 1))[0]
    assert S.format_slot_human(tomorrow_slot, NOW).startswith("завтра в")
    # без now_utc — по дню недели (для напоминаний)
    assert "июн" in S.format_slot_human(today_slot)


def test_preference_tomorrow_morning():
    nb, hmin, hmax = S.parse_time_preference("давайте созвонимся завтра утром", NOW)
    assert nb is not None and (hmin, hmax) == (11, 12)
    slots = S.compute_free_slots(NOW, n=2, not_before=nb, hour_min=hmin, hour_max=hmax)
    assert len(slots) == 2
    tomorrow = S._to_local(nb).date()
    for s in slots:
        loc = S._to_local(s)
        assert loc.date() == tomorrow and 11 <= loc.hour <= 12


def test_preference_weekday_and_evening():
    nb, hmin, hmax = S.parse_time_preference("можно в среду вечером?", NOW)
    assert S._to_local(nb).weekday() == 2  # среда
    assert (hmin, hmax) == (17, 19)


def test_no_preference_returns_none():
    nb, hmin, hmax = S.parse_time_preference("хочу сайт для кофейни", NOW)
    assert nb is None and hmin is None and hmax is None
