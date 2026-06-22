"""Осмысленный слот `due_at` для ЗАДАЧ МЕНЕДЖЕРА (executor='human').

ПРОБЛЕМА (которую это чинит): задачи менеджера («связаться с молчащим лидом»,
«бот затупил — помоги», «вернуть проигранного», цепочки касаний) во ВСЕХ точках
создания ставились на `due_at = datetime.now()` — момент срабатывания крона/правила.
Из-за этого на Календаре они висели в ПРОИЗВОЛЬНУЮ минуту (16:46) и даже ночью, как
будто это встреча в конкретное время. Но это ПОРУЧЕНИЕ «разобраться», а не встреча.

РЕШЕНИЕ: ставим чистый слот в ближайшем рабочем окне менеджера (целый час, 09:00–18:00,
пн–пт). Так задача читается как «сделать сегодня к ~17:00», а не «в 16:46 ночью».

Созвоны (📞), напоминания (⏰) и автономные дожимы бота (🤖) этот хелпер НЕ касается —
у них время осмысленно по своей природе (договорённость / -3ч/-1ч / «отправить сейчас»).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

# Дефолтный пояс менеджера, пока нет персонального (UTC+7 — как везде в проекте: Бангкок/Алматы).
MANAGER_TZ_OFFSET = timedelta(hours=7)
WORK_START_H = 9   # рабочее окно, локальное время менеджера
WORK_END_H = 18


def schedule_for_manager(
    now: datetime | None = None,
    tz_offset: timedelta = MANAGER_TZ_OFFSET,
) -> datetime:
    """Вернуть осмысленный `due_at` (в UTC) для задачи менеджера: ближайший целый час
    в рабочем окне 09:00–18:00 (пн–пт) по локальному времени менеджера.

    Примеры (UTC+7): 16:46 → 17:00 сегодня; 18:30 → 09:00 завтра; 07:10 → 09:00 сегодня;
    пятница 18:30 → понедельник 09:00. Округление вверх до часа убирает «случайную минуту».
    """
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    local = now + tz_offset  # сдвигаем к локальному «настенному» времени менеджера

    # округляем ВВЕРХ до целого часа — это поручение, а не встреча на конкретную минуту
    slot = local.replace(minute=0, second=0, microsecond=0)
    if local.minute or local.second or local.microsecond:
        slot += timedelta(hours=1)

    # клампим в рабочее окно: до 09:00 → 09:00 сегодня; в/после 18:00 → 09:00 след. дня
    if slot.hour < WORK_START_H:
        slot = slot.replace(hour=WORK_START_H)
    elif slot.hour >= WORK_END_H:
        slot = (slot + timedelta(days=1)).replace(hour=WORK_START_H)

    # выходные → ближайший понедельник 09:00
    while slot.weekday() >= 5:  # 5 = суббота, 6 = воскресенье
        slot = (slot + timedelta(days=1)).replace(
            hour=WORK_START_H, minute=0, second=0, microsecond=0)

    return slot - tz_offset  # обратно в UTC (тот же момент времени)
