# Умный следующий шаг (Next Action Engine)

Модуль умного планирования: что делать с каждым лидом прямо сейчас. Аналитический слой
над воронкой — не двигает стадии (это задача brain), но даёт оператору готовый черновик
и объяснение.

---

## Где код

| Слой | Файл |
|---|---|
| Логика | `services/next_action.py` |
| Хранение | `db/models.py` → `Conversation.next_action` (JSONB, миграция 020) |
| API | `admin_api.py` → `POST /admin/api/task-board/generate`, `GET /admin/api/task-board` |

---

## Функция generate_next_action

```python
async def generate_next_action(db, conv, cust, llm) -> dict:
    """
    Анализирует переписку и сохраняет в conv.next_action:
      kind  — вид действия (reengage/answer/human/wait/unclear)
      mode  — кто исполнитель (bot_auto/needs_approval/human/wait/unclear)
      label — метка для UI
      draft — черновик (если есть)
      reason — пояснение
    Возвращает dict.
    """
```

Результат записывается непосредственно в `conv.next_action` и коммитится в сессии вызывающего.

---

## Режимы (mode)

| mode | Кто действует | Когда |
|---|---|---|
| `bot_auto` | Бот — отправляет сам | `wa_autonomous==True` И (kind=reengage ИЛИ kind=answer) |
| `needs_approval` | Оператор одобряет черновик | wa_autonomous==False, но есть готовый черновик |
| `human` | Оператор — полностью вручную | Требуется живой контакт: КП, переговоры, звонок |
| `wait` | Никто не действует | Лид недавно обещал ответить / назначена встреча |
| `unclear` | Оператор решает | Непонятная ситуация, нужен контекст |

---

## Виды действий (kind)

| kind | Типичная ситуация | Ожидаемый mode |
|---|---|---|
| `reengage` | Лид замолчал после КП / предложения | bot_auto или needs_approval |
| `answer` | Есть очевидный ответ, нужно ответить | bot_auto или needs_approval |
| `human` | Нужен живой человек | human |
| `wait` | Лид сказал «жди» / скоро встреча | wait |
| `unclear` | Непонятно что делать | unclear |

---

## Связь с task-board

`GET /task-board` в разделе `no_task_leads` возвращает `next_action` поле прямо из БД.
Приоритет в UI: `unclear` → `human` → `reengage` → прочие → без next_action.

`POST /task-board/generate`:
- Берёт до 20 активных диалогов без pending задачи
- Вызывает `generate_next_action` для каждого в **отдельной session_scope**
- Обновляет `conv.next_action` в БД

---

## Связь с черновиками (pending_wa_draft)

Если `kind=answer` или `kind=reengage` и `wa_autonomous==False`:
- `mode = needs_approval`
- `draft` в next_action содержит текст
- Тот же текст записывается в `conv.pending_wa_draft`
- В карточке лида → кнопка «Одобрить» / «Отклонить»

---

## Флаги

| ENV | Влияние на next_action |
|---|---|
| `WA_BRAIN=1` | При входящем вебхуке — запускает brain (analyze_and_advance), который может попутно обновить next_action через refresh_draft |
| `WA_BRAIN_SWEEP=1` | В кроне — дополнительно перебирает лидов и обновляет (выключен по умолчанию) |
| `WA_AUTONOMOUS` (per-conv) | Определяет mode: True → bot_auto, False → needs_approval |

---

## Грабли

**next_action не обновляется автоматически** при смене стадии или новом сообщении — устаревает.
Решение: явный `POST /task-board/generate` или кнопка в задачнике.

**unclear ≠ ошибка** — это сигнал для оператора взять управление. Не стоит спамить
generate для unclear-лидов — нужно человеческое решение.

**LLM не держит коннект**: внутри `generate_next_action` нет LLM-вызовов, держащих
session — функция либо использует переданную сессию для READ, либо вызывает LLM уже
после закрытия читающей сессии. Новые изменения должны сохранять этот паттерн.
