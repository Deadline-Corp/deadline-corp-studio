# DEADLINE Sales OS — СТАТУС ПРОЕКТА (снимок 2026-06-16)

Сводный документ текущего состояния + как продолжать. Связанные доки:
- `PROJECT_VISION_BIBLE_RU.md` — глобальная идея/видение.
- `PROJECT_MASTER_RU.md` — архитектура бота, дебаты, роадмап.
- `ARCHITECTURE_RU.md` — АВТОРИТЕТНАЯ карта системы (обновлена 2026-06-16): подсистемы, каналы, мозг, эндпоинты, ENV, миграции, правила безопасной работы.
- `MULTITENANT_ONBOARDING_PLAN_RU.md` — план self-service онбординга.
- `HANDOVER_RU.md` — передача контекста.
- План реализации: `C:\Users\user\.claude\plans\iridescent-plotting-planet.md`.

---

## 1. ЧТО ЭТО

Самонастраиваемая система управления продажами на базе агентов (Sales-OS), которую владелец
настраивает под нишу без кода. Первый пилот — Кирил (клининг + ремонт). Стек: FastAPI +
Postgres (pgvector) + опц. HubSpot, Railway. Мозг: OpenRouter (llama-3.3-70b) + опц. Gemini
2.5 Flash. Эмбеддинги bge-m3 (1024-dim, ~2-3 ГБ RAM).

---

## 2. ЧТО ПОСТРОЕНО (ветка feature/call-booking)

### 2.1 Ранние 7 волн — ЗАДЕПЛОЕНО на DEADLINE prod

- WhatsApp-коннектор (WAHA self-hosted) + @lid рекламные лиды + LID API resolve
- Мозг-мультипровайдер: OpenRouter / Gemini / Ollama, fallback
- Все каналы: Telegram (форум-топики), Instagram / Messenger (Meta), Green-API (резерв), website-виджет
- Стадии воронки: кастомные (`pipeline_stages`) + встроенные 8 + `StageTransition` история
- Задачник: `scheduled_actions` (followup_message / call_booked / call_reminder) + крон-воркер
- Admin UI (React/Vite, @xyflow/react, /admin/ui/): карточки, инбокс, задачник, настройки, обучение
- ICS-календарь + FullCalendar
- Дедупликация @lid: `dedup_wa_by_phone` + `cancel_orphan_scheduled_actions` (крон)
- DB-бэкап: `build_export` gzip/JSON 13 таблиц, ежедневно в Telegram (DB_BACKUP_TG=1)
- Вотчдог-поток: пульс >120с → os._exit → Railway перезапускает
- Обзор-канвас: `/overview` с метриками каналов + воронки
- Phase 13 (Returning Lead Memory): recall при возврате лида
- Версионирование промпта: `prompt_versions` (редактирование без деплоя)
- Правила обучения: `training_corrections` + pgvector-поиск при каждом ответе
- Команда: `workspace_members`, роли owner/manager, форум-топики per-lead
- Автоматизации: `automation_rules` «Когда→Если→То», `automation_runs`
- `ProcessedUpdate` — дедуп апдейтов через рестарт
- CRM HubSpot: опционально (CRM_ENABLED=False дефолт), durable очередь `crm_queue`

### 2.2 Серия P0–P6 — ЗАКОММИЧЕНО, задеплоено на DEADLINE prod **НЕ** было (только на Кирила)

- **P0** — мультитенант-каркас: `tenants/_template/`, `deploy/new-client.sh`, TENANT_SLUG
- **P1** — пресет ниши «Клининг+Ремонт»: нишевая воронка, поля квалификации, дожим
- **P2a** — планировщик: ёмкость N параллельно, адрес + Google Maps в ICS
- **P2b** — настраиваемое рабочее окно (`sched_work_*`, выходные), перенос → уведомить человека
- **P3a** — эскалация → `manager_chat_id`, уведомление бригаде `crew_chat_id`
- **P3b** — назначение на сотрудника: миграция 015 (`assigned_member_id`, `department`, `telegram_chat_id`)
- **P4** — онбординг-агент: `/onboarding/generate` (дамп сайта → черновик LLM), `/onboarding/apply`, `/kb/upload`, UI визард 5 шагов + spotlight-тур 11 шагов + HintBar + значки «?»
- **P5** — языки: `/languages` + UI, `_active_languages()`, RU/EN/TH локализация напоминаний
- **P6** — постоянные клиенты: `run_due_recurring()` в кроне, `profile_data['recurrence']`

### 2.3 Серия «call-booking» (feature/call-booking) — последние изменения

- `conversation_brain.analyze_and_advance`: единственный LLM-вызов → JSON → движение стадии + `pending_call_suggestion`
- `_resolve_call_dt`: детерминированный расчёт даты из названия дня ("wed" → конкретная дата)
- `_lead_silent`: защита от фантом-созвонов при молчании лида
- `conversations.pending_call_suggestion` (миграция 019): ждёт подтверждения человека
- `conversations.next_action` (миграция 020): умный следующий шаг (mode/kind/label/draft)
- `GET /task-board` + `POST /task-board/generate`: задачник с next_action-интеграцией
- `conversations.pending_wa_draft` (миграция 016): черновик на одобрение
- `conversations.wa_autonomous` (миграция 017): per-conv «Бот ведёт сам»
- `conversations.wa_classification` (миграция 018): классификация при импорте

### 2.4 SaaS-доводка 2026-06-17 — ЗАДЕПЛОЕНО на DEADLINE prod

Путь «работает у нас» → «можно продавать». План: `~/.claude/plans/quizzical-wiggling-bachman.md`.

- **Надёжность WhatsApp:** `_wa_send` — failover-цепочка WAHA→Green-API→Cloud (падение провайдера не теряет сообщение); runtime-переключатель `bot_settings.wa_provider` без редеплоя.
- **Наблюдаемость:** 12 молчаливых `except:pass` залогированы (потеря данных карточки → warning; best-effort зеркала/парсинг → debug); лог parser-bypass идемпотентности `_seen_inbound`.
- **Telegram / одно окно:** `/simulate-lead` сделан канало-независимым (алиас `/whatsapp/simulate-lead`); онбординг-шаг каналов ведёт в /channels; баннер «подключено N/M каналов».
- **Онбординг до «бот ответил»:** гард preset_key (не 404); виджет «🧪 тест первого лида» в визарде.
- **Revenue-аналитика:** миграция **023** `conversations.deal_value`/`deal_currency`; `POST /conversations/{id}/deal-value`; revenue в `/analytics` (won/pipeline/lost по стадиям/каналам, средний чек); ввод суммы в карточке (под расширенным режимом) + revenue-блок в Аналитике.
- **База знаний:** дедуп одинаковых чанков при ингесте (`kb_ingest`).
- **Win-back (восстановление проигранных):** `cron.plan_winback_tasks` — задача оператору по восстановимым причинам (price/delayed/no_budget, НЕ hard_stop) старше N дней, анти-спам флаг; настройка `winback_after_days` (деф. 0=выкл).
- **Отчёты:** в утренний дайджест — дельта неделя-к-неделе + выручка за 7 дней.
- **UX-правки:** «План бота» в карточке показывается ТОЛЬКО при передаче боту (`wa_autonomous`); проигранные («Не сложилось») скрыты из «Переписок» (видны в «Воронке» / по фильтру стадии; параметр `include_lost`); «Сумма сделки» в карточке — под расширенным режимом.

Теги отката: `approved-2026-06-17-saas-blocks-1-7`, `approved-2026-06-17-inbox-botplan-fixes`.

**Ещё НЕ строим (PLAN-ONLY, ждут решений владельца):** биллинг (зависит от юрлица сбора денег), настоящий мульти-тенант (общая БД + `tenant_id` + RLS — при 4–10 клиентах), автопровижининг, исходящий голос (TTS). Ресёрч и рекомендации — в плане.

---

## 3. ДЕПЛОЙ-СОСТОЯНИЕ (на 2026-06-17)

| Инстанс | URL | Что задеплоено | Статус |
|---|---|---|---|
| **DEADLINE prod** | deadline-sales-bot.up.railway.app | 7 волн + call-booking + фокус-спринт багов + **SaaS-доводка §2.4** (миграция 023, bundle index-CRtSXsbK.js+) | Живой ✅ health 200 |
| **Кирил** | deadline-kiril-production.up.railway.app | Серия P0–P6 + call-booking | Развёрнут, обкатывается |
| ~~deadline-sales-kiril~~ | workspace A1exxx | OOM (512 МБ < bge-m3) | ❌ К удалению (с разрешения) |

**Деплой:**
```bash
railway link -p <project_id> -e production -s <service>
railway up --detach
```
**НЕ** `git push` — CI/CD-хука нет.

---

## 4. ИНЦИДЕНТЫ (важно для безопасной работы)

| Дата | Симптом | Причина | Статус |
|---|---|---|---|
| 2026-06-02 | Event loop завис → вотчдог убил | LLM-вызов держал sync SQLAlchemy коннект | Паттерн зафиксирован в коде + ARCHITECTURE_RU.md §16 |
| 2026-06-15 | Повтор виса | Новый код нарушил то же правило | Правило повторно задокументировано |

**Правило** (подробнее в `ARCHITECTURE_RU.md` §16): НИКОГДА не делать LLM-вызов внутри `with session_scope()`.

---

## 5. СЕКРЕТЫ / ДОСТУПЫ

- **DEADLINE prod** токены: `D:\deadline-bot-local\env\env` (RAILWAY_API_TOKEN, OPENROUTER_API_KEY, GROQ_API_KEY)
- **Кирил** токены: `D:\deadline-bot-local\env\kiril-secrets.txt` (ADMIN_UI_TOKEN для входа в панель)
- **Gemini-ключ** (только для локального теста): `D:\SMM Easy One\.env` или `D:\Христианский бот\.env` (AIzaSy…). На обоих прод-инстансах НЕ задан (мозг = OpenRouter).

---

## 6. ОТКРЫТЫЕ TODO / ИЗВЕСТНЫЕ ОСТАТКИ

- **P5b**: формат даты в напоминаниях (RU-формат даже при lang=EN/TH) — `format_slot_human`, локализация меток
- **P3b** (отложено): авто-назначение по правилам + «по прибытии»-напоминание + отметка «приехали»
- **Удалить** `deadline-sales-kiril` (workspace A1exxx) — требует явного подтверждения
- **Выкат P0–P6 на DEADLINE prod** — осознанно, после обкатки на Кириле
- **P7** (отложено): мультитенант (один апп, tenant_id, роутинг) + браузер-агент (sandbox)
- **WA_BRAIN_SWEEP**: пока выключен (риск пула при длинных LLM-вызовах в кроне). Включать только с измеренным pool timeout.
- Орфан-копия `deadline-sales-kiril` в workspace A1exxx — удалить по разрешению

---

## 7. КАК ДЕПЛОИТЬ / ПОДКЛЮЧИТЬ НОВОГО КЛИЕНТА

**Новый клиент (ручная последовательность):**
```bash
# 1. Инициализировать сервис в Railway (через CLI или UI)
railway init --name <client-slug>
railway add -d postgres -s <client-slug>
railway variables --set "TENANT_SLUG=<slug> LLM_PROVIDER=openrouter ..." -s <client-slug>
railway up --detach -s <client-slug>
railway domain -s <client-slug>

# 2. Вернуть привязку на DEADLINE prod:
railway link -p 0a9a93a3-... -e production -s deadline-sales-bot
```

**Пере-деплой Кирила:**
```bash
railway link -p 3cde1a75-a1da-4883-9593-4d097b2ddeb6 -e production -s deadline-kiril
railway up --detach -s deadline-kiril
# После завершения вернуть привязку DEADLINE:
railway link -p 0a9a93a3-... -e production -s deadline-sales-bot
```

---

*Снимок обновлён: 2026-06-17 (добавлен §2.4 SaaS-доводка). При деплое новых волн — обновить разделы 2 и 3.*
