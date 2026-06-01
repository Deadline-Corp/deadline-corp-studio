# -*- coding: utf-8 -*-
"""Симуляция конечного автомата брони — ТОЧНАЯ копия if/elif из main.py
(блок call-booking), но с профилем-словарём вместо БД. Прогоняет провальный
Telegram-диалог пользователя и показывает, какой МАРКЕР получает бот на каждом
ходу + состояние профиля. Цель — доказать многоходовую логику без живого ТГ.

Запуск:  python tests/sim_booking_flow.py
"""
from datetime import datetime, timezone

from services import scheduling as S

UTC = timezone.utc
NOW = datetime(2026, 6, 1, 3, 0, tzinfo=UTC)  # Пн 1 июня, 10:00 локал (Бангкок)


def step(profile, lead_msg_n, content, *, taken=()):
    """Возвращает (marker, details) — точная копия ветвления main.py."""
    now = NOW
    booked = profile.get("booked_call_at")
    offered = []
    for x in (profile.get("offered_call_slots") or []):
        try:
            offered.append(datetime.fromisoformat(x))
        except Exception:
            pass
    # анти-стале
    pref_nb_early, _, _ = S.parse_time_preference(content, now)
    if pref_nb_early is not None and offered:
        pday = S._to_local(pref_nb_early).date()
        if not any(S._to_local(s).date() == pday for s in offered):
            offered = []
            profile.pop("offered_call_slots", None)
    chosen = S.parse_slot_choice(content, offered, now) if offered else None
    wants_cancel = S.detect_cancel_intent(content)
    if chosen is None and not booked and not wants_cancel:
        explicit = S.parse_explicit_datetime(content, now)
        if explicit is not None and not any(S._hour_key(explicit) == S._hour_key(t) for t in taken):
            chosen = explicit

    if booked and wants_cancel:
        profile.pop("booked_call_at", None)
        profile.pop("offered_call_slots", None)
        return "CALL_CANCELLED", "снял бронь + погасил напоминания"
    elif booked:
        m = profile.get("call_medium")
        when = S.format_slot_human(datetime.fromisoformat(booked), now)
        return "CALL_ALREADY_BOOKED", when + (f", {m}" if m else "")
    elif chosen is not None:
        medium = S.detect_call_medium(content) or profile.get("call_medium")
        profile["booked_call_at"] = chosen.isoformat()
        if medium:
            profile["call_medium"] = medium
        profile.pop("offered_call_slots", None)
        return "CALL_BOOKED", S.format_slot_human(chosen, now) + (f", {medium}" if medium else "")
    elif wants_cancel:
        profile.pop("offered_call_slots", None)
        return "CALL_CANCELLED(до брони)", "не навязываю созвон"
    elif lead_msg_n >= 2:
        pref_nb, pref_hmin, pref_hmax = S.parse_time_preference(content, now)
        has_pref = pref_nb is not None or pref_hmin is not None
        valid = [s for s in offered if s > now]
        if valid and not has_pref:
            return "CALL_ASK_TIME", "спрашиваю удобное лиду время (не повторяю список)"
        slots = S.compute_free_slots(now, taken=list(taken), n=2,
                                     not_before=pref_nb, hour_min=pref_hmin, hour_max=pref_hmax)
        if slots:
            profile["offered_call_slots"] = [s.isoformat() for s in slots]
            return "CALL_SLOTS", "; ".join(S.format_slot_human(s, now) for s in slots)
        return "CALL_SLOTS", "(нет слотов)"
    return "(нет маркера — просто ответ)", ""


def run(title, turns):
    print(f"\n{'='*70}\n{title}\n{'='*70}")
    profile = {}
    for n, content in turns:
        marker, details = step(profile, n, content)
        booked = profile.get("booked_call_at")
        bflag = "✅броней нет" if not booked else f"🔴 booked={S.format_slot_human(datetime.fromisoformat(booked), NOW)}"
        print(f"[{n}] ЛИД: {content}")
        print(f"     → бот получает: [{marker}] {details}   | {bflag}")


# Точный провальный диалог пользователя (Telegram):
run("ПРОВАЛЬНЫЙ ДИАЛОГ ИЗ ТЕСТА — теперь с фиксами", [
    (1, "Здравствуйте я писал вам на сайте по поводу сайта доставки суши"),
    (2, "С кем то можно созвониться все обсудить?"),
    (3, "Мне удобно в четверг в 14 часов"),
    (4, "В четверг тоже только в 11 и 12 ?"),
    (5, "Что другого времени нет"),
    (6, "Нет"),
    (7, "Мне не удобно"),
    (8, "Ладно не надо тогда"),
])

# Happy-path: лид нормально выбирает время и канал.
run("HAPPY-PATH — лид выбирает время", [
    (1, "Привет, хочу обсудить проект"),
    (2, "давайте созвонимся"),
    (3, "давайте в четверг в 14"),
    (4, "в whatsapp удобнее"),
    (5, "ок спасибо"),
])
