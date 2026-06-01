# -*- coding: utf-8 -*-
"""Тесты пост-гарда ответа (services/reply_polish) — на реальных косяках из живого теста."""
from services import reply_polish as R


def test_mirror_greeting_formal():
    ans = "Привет! Я AI-агент студии Deadline, помогу разобраться."
    out = R.mirror_greeting(ans, "Здравствуйте, нужен сайт", is_first_turn=True)
    assert out.startswith("Здравствуйте!")
    assert "Я AI-агент" in out


def test_mirror_greeting_informal_unchanged():
    ans = "Привет! Чем помочь?"
    assert R.mirror_greeting(ans, "привет", is_first_turn=True) == ans


def test_mirror_greeting_not_first_turn_unchanged():
    ans = "Привет! ещё раз"
    assert R.mirror_greeting(ans, "Здравствуйте", is_first_turn=False) == ans


def test_drop_markup_question_keeps_statement():
    ans = ("своя наценка и автообновление — это интересная задача. "
           "Расскажите, что за шкуры вы планируете продавать и как считать наценку?")
    out = R.drop_bad_questions(ans)
    assert "это интересная задача" in out      # statement про наценку остаётся
    assert "как считать наценку" not in out     # вопрос-выпытывание вырезан


def test_drop_budget_frequency_questions():
    assert R.drop_bad_questions("Ок. На какой бюджет ориентируетесь?") == "Ок."
    assert R.drop_bad_questions("Понял. Как часто обновлять цены?") == "Понял."


def test_drop_reask_when_both_known():
    ans = ("Команда ответит скоро. Давайте продолжим в telegram @deadline_corp. "
           "Как вас зовут и на какой email продублировать?")
    out = R.drop_bad_questions(ans, name_known=True, email_known=True)
    assert "Как вас зовут" not in out
    assert "telegram @deadline_corp" in out     # увод в ТГ (statement) остаётся


def test_keep_contact_ask_when_unknown():
    ans = "Понял задачу. На какой email написать?"
    out = R.drop_bad_questions(ans, name_known=False, email_known=False)
    assert "email" in out                       # email ещё не дали — спрашивать НАДО


def test_polish_full_bad_reply():
    ans = ("Привет! Я AI-агент студии Deadline. своя наценка и автообновление — "
           "это интересная задача. Расскажите, что за шкуры и как считать наценку?")
    out = R.polish(ans, lead_message="Здравствуйте, нужен сайт по продаже шкур",
                   is_first_turn=True)
    assert out.startswith("Здравствуйте!")
    assert "как считать наценку" not in out
    assert "это интересная задача" in out


def test_polish_never_empty():
    ans = "Как считать наценку?"  # единственное предложение — выпытывание
    out = R.polish(ans, is_first_turn=False)
    assert out  # не пусто (лучше неидеально, чем пустой ответ)
