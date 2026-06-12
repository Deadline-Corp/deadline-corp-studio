#!/usr/bin/env bash
# ============================================================================
# Развёртывание КОПИИ системы для нового клиента (free-pilot / founding partner)
#
#   ./deploy/new-client.sh <slug> "<Название бизнеса>"
#   Пример: ./deploy/new-client.sh barbershop "Барбершоп Усы"
#
# Что делает: новый Railway-проект (изолированный: свой Postgres, свои токены,
# свой домен) + деплой текущего кода + печать чек-листа ручных шагов.
# Требует: railway CLI залогинен (RAILWAY_API_TOKEN), запуск из папки бота.
# Себестоимость копии: ~$5-10/мес Railway + копейки LLM.
# ============================================================================
set -euo pipefail

SLUG="${1:?Usage: new-client.sh <slug> \"<Business name>\"}"
BIZ="${2:?Usage: new-client.sh <slug> \"<Business name>\"}"
PROJECT="deadline-sales-${SLUG}"

command -v railway >/dev/null || { echo "railway CLI не найден"; exit 1; }
[ -f "main.py" ] || { echo "Запускайте из папки бота (где main.py)"; exit 1; }

gen() { python -c "import secrets; print(secrets.token_hex(24))"; }

echo "==> 1/6 Новый проект Railway: $PROJECT"
railway init --name "$PROJECT"

echo "==> 2/6 Postgres"
railway add --database postgres

echo "==> 3/6 Секреты клиента (генерируются уникальные)"
ADMIN_TOKEN=$(gen)
TRAIN_TOKEN=$(gen)
TG_SECRET=$(gen)
railway variables \
  --set "TENANT_SLUG=deadline-corp" \
  --set "ADMIN_UI_TOKEN=${ADMIN_TOKEN}" \
  --set "TRAINING_AUTH_TOKEN=${TRAIN_TOKEN}" \
  --set "TELEGRAM_WEBHOOK_SECRET=${TG_SECRET}" \
  --set "CRM_ENABLED=true" \
  --set "CRM_PROVIDER=noop" \
  --set "LOG_LEVEL=INFO" \
  --set 'DATABASE_URL=${{Postgres.DATABASE_URL}}'
echo "    ⚠ OPENROUTER_API_KEY и GROQ_API_KEY добавьте вручную (общие или клиентские):"
echo "      railway variables --set OPENROUTER_API_KEY=sk-..."

echo "==> 4/6 Деплой кода"
railway up --detach

echo "==> 5/6 Публичный домен"
railway domain || true

echo "==> 6/6 ГОТОВО. Дальше по чек-листу docs/FREE_PILOT.md:"
cat <<EOF

  ────────────────────────────────────────────────────────────
  Клиент:        ${BIZ}
  Проект:        ${PROJECT}
  ADMIN_UI_TOKEN (вход владельца в панель — передать клиенту лично):
      ${ADMIN_TOKEN}
  TELEGRAM_WEBHOOK_SECRET (для setWebhook):
      ${TG_SECRET}
  ────────────────────────────────────────────────────────────
  Ручные шаги (≈15 мин):
   1. OPENROUTER_API_KEY (+ GROQ_API_KEY для войсов) → railway variables --set ...
   2. Telegram-бот клиента: @BotFather /newbot → TELEGRAM_BOT_TOKEN,
      TELEGRAM_CHAT_ID (чат уведомлений клиента) → railway variables --set ...
   3. setWebhook на <домен>/webhooks/telegram с secret_token выше
   4. Сайт клиента: <script src="<домен НАШ виджет-хост>/widget.js" defer>
      + window.DEADLINE_BOT_API="<домен>/chat" ПЕРЕД подключением
      + добавить домен сайта клиента в ALLOWED_ORIGINS
   5. Открыть <домен>/admin/ui/ → токен выше → клиент проходит онбординг
      (имя, ниша, демо, обучение) САМ — для этого всё и строилось
   6. Записать проект в deploy/clients.txt (для массовых обновлений)
  ────────────────────────────────────────────────────────────
EOF
