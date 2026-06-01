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


def test_strip_embedded_markup_clause_keeps_rest():
    # Точная фраза из живого теста sess_web_03: зонд «и как считать наценку» вшит
    # в середину хорошего предложения — режем ТОЛЬКО клаузу, остальное живёт.
    ans = ("Расскажите, что за шкуры вы планируете продавать и как считать наценку — "
           "детали можно спокойно разложить с командой на созвоне.")
    out = R.drop_bad_questions(ans)
    assert "как считать наценку" not in out.lower()
    assert "что за шкуры вы планируете продавать" in out
    assert "разложить с командой на созвоне" in out


def test_strip_clause_does_not_touch_statement():
    # «своя наценка и автообновление» — это summary-statement, НЕ трогаем.
    ans = "Понял — каталог с автоподтягиванием, своя наценка и автообновление."
    out = R.drop_bad_questions(ans)
    assert "своя наценка и автообновление" in out


def test_drop_budget_frequency_questions():
    assert R.drop_bad_questions("Ок. На какой бюджет ориентируетесь?") == "Ок."
    assert R.drop_bad_questions("Понял. Как часто обновлять цены?") == "Понял."


def test_drop_reask_when_both_known():
    ans = ("Команда ответит скоро. Давайте продолжим в telegram @deadline_corp. "
           "Как вас зовут и на какой email продублировать?")
    out = R.drop_bad_questions(ans, name_known=True, email_known=True)
    assert "Как вас зовут" not in out
    assert "telegram @deadline_corp" in out     # увод в ТГ (statement) остаётся


def test_drop_imperative_reask_no_qmark():
    # Повтор-запрос без «?» («Расскажите, как вас зовут…») — тоже вырезаем.
    ans = ("Команда ответит скоро. Расскажите, как вас зовут и на какой email удобнее "
           "написать, чтобы не потеряться. Давайте в telegram @deadline_corp.")
    out = R.drop_bad_questions(ans, name_known=True, email_known=True)
    assert "как вас зовут" not in out.lower()
    assert "telegram @deadline_corp" in out


def test_drop_false_telegram_record():
    # Лид сказал «я напишу в телеграм» (без ника) → «записал ваш телеграм» — ложь, режем.
    ans = ("Отлично, записал ваш телеграм — команда напишет вам в telegram. "
           "Давайте продолжим в telegram @deadline_corp.")
    out = R.polish(ans, lead_message="Ок я напишу в телеграм", is_first_turn=False)
    assert "записал ваш телеграм" not in out.lower()
    assert "@deadline_corp" in out


def test_keep_telegram_record_when_handle_given():
    # Лид реально дал @ник → «записал» правомерно, не трогаем.
    ans = "Записал ваш телеграм, передам команде."
    out = R.polish(ans, lead_message="мой тг @petrov_dev", is_first_turn=False)
    assert "телеграм" in out.lower()


def test_keep_contact_ask_when_unknown():
    ans = "Понял задачу. На какой email написать?"
    out = R.drop_bad_questions(ans, name_known=False, email_known=False)
    assert "email" in out                       # email ещё не дали — спрашивать НАДО


def test_limit_questions_keeps_last_cta():
    # Кейс сайта: лишний переспрос «какие функции?» + нужный «как вас зовут?» → оставляем последний.
    ans = ("Понял — полноценный сайт. Какие функции обязательно — меню или магазин? "
           "Как вас зовут и на какой email написать?")
    out = R.limit_questions(ans)
    assert "какие функции" not in out.lower()
    assert "как вас зовут" in out.lower()
    assert "понял" in out.lower()


def test_limit_questions_single_unchanged():
    ans = "Понял задачу. Как вас зовут?"
    assert R.limit_questions(ans) == ans


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
