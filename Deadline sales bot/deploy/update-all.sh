#!/usr/bin/env bash
# ============================================================================
# Обновить ВСЕ клиентские копии текущим кодом (одна ветка → все инстансы).
#
#   ./deploy/update-all.sh            — задеплоить всех из deploy/clients.txt
#   ./deploy/update-all.sh --dry-run  — только показать, кого обновим
#
# deploy/clients.txt: по строке на клиента: <railway_project_id> <slug-комментарий>
# (наш основной прод НЕ включаем — он деплоится отдельно, осознанно).
# Миграции идемпотентны (alembic upgrade head в CMD) — копии догоняют схему сами.
# ============================================================================
set -euo pipefail

LIST="$(dirname "$0")/clients.txt"
[ -f "$LIST" ] || { echo "Нет $LIST — скопируйте clients.txt.example и заполните"; exit 1; }
[ -f "main.py" ] || { echo "Запускайте из папки бота (где main.py)"; exit 1; }

DRY="${1:-}"
ok=0; fail=0
while read -r PROJECT_ID SLUG _; do
  [ -z "${PROJECT_ID}" ] && continue
  case "$PROJECT_ID" in \#*) continue;; esac
  echo "──> ${SLUG:-$PROJECT_ID}"
  if [ "$DRY" = "--dry-run" ]; then continue; fi
  if railway link --project "$PROJECT_ID" >/dev/null 2>&1 && railway up --detach; then
    ok=$((ok+1))
  else
    echo "    ❌ не удалось — проверьте руками"; fail=$((fail+1))
  fi
done < "$LIST"
echo "Готово: обновлено $ok, ошибок $fail"
