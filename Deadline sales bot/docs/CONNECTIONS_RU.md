# Карта связей по разделам — Deadline Sales Bot

> **Назначение.** Этот документ отвечает на вопрос «как у каждого раздела работают связи»:
> что раздел читает, что пишет, какая кнопка какой эндпоинт дёргает, какой сервис это
> исполняет и куда уходит сигнал. Это карта **проводки**, а не описание фич.
>
> **Выверено:** 2026-06-19 многоагентным аудитом связей (5 областей, адверсариальная
> верификация каждой находки) + ручной проверкой на проде после деплоя `e7e7d285`.
> Прод: `https://deadline-sales-bot-production.up.railway.app`, ветка `feature/call-booking`.
>
> **Как перепроверить связь самому:**
> ```bash
> TOK=$(railway variables --kv | grep '^TRAINING_AUTH_TOKEN=' | cut -d= -f2-)
> URL=https://deadline-sales-bot-production.up.railway.app
> curl -s "$URL/health"                                   # db + vectorstore
> curl -s "$URL/admin/api/task-board" -H "Authorization: Bearer $TOK"   # доска
> curl -s -G "$URL/admin/api/calendar-events" -H "Authorization: Bearer $TOK" \
>   --data-urlencode "start=2026-06-17T00:00:00+00:00" \
>   --data-urlencode "end=2026-07-03T00:00:00+00:00"      # календарь
> ```

---

## 0. Сквозной поток (с чего всё начинается)

```
ВХОДЯЩЕЕ (WhatsApp/Telegram/Instagram/сайт)
        │  нормализация в MessageRequest, дедуп по нативному message-id
        ▼
  _handle_message (main.py)  ──► персист сообщения, takeover⊻bot гейт
        │                         LLM-ответ (если бот ведёт) / черновик (если ручной)
        ├──► funnel: lead_stage вперёд (hot-path, gated crm_enabled)
        ├──► handoff: контакт собран → бриф оператору в Telegram + handoff_done
        ├──► бронь созвона → scheduled_actions(call_booked + call_reminder) + lead_stage=on_call
        └──► (WA_BRAIN=1) _brain_bg: отдельная сессия, analyze_and_advance
                          двигает стадию / кладёт pending_call_suggestion
        ▼
  scheduled_actions (БД)  ◄── единая таблица «что и когда сделать»
        │   followup_message | call_booked | call_reminder | operator_callback | warming_touch
        ▼
  КРОН (services/cron.py, цикл ~600с)
        ├─ sweep_once: тишина→дожим/lost, прогрев, winback
        ├─ run_due_followups / run_due_call_reminders: реальная отправка по due_at
        ├─ run_wa_maintenance: чистка дублей/фантомов/осиротевших задач
        └─ auto_heal (diagnostics): чинит рассинхроны стадии/брони
        ▼
  ПАНЕЛЬ (admin-ui)  ◄── /task-board, /calendar-events, /conversations …
        Задачник · Календарь · Воронка · Карточка диалога
```

**Единая точка истины для «что делать»** — таблица `scheduled_actions`. И доска, и
календарь, и крон читают её. Стадию (`lead_stage`) держит `conversations`. Бронь
созвона дублируется в `customer.profile_data.booked_call_at` (для кросс-канальности)
и в `scheduled_actions(call_booked)` (для календаря) — за их согласованностью следит
`auto_heal`.

---

## 1. Задачник (`/task-board` ↔ `admin-ui/src/pages/Tasks.tsx`)

**Что читает доска.** Один GET `/admin/api/task-board` (admin_api.py:2902, гард
`_verify_member`) собирает весь экран:

| Поле ответа | Откуда берётся | Что показывает |
|---|---|---|
| `buckets` (overdue/today/tomorrow/week/later) | `scheduled_actions` WHERE status∈(pending,processing), НЕ архив, по `due_at` | задачи по дням |
| `no_task_leads` | активные диалоги (`lead_stage∈_ACTIVE_STAGES`, не архив) БЕЗ pending-задачи | клиенты «без следующего шага» (бледно-красные) |
| `zones.approve_now` | диалоги с `pending_wa_draft` | готовые черновики бота на одобрение |
| `zones.{your_turn,bot_leading,waiting}` | расщепление активных по режиму next_action | **приходят, но в дневной доске НЕ рендерятся** (см. §8) |
| `stuck` | активные, не разобранные, молчат >48ч | зона-стоп-сигнал (приходит, не рендерится) |
| `delivery_failed` | `scheduled_actions` status=failed | авто-сообщения бота, что не дошли |
| `summary` | счётчики всех зон + `no_task_shown` | шапка-цифры |

**Кнопки → эндпоинт → сервис → эффект** (всё выверено, всё работает):

| Кнопка (Tasks.tsx) | Эндпоинт | Что делает |
|---|---|---|
| ✓ Сделано | POST `/scheduled-actions/{id}/done` | статус задачи → done |
| ↪ Перенести | POST `/scheduled-actions/{id}/reschedule` `{due_at}` | новый срок задачи |
| ➕ Задача | POST `/tasks` `{conversation_id,text,due_at,executor:'human'}` | оператор-задача → buckets |
| 🤖 Боту | POST `/conversations/{id}/wa-autonomous` `{on:true}` | автопилот. **⚠️ если есть черновик — отправит лиду сразу** → теперь с подтверждением |
| ✅ Одобрить | POST `/conversations/{id}/wa-draft` `{action:'send'}` | отправить черновик бота лиду |
| 🚫 Отклонить | POST `/conversations/{id}/wa-draft` `{action:'reject'}` | стереть черновик, ничего не шлёт |
| ✗ Не сложилось | POST `/conversations/{id}/stage` `{to_stage:'lost',lost_reason:'delayed'}` | в «Проигран», гасит будущие действия |
| 🤖 Разобрать ботом | POST `/task-board/generate` `{limit:10}` | мозг читает диалоги без шага, ставит next_action/задачу. Гард `_verify_owner` |
| ↻ Обновить | POST `/cron/sweep` | ручной прогон крона. Гард `_verify_owner` |
| Открыть | — | клиентский Drawer, без сети |
| Все задачи → Отменить | POST `/scheduled-actions/{id}/cancel` | статус → cancelled |

**Инвариант доски:** лид попадает в `no_task_leads` ⇔ он активен и у него НЕТ
pending/processing `scheduled_action`. Как только создаётся любая задача (ботом или
человеком) — лид уходит из «Без задачи» на следующем поллинге (20с).

**Защита от тихой потери (фикс 2026-06-19):** запросы подняты 400/500 → 1500,
срез `no_task_leads` 60 → 300, в `summary` добавлен `no_task_shown`, в логи — warning
при упоре в кап, на доску — футер «+N не показаны». Раньше лиды за лимитом исчезали
без следа.

---

## 2. Календарь (`/calendar-events` ↔ `admin-ui/src/pages/Calendar.tsx`)

**Принцип:** в календаре — только то, что требует физического присутствия человека
(созвоны и напоминания), а не задачи дожима. Фильтры по умолчанию:
`{call:true, reminder:true, task:false, bot:false}` (Calendar.tsx).

| Связь | Эндпоинт/сервис | Статус |
|---|---|---|
| Бронь созвона видна в календаре | GET `/calendar-events` читает `scheduled_actions(call_booked)` за диапазон (НЕ `profile_data` — та пропадала при смене стадии) | ✅ |
| Drag-drop созвона → перенос | POST `/conversations/{id}/call` `{action:'reschedule',time}` → `cancel_call_actions` + `write_call_booking` + новые `call_reminder` | ✅ |
| Перенос пересчитывает напоминания | `reminder_schedule` гасит старые, ставит новые из `call_reminder_offsets` (1д/3ч/1ч) | ✅ |
| Напоминания реально шлются | КРОН `run_due_call_reminders` → лиду по его каналу, оператору в Telegram-группу; `FOR UPDATE SKIP LOCKED` от двойной отправки | ✅ |
| Пара лид+админ напоминаний | схлопывается в одно событие по `(conversation_id, due_at)` | ✅ |

**Важно (источники истины созвона):** `booked_call_at` (в `profile_data`, кросс-канально)
и строка `call_booked` (в `scheduled_actions`, для календаря) — два хранилища. При
брони/переносе/отмене обновляются вместе. Если рассинхрон — ловит `auto_heal`.

---

## 3. Воронка / стадии (`conversations.lead_stage`)

**Кто пишет стадию (3 независимых писателя):**

| Писатель | Файл | Когда | Гейт |
|---|---|---|---|
| hot-path funnel | main.py (decide_from_tenant_config) | при разборе входящего | `crm_enabled` |
| brain | conversation_brain.py:619 `analyze_and_advance` | при WA_BRAIN=1, фоном после ответа | forward-only |
| крон | cron.py:719 `sweep_once` | тишина → lost; своя сессия, раз в ~10 мин | `can_auto_transition` |

**Защита от отката (фикс 2026-06-19):** все писатели идут forward-only (`_stage_forward`).
Brain теперь ПЕРЕЧИТЫВАЕТ свежую `lead_stage` из БД перед guard (column-query, READ
COMMITTED) — иначе при WA_BRAIN=1 и двух сообщениях подряд он мог откатить стадию,
которую hot-path только что продвинул.

**UI-смена стадии:** POST `/conversations/{id}/stage` (`validate_transition`,
`operator_override=True`) — оператор двигает в любом направлении; зеркалится в CRM
(HubSpot). Стадия показывается чипом на каждой карточке доски; фильтр «стадия» вверху
доски строится из стадий задач в buckets.

**Активные стадии** (`_ACTIVE_STAGES`) попадают в «Без задачи»; `lost`/архив — нет.
Денежные/юр стадии (`nda/tz_approved/prepayment/in_work`) мозг НЕ трогает
(next_action.py:103) — по ним решает только человек.

---

## 4. Бот ↔ человек (автономия и создание задач)

**Инвариант takeover⊻bot:** если `operator_takeover=True` — `_handle_message`
возвращает пустой ответ, бот молчит (main.py). Если `wa_autonomous=True` — бот шлёт сам.
Для **ручных** лидов (не автопилот) бот НЕ отправляет — только кладёт `pending_wa_draft`
на одобрение.

**Откуда берутся задачи на доске:**

| Источник | Что создаёт | Куда попадает |
|---|---|---|
| крон-нудж (sweep) | `followup_message` executor=bot | buckets (бот сделает сам) |
| черновик дожима ручного лида (крон) | `conversation.pending_wa_draft` | zones.approve_now (на одобрение) |
| человек | `operator_callback` executor=human | buckets |
| мозг «не справляется» (`maybe_create_stuck_task`) | `operator_callback` «🤖 Бот не справляется» | buckets, дедуп по pending operator_callback |
| no-show heal | снимает бронь, `on_call→qualified` | лид возвращается под нудж/в «Без задачи» |
| winback | `operator_callback` executor=human для lost | buckets (без zones-контекста) |

**Каскад takeover** (`set_operator_takeover`): гасит `wa_autonomous` + отменяет pending
бот-нуджи (`followup_message`). `call_reminder` НЕ трогает (созвон всё равно состоится),
но `run_due_call_reminders` сам проверит `wa_autonomous` и не пошлёт лиду напоминание
на ручном перехвате — двойная защита.

**Голос (STT):** входящее голосовое → Groq Whisper (primary) → при сбое fallback на
Gemini (`_transcribe_gemini`, channels/telegram.py). Если транскрипт не удался и стадия
не денежная — создаётся stuck-задача человеку (main.py, отдельная короткая сессия).

---

## 5. Оркестрация (входящее → сигнал оператору)

| Звено | Где | Статус |
|---|---|---|
| Все каналы → единый `_handle_message` | main.py | ✅ |
| Дедуп входящих по нативному message-id (переживает рестарт через `processed_updates`) | main.py | ✅ (см. §7: дыра при отсутствии id) |
| takeover⊻bot гейт | main.py | ✅ |
| Бронь созвона атомарна со стадией | main.py rebook/cancel | ✅ |
| Handoff: бриф оператору + `handoff_done` | main.py | ✅ **(фикс)** обёрнут в try/except: падение Telegram не рушит ответ лиду; `handoff_done` ставится только при доставке (иначе ретрай) |
| Задача → сигнал лиду/оператору | КРОН `run_due_*` | ✅ |
| on_call без брони | ловит `diagnostics.auto_heal` | ✅ (откат через 48ч) |

---

## 6. Крон (`services/cron.py`, цикл ~600с)

Порядок в `_worker_loop` (важен для гонок):
1. `sweep_once` — тишина→дожим/lost, прогрев, winback. **(фикс)** при переводе в lost
   гасит `call_booked`+`call_reminder` в той же сессии — иначе шаг 2 в этом же цикле
   слал напоминание о созвоне уже потерянному лиду.
2. `run_due_recurring` → `run_due_followups` → `run_due_call_reminders` — реальная
   отправка по `due_at` (статус pending→processing→done от двойной отправки).
3. `resolve_lid_backlog` — добор реального телефона @lid-лидов через WAHA.
4. `run_wa_maintenance` — **(фикс)** теперь `await asyncio.to_thread(...)`: раньше
   голый sync-вызов блокировал event loop (~8-10 DB-транзакций) → вебхуки/health висли.
5. `auto_heal` (diagnostics) — чинит рассинхроны стадии/брони.
6. Авто-бэкап БД в Telegram раз в день.

Тот же код доступен вручную: кнопка «↻ Обновить» → POST `/cron/sweep`.

---

## 7. Диагностика / auto_heal (`services/diagnostics.py`)

Запускается каждый цикл крона. Чинит обратимо (статусы/`profile_data`), ничего не удаляет:

| Аномалия | Действие |
|---|---|
| пустой `on_call` >48ч | → `qualified` |
| бронь без стадии on_call | снять бронь |
| no-show (бронь в прошлом >3ч) | снять `call_booked`+`call_reminder`, `on_call→qualified`, StageTransition |
| осиротевшие `call_reminder` (нет `booked_call_at`) | cancelled |

Также `run_wa_maintenance`: чистка фантомов/эхо-дублей, слияние разорванных @lid+@c.us
карточек (кейс Zaal), дедуп по телефону/имени, архив empty-stub карточек, гашение
осиротевших задач.

---

## 8. Что приходит на доску, но НЕ рендерится (осознанно)

Дневная доска показывает: `buckets` (по дням) + `zones.approve_now` + `no_task_leads`
+ `delivery_failed`. А вот `zones.{your_turn,bot_leading,waiting}` и `stuck` бэкенд
считает и отдаёт, но дневная доска их **не выводит** (компонент `Zone` определён, но не
вызывается). Лиды из этих зон не теряются — они же попадают в `no_task_leads`, если у
них нет задачи. Это не баг, а текущий выбор UX (доска перестроена на «дни»). Если
захочется вернуть зоны — данные уже есть, нужен только рендер (см. §10, п.3).

---

## 9. Известные остаточные риски (не чинили — задокументировано почему)

| # | Риск | Severity | Почему отложено / чем смягчено |
|---|---|---|---|
| R1 | Дедуп входящих обходится, если парсер канала не дал нативный message-id (голос/forwarded/business) → дубль ответа при ретрае вебхука | medium | Правка трогает hot-path всех каналов. Смягчение: `dedup_messages_global` в крон-maintenance подчищает дубли постфактум. Фикс: составной fingerprint `sha256(chat+ts+content[:64])` + тесты на фикстурах |
| R2 | `lead_stage='on_call'` ставится ДО успешного `write_call_booking` (он в отдельной транзакции) | low | Ловит `auto_heal` (откат через 48ч). Фикс: поменять порядок — бронь, потом стадия |
| R3 | `/task-board` calls-бакет (старый эндпоинт 2782) и `/calendar-events` расходятся при `on_call→tz_approved` | medium | Дневная доска этим бакетом НЕ пользуется (у неё buckets по дням). Затрагивает только старый эндпоинт. Фикс: расширить фильтр стадий до `_BOOKING_OK_STAGES` |
| R4 | POST `/tasks` executor=bot для Telegram отдаёт 200, но `run_due_followups` молча отменяет (требует `wa_autonomous`, которого у TG нет) | high* | *Из текущего UI НЕДОСТИЖИМО: кнопка «➕ Задача» хардкодит executor='human'. Фикс при добавлении бот-задач из UI: отдельный флаг авторизации канала вместо WA-поля |
| R5 | `warming_touch` — мёртвый тип в `_BOT_ACTIONS`/`TYPE_LABELS` (строк в БД нет, прогрев идёт в CRM) | low | Безвредный мёртвый код. Убрать при следующей чистке |
| R6 | Роль-гейт: `/cron/sweep` и `/task-board/generate` требуют `_verify_owner`, кнопки видны всем | low | На практике не стреляет: вход через `/login` (PANEL_LOGINS) выдаёт owner-токен. Появятся manager-роли — гейтить кнопки по `me.role` |

---

## 10. Бэклог усиления задачника (предложения аудита, приоритет)

Реализовано сегодня: подписи `call_booked`/`call_reminder`, подтверждение «🤖 Боту»,
футер «+N не показаны», убран двойной тост. Осталось (по убыванию пользы):

| # | Улучшение | Польза | Сложность |
|---|---|---|---|
| 1 | Рендер зоны «🧊 Затыки» (`stuck`) на доске | лиды-молчуны перестанут быть невидимыми | S (фронт) |
| 2 | Фильтр по стадии применять и к зонам, не только к buckets | фильтр «воронка» перестанет работать наполовину | M (фронт) |
| 3 | Таблетка «🏷 Без задачи N» в строке фильтров | быстрый прыжок к риску | S (фронт) |
| 4 | Инлайн-просмотр полного черновика в карточке approve_now (без открытия Drawer) | меньше кликов на одобрение | S (фронт+2 стр. бэк) |
| 5 | Метка «обновлено N сек назад» + жёлтый чип при >60с | доверие к свежести доски | S (фронт) |
| 6 | `deal_value` (вес сделки) чипом на карточке | приоритизация по деньгам | M |
| 7 | «через 2ч» рядом с абсолютным временем «когда бот напишет» | временной контекст | S (фронт) |
| 8 | «✓ Сделано» для лида в your_turn без task_id | закрыть дыру UX (когда зоны вернут) | M |

---

*Этот файл — живой. При изменении проводки (нового эндпоинта, переезда источника
истины, смены порядка в кроне) — обновлять соответствующий раздел.*
