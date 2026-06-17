# DEADLINE Sales System — карта документации по фичам

Эта папка — «по полочкам». Каждый файл = одна фича, самодостаточный контекст:
что делает, где код, как работает, флаги, грабли. Открыл нужный файл → сразу в теме,
можно править фичу или продолжить в новом контекстном окне, не читая весь проект.

> Система = AI-ботовый центр продаж: видит все переписки, понимает смысл, ведёт лида
> (выясни задачу → квалификация → созвон), работает на автопилоте, держит воронку,
> задачник, календарь, сигналит владельцу. Мозг — Gemini 2.5 Flash / llama-3.3-70b.

> **Конвенция названий (UI и доки):** весь продукт = **«система»**; автоматическая
> AI-часть, которая отвечает/ведёт сама = **«бот»**; человек, работающий внутри
> (отвечает вручную, одобряет, ведёт лида) = **«менеджер»** (он же администратор).
> Поэтому: «задачи бота» + «задачи менеджера»; «Бот ведёт диалог», «Бот предложил».

---

## Фичи

| Файл | О чём | Ключевые файлы кода |
|---|---|---|
| [whatsapp-connector.md](whatsapp-connector.md) | Подключение WhatsApp (WAHA), приём, отправка, @lid рекламные лиды, голос, ack-first вебхуки | `channels/waha.py`, `main.py` |
| [autopilot-and-brain.md](autopilot-and-brain.md) | Режимы (наблюдение/черновик/автопилот), WA_BRAIN, умное авто-ведение (стадия+созвон+сигнал), per-conv «Бот ведёт диалог» | `services/conversation_brain.py`, `main.py` |
| [next-action.md](next-action.md) | Умный следующий шаг: generate_next_action, режимы bot_auto/needs_approval/human/wait/unclear, task-board интеграция | `services/next_action.py` |
| [task-board.md](task-board.md) | CRM-задачник: 4 бакета срочности, лиды без задачи, /task-board/generate, приоритизация | `admin_api.py`, `services/scheduled_actions.py` |
| [reply-engine-drafts.md](reply-engine-drafts.md) | Генерация ответов: черновики, переформулировать, тёплое первое сообщение, тест нового лида | `services/wa_drafts.py`, `admin_api.py` |
| [knowledge-base.md](knowledge-base.md) | База знаний (кейсы/услуги/цены) + RAG в ответах, /kb/upload, pgvector | `services/kb_ingest.py`, `db/vector.py`, `admin_api.py` |
| [funnel-stages.md](funnel-stages.md) | Воронка, кастомные стадии, авто-движение, классификатор лид/не-лид, StageTransition | `services/funnel_store.py`, `services/funnel.py` |
| [calendar-scheduling.md](calendar-scheduling.md) | Созвоны: pending_call_suggestion, _resolve_call_dt, напоминания, часовые пояса, FullCalendar, ICS-фид | `services/scheduling.py`, `services/scheduled_actions.py`, `main.py` |
| [dedup-lid.md](dedup-lid.md) | @lid дедупликация: dedup_wa_by_phone, cancel_orphan_scheduled_actions, cleanup_wa_artifacts, LID API resolve | `services/whatsapp_sync.py`, `main.py` |
| [db-backup.md](db-backup.md) | Бэкап БД: build_export (13 таблиц, gzip/JSON), GET /db-backup, авто-ежедневно в Telegram | `services/db_backup.py`, `admin_api.py` |
| [config-snapshots.md](config-snapshots.md) | Снимки конфигурации (воронка/поля/автоматизации/настройки/промпт) — откат на любую версию; авто-снимок перед каждым изменением | `services/config_snapshot.py`, `admin_api.py`, миграция 021 |
| [activity-log.md](activity-log.md) | Журнал активности (Настройки→Логи): что/как/почему/кто — конфиг-правки, ошибки, отправки, takeover, запуск; фильтры+поиск, хранение 30д | `services/activity_log.py`, `admin_api.py` (/logs), `admin-ui/.../Logs.tsx`, миграция 022 |
| [channels-connect.md](channels-connect.md) | Подключение каналов из панели БЕЗ редеплоя: токены в bot_settings → live settings (override-или-env), webhook-URL копировать, кнопка «Проверить» | `main.py` (apply_channel_settings_overrides), `admin_api.py`, `admin-ui/.../Channels.tsx` |
| [canvas-overview.md](canvas-overview.md) | Обзор-канвас: метрики каналов (new_yesterday/hot/no_task), воронка, задачи, inbox | `admin_api.py` → `/overview` |
| [panel-and-access.md](panel-and-access.md) | Панель (переписки/карточка/сворачивание), имена, доступ owner/менеджер | `admin-ui/src/*`, `admin_api.py` |
| [deploy-and-ops.md](deploy-and-ops.md) | Деплой Railway, env-флаги, восстановление при висе, WAHA VPS, вотчдог | Railway, `services/cron.py`, `main.py` |

---

## Главный архитектурный документ

**[../ARCHITECTURE_RU.md](../ARCHITECTURE_RU.md)** — авторитетная карта системы (обновлена 2026-06-16):
- Общая архитектура (FastAPI + sync SQLAlchemy + вотчдог + крон-воркер)
- Все подсистемы и связи (data flow diagram)
- Каналы (WAHA/@lid/TG/IG/виджет)
- Мозг (analyze_and_advance, _resolve_call_dt, next_action)
- Воронка / стадии / ScheduledActions
- Полный список эндпоинтов (80+)
- ENV-флаги (полная таблица)
- Миграции 001–021
- **Правила безопасной работы** (КРИТИЧНО: никогда не держать коннект при LLM-вызове)

---

## Прочее в docs/

- `PROJECT_STATUS_RU.md` — текущий статус, деплой-состояние, инциденты, TODO (обновлён 2026-06-16)
- `PROJECT_MASTER_RU.md` — архитектурные дебаты, роадмап
- `PROJECT_VISION_BIBLE_RU.md` — идея и видение продукта
- `MULTITENANT_ONBOARDING_PLAN_RU.md` — план self-service онбординга (Кирил и далее)
- `WHATSAPP_SYSTEM_STATUS_RU.md` — детальный статус WhatsApp-части
- `HANDOVER_RU.md` — передача контекста

---

## Как пользоваться (для нового контекстного окна)

1. Открой **[ARCHITECTURE_RU.md](../ARCHITECTURE_RU.md)** — получи полную карту за один файл.
2. Открой нужный файл фичи из таблицы выше → раздел «Где код», «Как работает», «Флаги», «Грабли».
3. Правишь фичу → код в указанных файлах → деплой по [deploy-and-ops.md](deploy-and-ops.md).

> Примечание про ветки: держать одну рабочую ветку (`feature/call-booking`) и
> разделять контекст ДОКАМИ по фичам — практичнее, чем дробить живой код на git-ветки.
> Эта папка и есть «разделение по фичам».
