"""Гейт авто-стадии «мозга» (conversation_brain.clamp_brain_stage).

Закрывает баг: спам/офтоп улетал в КП. Проверяем, что:
- нерелевантный лид по воронке не двигается;
- бот не прыгает в серьёзные стадии (КП/аванс/сдано) — максимум «квалифицирован»;
- обычное движение вперёд работает; назад/на месте — нет.
"""
from services.conversation_brain import clamp_brain_stage


def test_irrelevant_lead_never_advances():
    assert clamp_brain_stage("in_dialog", "proposal", False) is None
    assert clamp_brain_stage("new_lead", "qualified", False) is None
    assert clamp_brain_stage("new_lead", "in_dialog", False) is None


def test_no_jump_to_proposal_from_below_qualified():
    # из in_dialog / new_lead нельзя прыгнуть в КП — максимум «квалифицирован»
    assert clamp_brain_stage("in_dialog", "proposal", True) == "qualified"
    assert clamp_brain_stage("new_lead", "prepayment", True) == "qualified"
    assert clamp_brain_stage("new_lead", "completed_won", True) == "qualified"


def test_bot_never_sets_serious_stages_itself():
    # из qualified/on_call бот сам в КП не двигает — это человек
    assert clamp_brain_stage("qualified", "proposal", True) is None
    assert clamp_brain_stage("on_call", "proposal", True) is None
    assert clamp_brain_stage("qualified", "prepayment", True) is None


def test_normal_forward_allowed():
    assert clamp_brain_stage("new_lead", "in_dialog", True) == "in_dialog"
    assert clamp_brain_stage("in_dialog", "qualified", True) == "qualified"
    assert clamp_brain_stage("new_lead", "qualified", True) == "qualified"


def test_no_backward_or_same():
    assert clamp_brain_stage("qualified", "in_dialog", True) is None
    assert clamp_brain_stage("in_dialog", "in_dialog", True) is None
    assert clamp_brain_stage("on_call", "qualified", True) is None
