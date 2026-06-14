# DEADLINE Sales OS — СТАТУС ПРОЕКТА (снимок 2026-06-13)

Сводный документ текущего состояния + как продолжать. Связанные доки:
- `PROJECT_VISION_BIBLE_RU.md` — глобальная идея/видение (документация проекта).
- `PROJECT_MASTER_RU.md` — архитектура бота, дебаты, роадмап.
- `MULTITENANT_ONBOARDING_PLAN_RU.md` — план self-service онбординга + стоимость.
- `ARCHITECTURE.md` — карта файлов бота. `HANDOVER_RU.md` — передача товарищу.
- План реализации: `C:\Users\user\.claude\plans\iridescent-plotting-planet.md`.

---

## 1. ЧТО ЭТО
Самонастраиваемая система управления продажами на базе агентов (своя гибкая Sales-OS),
которую владелец настраивает под свою нишу без кода. Первый пилот — Кирил (клининг + ремонт).
Стек: FastAPI + Postgres(pgvector) + HubSpot-зеркало, Railway. Мозг: OpenRouter
(llama-3.3-70b) + опц. Gemini/Groq. Эмбеддинги bge-m3 (локально, ~2-3ГБ RAM).

## 2. ЧТО ПОСТРОЕНО (ветка feature/call-booking)

### Ранее (7 волн, ЗАДЕПЛОЕНО на DEADLINE prod)
Мозг-мультипровайдер+Gemini · WhatsApp-коннектор · Каналы→Настройки · №13 stage_changed
триггер · умная карточка (ленивый контакт) · ICS-календарь · AI-копилот «🧭 Что делать».

### Серия P0–P6 (build по плану; закоммичено, НЕ задеплоено на DEADLINE)
- **P0** — каркас тенанта: `tenants/_template/` + фикс `deploy/new-client.sh`
  (TENANT_SLUG=<slug>, скаффолд, RAILWAY_WORKSPACE для неинтерактива).
- **P1** — пресет ниши «Клининг+Ремонт» (`NICHE_PRESETS['cleaning_repair']`): воронка с
  ветками + поля квалификации (адрес/услуга/дата) + дожим.
- **P2a** — планировщик: ёмкость N параллельно (`sched_capacity_per_slot`) + адрес+Google
  Maps в ICS. **P2b** — настраиваемое рабочее окно (`sched_work_*`, выходные) + перенос→
  уведомить человека.
- **P3a** — эскалация→`manager_chat_id` + уведомление бригаде о визите `crew_chat_id`.
  **P3b** — назначение на сотрудника (миграция 015: `conversations.assigned_member_id`,
  `workspace_members.department`+`telegram_chat_id`) + UI дропдаун + отделы в «Команде».
- **P4** — онбординг конфиг-агент: `/onboarding/generate` (дамп+URL→черновик LLM),
  `/onboarding/apply` (промпт+KB+пресет+цель), `/kb/upload` (рантайм-KB), UI «🪄 Авто-
  настройка» в Настройках. **+ встроено в визард первого входа** (Onboarding.tsx, шаг 3
  из 5: имя→ниша→🪄авто-настройка→каналы→демо+тур; коммит fd9ed26) — владелец настраивает
  бота под себя сразу при входе. Карточка в Настройках остаётся для повторного запуска.
  Обучение целиком: визард 5 шагов + spotlight-тур 11 шагов (Tour.tsx) + плашки HintBar +
  значки «?» (Help). Тур перезапускается из Настроек.
- **P5** — языки настраиваемые (`/languages` + UI, `_active_languages()`) + локализация
  напоминаний RU/EN/TH (`detect_lang`, `lead_reminder_text(lang)`).
- **P6** — постоянные клиенты: `run_due_recurring()` (крон) + кнопка «🔁 Регулярный».

Все новые настройки — за дефолтами (поведение DEADLINE 1:1 пока флаги/чаты пусты).

## 3. ДЕПЛОЙ-СОСТОЯНИЕ
- **DEADLINE prod** (`deadline-sales-bot`, workspace Nikolay, ID 0a9a93a3): живой, на коде
  ДО серии P0–P6 (7 волн задеплоены). Серия P0–P6 НЕ выкачена на прод (по плану — только
  на копии Кирила; на прод выкатим осознанно позже).
- **Копия Кирила (рабочая)** — `deadline-kiril` в workspace **Nikolay** (ID
  3cde1a75-a1da-4883-9593-4d097b2ddeb6), сервис `deadline-kiril` + Postgres. Домен:
  **https://deadline-kiril-production.up.railway.app** (вход в панель: `/admin/ui/`,
  токен в kiril-secrets.txt). Развёрнута с полной серией P0–P6 + миграцией 015. На момент
  снимка — идёт сборка/первый старт.
- **Старая копия (НЕ РАБОТАЕТ, к удалению)** — `deadline-sales-kiril` в workspace **A1exxx**
  (ID c5fa8b30): образ собрался, но рантайм OOM (512МБ free-тариф < 2-3ГБ для bge-m3).
  Перенесли в workspace Ника. Старую можно удалить (с разрешения пользователя).

## 4. СЕКРЕТЫ / ДОСТУПЫ
- Токены копии Кирила (ADMIN_UI_TOKEN для входа в панель и др.): `D:\deadline-bot-local\env\kiril-secrets.txt`.
- Общие: `D:\deadline-bot-local\env\env` (RAILWAY_API_TOKEN, наши OPENROUTER/GROQ).
- Gemini-ключ (для теста умного мозга): в `D:\SMM Easy One\.env` / `D:\Христианский бот\.env`
  (AIzaSy…); на проде НЕ задан (мозг = OpenRouter).

## 5. ИЗВЕСТНЫЕ ОСТАТКИ / TODO
- ⏳ Дождаться старта `deadline-kiril` → пройти онбординг как Кирил (дамп инфо → агент
  настроил) → весь путь: лид→квалификация→бронь визита+адрес→напоминания/эскалация→
  назначение→регулярный клиент. Что всплывёт — править.
- Удалить орфан-копию `deadline-sales-kiril` (workspace A1exxx) — по слову пользователя.
- P5b: формат даты/метки в напоминании пока RU (`format_slot_human`, labels) даже в TH/EN —
  мелкая локализация даты.
- P3b: авто-назначение по правилам + «по прибытии»-напоминание + отметка «приехали» — позже.
- P7 (отложено): мультитенант (один апп, tenant_id, роутинг) + браузер-агент (sandbox).
- Выкат серии P0–P6 на боевой DEADLINE — осознанно, когда обкатаем на копии Кирила.

## 6. КАК РАЗВЕРНУТЬ ЕЩЁ КЛИЕНТА / ПЕРЕ-ДЕПЛОЙ КИРИЛА
- Новый клиент: `RAILWAY_WORKSPACE="<ws>" ./deploy/new-client.sh <slug> "<Бизнес>"` —
  НО CLI 4.x: `railway add --service` и `variables/up` требуют `-s <service>`; проще
  повторить ручную последовательность (см. MemPalace deployment-room): init --name --workspace
  → add -d postgres → add --service <svc> → variables -s <svc> --set … → up --detach -s <svc>
  → domain -s <svc>. После — вернуть привязку папки: `railway link -p 0a9a93a3… -e production
  -s deadline-sales-bot`.
- Пере-деплой Кирила: `railway link -p 3cde1a75… -e production -s deadline-kiril` →
  `railway up --detach -s deadline-kiril` → вернуть привязку на DEADLINE.
