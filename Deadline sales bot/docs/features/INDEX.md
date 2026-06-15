# DEADLINE Sales System — карта документации по фичам

Эта папка — «по полочкам». Каждый файл = одна фича, самодостаточный контекст:
что делает, где код, как работает, флаги, грабли. Открыл нужный файл → сразу в теме,
можно править фичу или продолжить в новом контекстном окне, не читая весь проект.

> Система = WhatsApp-командный центр продаж: видит все переписки, понимает текст и
> голос, ведёт лида (выясни задачу → «от $X» → созвон), работает на автопилоте когда
> включишь, держит воронку/календарь, сигналит владельцу. Мозг — Gemini 2.5 Flash.

## Фичи

| Файл | О чём | Ключевые файлы кода |
|---|---|---|
| [whatsapp-connector.md](whatsapp-connector.md) | Подключение WhatsApp (WAHA), приём, отправка, **@lid рекламные лиды**, голос, ack-first вебхуки | `channels/waha.py`, `main.py` (`_wa_send`, `_resolve_wa_chat_id`, `_process_wa_payload`, webhooks) |
| [autopilot-and-brain.md](autopilot-and-brain.md) | Режимы (наблюдение/черновик/автопилот), `WA_BRAIN`, умное авто-ведение (стадия+созвон+сигнал), per-conv «Ведёт система» | `services/conversation_brain.py`, `main.py` (`_wa_route_answer`, `_brain_for_wa_peer`) |
| [reply-engine-drafts.md](reply-engine-drafts.md) | Генерация ответов: «собери задачу→цена→созвон», тёплое первое сообщение, переформулировать, тест нового лида | `services/wa_drafts.py`, `admin_api.py` (`suggest-reply`, `prepare-drafts`, `simulate-lead`) |
| [knowledge-base.md](knowledge-base.md) | База знаний (кейсы/услуги/цены с сайта) + RAG в ответах | `services/kb_ingest.py`, `db/vector.py`, `admin_api.py` (`/kb*`), `D:\deadline-bot-local\deadline_kb.md` |
| [funnel-stages.md](funnel-stages.md) | Воронка, авто-движение стадий, классификатор лид/не-лид | `services/funnel_store.py`, `services/lead_classifier.py`, `services/funnel.py` |
| [calendar-scheduling.md](calendar-scheduling.md) | Бронь созвонов, напоминания, часовые пояса лида | `services/scheduling.py`, `services/scheduled_actions.py`, `admin_api.py` (`/call`) |
| [panel-and-access.md](panel-and-access.md) | Панель (Переписки/карточка/сворачивание), имена, доступ owner/менеджер/партнёр | `admin-ui/src/*`, `admin_api.py` (`_verify_owner/_verify_member`, `/team`) |
| [deploy-and-ops.md](deploy-and-ops.md) | Деплой Railway, env-флаги, восстановление, WAHA VPS, что делать при висе | Railway, `WA_BRAIN`/`WA_WEBHOOK_PAUSE`, `services/cron.py` |

## Полный обзор системы (одним документом)
[../WHATSAPP_SYSTEM_STATUS_RU.md](../WHATSAPP_SYSTEM_STATUS_RU.md) — единая точка правды по WhatsApp-части.

## Прочее в docs/
- `PROJECT_STATUS_RU.md`, `PROJECT_MASTER_RU.md` — общий статус/архитектура.
- `MULTITENANT_ONBOARDING_PLAN_RU.md` — план мультитенанта (Кирил и др.).

## Как пользоваться (для нового контекстного окна)
1. Открой [INDEX.md](INDEX.md) → выбери фичу.
2. В файле фичи — раздел «Файлы кода» (что открыть), «Как работает», «Флаги», «Грабли».
3. Правишь фичу → код в указанных файлах → деплой по [deploy-and-ops.md](deploy-and-ops.md).

> Примечание про «ветки»: держать одну рабочую ветку (`feature/call-booking`) и
> разделять контекст ДОКАМИ по фичам — практичнее, чем дробить живой код на git-ветки
> (иначе придётся постоянно мёржить). Эта папка и есть «разделение по фичам».
