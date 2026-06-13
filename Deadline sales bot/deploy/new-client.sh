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

echo "==> 0/6 Каркас тенанта tenants/${SLUG}/ (из шаблона _template)"
TENANT_DIR="tenants/${SLUG}"
if [ -d "$TENANT_DIR" ]; then
  echo "    уже существует — пропускаю"
else
  [ -d "tenants/_template" ] || { echo "tenants/_template не найден"; exit 1; }
  cp -r tenants/_template "$TENANT_DIR"
  for f in "$TENANT_DIR/config.yaml" "$TENANT_DIR/system_prompt.md"; do
    python - "$f" "$SLUG" "$BIZ" <<'PY'
import sys
path, slug, biz = sys.argv[1], sys.argv[2], sys.argv[3]
s = open(path, encoding="utf-8").read()
s = s.replace("__SLUG__", slug).replace("__DISPLAY_NAME__", biz)
open(path, "w", encoding="utf-8").write(s)
PY
  done
  echo "    создан $TENANT_DIR (config.yaml + system_prompt.md) — нишу донастроит онбординг"
fi

echo "==> 1/6 Новый проект Railway: $PROJECT"
railway init --name "$PROJECT"

echo "==> 2/6 Postgres"
railway add --database postgres

echo "==> 3/6 Секреты клиента (генерируются уникальные) + TENANT_SLUG=${SLUG}"
ADMIN_TOKEN=$(gen)
TRAIN_TOKEN=$(gen)
TG_SECRET=$(gen)
railway variables \
  --set "TENANT_SLUG=${SLUG}" \
  --set "ADMIN_UI_TOKEN=${ADMIN_TOKEN}" \
  --set "TRAINING_AUTH_TOKEN=${TRAIN_TOKEN}" \
  --set "TELEGRAM_WEBHOOK_SECRET=${TG_SECRET}" \
  --set "CRM_ENABLED=true" \
  --set "CRM_PROVIDER=noop" \
  --set "LOG_LEVEL=INFO" \
  --set 'DATABASE_URL=${{Postgres.DATABASE_URL}}'

# Наши общие LLM-ключи на время пилота (если есть в локальном env) — клиент позже сменит на свои.
SHARED_ENV="/d/deadline-bot-local/env/env"
read_key() { [ -f "$SHARED_ENV" ] && grep -E "^$1=" "$SHARED_ENV" | head -1 | cut -d= -f2- | tr -d '"\r'; }
OR_KEY=$(read_key OPENROUTER_API_KEY); GROQ_KEY=$(read_key GROQ_API_KEY); GG_KEY=$(read_key GOOGLE_API_KEY)
[ -n "$OR_KEY" ]   && railway variables --set "OPENROUTER_API_KEY=${OR_KEY}"   && echo "    ✓ OPENROUTER_API_KEY (общий) проставлен"
[ -n "$GROQ_KEY" ] && railway variables --set "GROQ_API_KEY=${GROQ_KEY}"       && echo "    ✓ GROQ_API_KEY (общий) проставлен"
[ -n "$GG_KEY" ]   && railway variables --set "GOOGLE_API_KEY=${GG_KEY}"       && echo "    ✓ GOOGLE_API_KEY (общий) проставлен"
[ -z "$OR_KEY" ]   && echo "    ⚠ OPENROUTER_API_KEY не найден в $SHARED_ENV — добавьте вручную: railway variables --set OPENROUTER_API_KEY=sk-..."

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
