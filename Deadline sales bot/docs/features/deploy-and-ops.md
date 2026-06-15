# Фича: Деплой, флаги, эксплуатация

## Деплой (Railway)
- Проект `deadline-sales-bot`, service `9cdf9db1-b9fd-476a-bdf6-ef7ed4c91c6f`, env production.
- Ветка `feature/call-booking`, worktree `D:\deadline-bot-wt-call\Deadline sales bot`.
- **GitHub-пуш НЕ авто-деплоит.** Деплой:
  ```
  $env:RAILWAY_API_TOKEN = (из D:\deadline-bot-local\env\env)
  railway up --service 9cdf9db1-b9fd-476a-bdf6-ef7ed4c91c6f --detach
  ```
- Дождаться выката: `railway status` → `deployment ID` сменился (новый healthy).
- Миграции на старте (`alembic upgrade head`). Если упадёт — Railway оставит старую версию.
- Health: `GET …/health` → 200 + `db_connected`, `llm_provider=gemini`.

## Env-флаги (мгновенно через Railway vars → редеплой)
| Флаг | Что |
|---|---|
| `WA_BRAIN=1` | Умное авто-ведение + движок `wa_drafts` + cron-sweep. Пусто = выкл. |
| `WA_WEBHOOK_PAUSE=1` | Аварийный стоп обработки входящих WhatsApp. |
| `LLM_PROVIDER=gemini` + `GOOGLE_API_KEY` | Мозг = Gemini 2.5 Flash. Убрать → OpenRouter. |
| `WAHA_BASE_URL`/`WAHA_API_KEY`/`WAHA_SESSION` | Коннектор WhatsApp. |
| `TRAINING_AUTH_TOKEN` | Owner-токен входа в панель (полный доступ). |

## Доступ к панели
- Owner: `TRAINING_AUTH_TOKEN`.
- Менеджер/партнёр: `POST /admin/api/team {name}` → токен `mgr_…` (роль manager,
  ограниченный). Партнёр вводит на экране входа. Отключить: `/team/{id}/toggle`.
- См. [panel-and-access.md](panel-and-access.md).

## Если процесс завис (история инцидентов 06-15)
Причина — тяжёлый LLM, держащий DB-коннект (sync SQLAlchemy блокирует event loop).
1. Снять `WA_BRAIN` (или `WA_WEBHOOK_PAUSE=1`) в Railway → редеплой = свежий контейнер.
2. Проверить health 200.
3. **Правило на будущее:** не делать LLM, держа request/session-коннект; новые тяжёлые
   пути — в фон, bounded (`_WA_INBOUND_SEMA`), своя сессия; GET-эндпоинты без LLM.

## WAHA (VPS)
- Contabo `84.247.148.135:3000`, Docker, WAHA 2026.5.1 NOWEB. Секреты —
  `D:\deadline-bot-local\env\waha-secrets.txt`. SSH-ключ `~/.ssh/waha_contabo`.
- Ре-скан QR (backfill истории): logout сессии → `GET /api/default/auth/qr` → скан.

## Антибан (неофициальный номер)
Неофиц. linked-device банят за бурсты/спам. Защита:
- **Троттл отправки** (`main._wa_throttle`): все исходящие сериализованы и разнесены —
  мин 5с между сообщениями + случайный джиттер 0.7-2.2с («как человек»). Вне DB-сессии.
- **Только ответы на входящие** — бот никому не пишет первым (нет рассылок).
- **Уникальные сообщения** — каждый ответ генерится LLM под диалог (не шаблон-клоны).
- Рекомендации: держать запасной номер; не включать полный автопилот на огромный
  поток разом; прогрев нового номера если заведёшь.

## Авто-актуальность панели = WhatsApp
- **`cleanup_wa_artifacts()`** (cron, каждый цикл, только БД — дёшево): чистит
  фантомы (assistant без доставки) + эхо-дубли (operator-эхо нашего исходящего).
- **Полная сверка с WhatsApp** — кнопка «🔄 Подтянуть все переписки» (reconcile,
  тяжёлая, по требованию): WAHA = источник правды, убирает локальное, чего нет в WhatsApp.
- Стартовая пауза крона 60с — не нагружает контейнер на прогреве.

## Диагностика
- `railway logs --service 9cdf9db1-...` — ищи `webhooks/waha`, `brain(bg)`, `_wa_send`, ошибки.
- Прод-данные read-only: `D:\deadline-bot-local\notes\*.py` (DATABASE_URL из env).
- Админ-API: Bearer `TRAINING_AUTH_TOKEN`.
