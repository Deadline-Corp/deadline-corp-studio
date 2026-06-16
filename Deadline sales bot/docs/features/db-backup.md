# Бэкап базы данных

Портативный логический дамп ключевых таблиц в gzip(JSON). Без pg_dump, без новых
зависимостей — только SQLAlchemy core. Хранит все переписки, статусы, стадии, задачи,
правила; восстановим даже при потере Railway-проекта.

---

## Где код

| Слой | Файл |
|---|---|
| Сборка дампа | `services/db_backup.py` → `build_export()` |
| API | `admin_api.py` → `GET /admin/api/db-backup`, `POST /admin/api/db-backup/send-telegram` |
| Авто-запуск | `services/cron.py` → ежедневно при `DB_BACKUP_TG=1` |

---

## build_export()

```python
def build_export() -> tuple[str, bytes]:
    """
    Синхронный обход всех таблиц через SQLAlchemy engine.
    Возвращает (filename, gzip_bytes).
    Вызывать через asyncio.to_thread — это blocking-операция.
    """
```

**Включаемые таблицы (13 штук):**
```
customers, conversations, messages, scheduled_actions, stage_transitions,
bot_settings, training_corrections, workspace_members, crm_events, automations,
automation_runs, processed_updates, kb_chunks
```

**Исключается:** колонка `embedding` (большие векторы bge-m3 — не нужны для восстановления
переписок, сильно раздувают размер).

**Формат файла:** `deadline-backup-YYYYMMDD-HHMM.json.gz`

**Структура дампа:**
```json
{
  "_meta": {
    "generated_at": "2026-06-16T10:00:00Z",
    "format": "deadline-logical-backup-v1",
    "tables": [{"name": "customers", "rows": 142}, ...]
  },
  "tables": {
    "customers": [...],
    "conversations": [...],
    ...
  }
}
```

**Типы данных:** datetime → ISO 8601 строка, UUID → строка, Decimal → float, bytes → null.

---

## Способы получить бэкап

### 1. Скачать вручную (owner only)

```
GET /admin/api/db-backup
Authorization: Bearer <ADMIN_UI_TOKEN>
```
Ответ: `Content-Disposition: attachment; filename=deadline-backup-YYYYMMDD-HHMM.json.gz`

### 2. Отправить в Telegram владельцу

```
POST /admin/api/db-backup/send-telegram
Authorization: Bearer <ADMIN_UI_TOKEN>
```
Отправляет файл в `TELEGRAM_CHAT_ID` (chat владельца). Удобно для быстрого snapshot
перед рискованным деплоем.

### 3. Автоматически раз в день (крон)

При `DB_BACKUP_TG=1` (дефолт включён) крон вызывает `send-telegram` раз в сутки,
около первого цикла после 00:00 UTC.

Пример из `cron.py`:
```python
if settings.DB_BACKUP_TG and _should_run_daily_backup():
    fname, blob = await asyncio.to_thread(build_export)
    await _send_backup_to_telegram(fname, blob, settings)
```

---

## ENV-флаги

| Переменная | Назначение | Дефолт |
|---|---|---|
| `DB_BACKUP_TG` | `1` — авто-бэкап в TG раз в день | `1` |
| `TELEGRAM_CHAT_ID` | Chat ID владельца (куда слать бэкап) | None |
| `TELEGRAM_BOT_TOKEN` | Бот для отправки | None |

При `DB_BACKUP_TG=1` но без `TELEGRAM_CHAT_ID` / `TELEGRAM_BOT_TOKEN` — бэкап не
отправится, ошибка в логах (не роняет весь крон).

---

## Восстановление из бэкапа

Бэкап — логический (JSON, не SQL). Восстановление — скрипт-импорт:

```python
import json, gzip
from db.connection import engine
from sqlalchemy import text

with gzip.open("deadline-backup-20260616-1000.json.gz") as f:
    dump = json.load(f)

with engine.connect() as conn:
    for table, rows in dump["tables"].items():
        for row in rows:
            # INSERT OR IGNORE (уже есть — не трогать)
            conn.execute(text(
                f'INSERT INTO "{table}" VALUES :row ON CONFLICT DO NOTHING'
            ), row=row)
    conn.commit()
```

Эмбеддинги (векторы) придётся пересоздать через `/kb/upload` или `services/kb_ingest.py`.
Остальное (переписки, стадии, задачи, правила) восстанавливается полностью.

---

## Грабли

**Бэкап ≠ полное восстановление Postgres** — эмбеддинги не включены. После восстановления
Knowledge Base нужно переиндексировать.

**Blobbing под ограничения Telegram** — файл бэкапа >50 МБ не пройдёт через Bot API.
При большой базе (миллионы сообщений) сжатие может не помочь. Решение: разбить по таблицам
или перейти на S3/R2 как хранилище.

**Авто-бэкап в кроне** — запускается только если `TELEGRAM_CHAT_ID` и `TELEGRAM_BOT_TOKEN`
заданы. Без них бэкап пропускается тихо (warning в лог).
