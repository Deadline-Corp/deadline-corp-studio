# DEADLINE Sales System

**AI-система управления продажами «одного окна».** Все переписки всех каналов + CRM +
календарь + статусы лидов в одной панели; бот сам квалифицирует и ведёт лида (выясни задачу →
квалификация → созвон), работает на автопилоте / полуручном / ручном режиме. Адаптируется под
любой бизнес сменой воронки + «мозгов» (база знаний). Мультитенант (DEADLINE, Кирил и далее).

**Стек:** Python 3.11 · FastAPI (sync SQLAlchemy + async) · **Postgres + pgvector** · Gemini 2.5
Flash (мозг) · bge-m3 эмбеддинги (локально в Docker) · React admin-ui (`/admin/ui`).
**Каналы:** WhatsApp (WAHA/Green-API) · Telegram · Instagram · Messenger · сайт-виджет.
**Деплой:** Docker → Railway (один инстанс + вотчдог). **CRM-зеркало:** HubSpot (опц.).

> ⚠️ README — только вход. **Авторитетная карта системы:**
> - [docs/features/INDEX.md](docs/features/INDEX.md) — карта по фичам (каждый файл самодостаточен)
> - [docs/ARCHITECTURE_RU.md](docs/ARCHITECTURE_RU.md) — полная архитектура, эндпоинты, ENV, миграции
> - [docs/SYSTEM_AUDIT_2026-06-16.md](docs/SYSTEM_AUDIT_2026-06-16.md) — аудит: что крепко, что дорабатываем
> - [docs/PROJECT_STATUS_RU.md](docs/PROJECT_STATUS_RU.md) — текущий статус, деплой, инциденты

---

## Что внутри (карта)

| Слой | Где |
|---|---|
| Приём сообщений (вебхуки всех каналов) | `main.py` → `/webhooks/*`, `channels/*.py` |
| Мозг (авто-ведение: стадия + созвон + сигнал) | `services/conversation_brain.py`, `services/next_action.py` |
| База знаний (RAG, pgvector) | `services/kb_ingest.py`, `db/vector.py` |
| Воронка / стадии / классификатор | `services/funnel.py`, `services/funnel_store.py` |
| Созвоны / напоминания / календарь | `services/scheduling.py`, `services/scheduled_actions.py` |
| Дедуп/склейка WhatsApp (@lid↔телефон) | `services/whatsapp_sync.py` |
| Снимки конфигурации (откат) | `services/config_snapshot.py` |
| Admin-API (панель) | `admin_api.py` (`/admin/api/*`, Bearer `TRAINING_AUTH_TOKEN`) |
| Панель (React) | `admin-ui/src/` → `/admin/ui` |
| Крон (followups, напоминания, чистка, бэкап) | `services/cron.py` |

---

## Локальная разработка

**Бэкенд** (нужен Postgres с расширением `pgvector` + `DATABASE_URL`):
```bash
cd "Deadline sales bot"
python -m venv venv && venv\Scripts\activate          # Windows (Mac/Linux: source venv/bin/activate)
pip install -r requirements.txt
cp .env.example .env                                   # заполнить DATABASE_URL, GEMINI_API_KEY, токены каналов
alembic upgrade head                                   # миграции
uvicorn main:app --reload --port 8000
curl http://localhost:8000/health                      # → {"ok": true}
```

**Панель** (Vite dev-сервер, проксирует `/admin/api` → localhost:8000):
```bash
cd admin-ui && npm ci && npm run dev
```

---

## Деплой (Railway)

Один инстанс, Docker-образ собирает и бэкенд, и `admin-ui/dist`. Миграции (`alembic upgrade head`)
прогоняются на старте контейнера. Деплой текущей ветки:
```bash
railway up --detach     # из папки «Deadline sales bot», токен в Railway vars
```
Подробности, ENV-флаги, восстановление при висе, WAHA-VPS → [docs/features/deploy-and-ops.md](docs/features/deploy-and-ops.md).

---

## Доступ к панели

`/admin/ui` — вход по Bearer-токену:
- **Owner** — `TRAINING_AUTH_TOKEN` (полный доступ, вкл. промпт/воронку/KB/настройки).
- **Менеджер** — именной токен из раздела «Команда».

---

## Безопасность данных

- **Бэкап БД** — логический дамп (gzip/JSON), авто-ежедневно в Telegram + вручную. См.
  [docs/features/db-backup.md](docs/features/db-backup.md).
- **Снимки конфигурации** — откат настроек на любую версию (Настройки → «🗂 Версии конфигурации»).
  См. [docs/features/config-snapshots.md](docs/features/config-snapshots.md).
- **Экспорт в таблицу** — лиды (CRM) и переписки в CSV (Excel-совместимый).
- **Ребайнд WhatsApp** — база привязана к телефону ЛИДА, не к номеру бота → смена номера не теряет
  базу (процедура — в db-backup.md).

---

## Лицензия / Контакты

Internal — Deadline, не для публикации. · Telegram [@deadline_corp](https://t.me/deadline_corp) · corpdeadline@gmail.com
