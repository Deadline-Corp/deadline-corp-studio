# Day 1 — Walkthrough

*Пошаговая инструкция для запуска Postgres + pgvector + первой миграции.*

---

## Шаг 1 — Решить, где Postgres

| Опция | Стоимость | Когда выбирать |
|---|---|---|
| **Railway Postgres** | $5/мес | Рекомендую: уже там у нас Railway, autoset DATABASE_URL, pgvector pre-installed |
| **Supabase Free Tier** | $0 | Если хочешь сохранить бюджет; pgvector pre-installed; 500 МБ лимит |
| **Локально (Docker)** | $0 | Только для разработки. **НЕ для прода.** |

**Решение:** Railway Postgres. $5/мес — copecks, экономит головную боль.

### Railway Postgres setup

1. Открой свой Railway project (где живёт бот)
2. `+ New` → `Database` → `PostgreSQL`
3. Railway автоматически добавит переменную `DATABASE_URL` в твой web service
4. Зайди в Postgres service → `Data` → `Query` → выполни:
   ```sql
   CREATE EXTENSION IF NOT EXISTS vector;
   CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
   ```
5. Готово.

---

## Шаг 2 — Установка зависимостей

В терминале проекта:

```bash
cd "D:\Projects\Deadline\Deadline sales bot"
venv\Scripts\activate     # Windows
# или: source venv/bin/activate    # Mac/Linux

pip install -r requirements.txt
```

Обнови `.env` локально (для разработки). Если ты будешь тестировать против локального Postgres — поставь Docker:

```bash
docker run -d --name deadline-pg \
  -e POSTGRES_PASSWORD=postgres \
  -e POSTGRES_DB=deadline_bot \
  -p 5432:5432 \
  pgvector/pgvector:pg16

# Подключиться и включить extensions
docker exec -it deadline-pg psql -U postgres -d deadline_bot -c "CREATE EXTENSION IF NOT EXISTS vector; CREATE EXTENSION IF NOT EXISTS \"uuid-ossp\";"
```

В `.env` поставь:
```
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/deadline_bot
```

---

## Шаг 3 — Применить миграцию

```bash
alembic upgrade head
```

Должно вывести что-то типа:

```
INFO  [alembic.runtime.migration] Context impl PostgreSQLImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
INFO  [alembic.runtime.migration] Running upgrade  -> 001_initial_schema, initial schema: customers, channel_identities, conversations, messages, kb_chunks
```

---

## Шаг 4 — Health check

```bash
python scripts/check_db.py
```

Должно вывести:
```
✓ Connection OK
✓ pgvector available

Tables found:
  ✓ channel_identities
  ✓ conversations
  ✓ customers
  ✓ kb_chunks
  ✓ messages

✓ All tables present. Schema ready.
```

---

## Шаг 5 — Деплой миграции на Railway

В Railway добавим автоматический запуск миграции при каждом деплое.

Открой `Dockerfile`, после `RUN python ingest.py` добавь:

```dockerfile
# Применить миграции БД (применятся при старте контейнера, не на build time)
CMD alembic upgrade head && uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}
```

(Заменить старую CMD строку)

Альтернатива: запустить миграцию вручную **один раз** через Railway CLI:
```bash
railway run alembic upgrade head
```

---

## Что делать НЕЛЬЗЯ на этом этапе

- ❌ Не удаляй `chroma_db/` — старый бот ещё работает через него
- ❌ Не меняй `main.py` — он пока продолжает использовать Chroma
- ❌ Не переключай виджет на новый endpoint — это Day 4

---

## Что готово к концу Day 1

- ✅ Postgres работает (Railway или Local Docker)
- ✅ pgvector extension установлено
- ✅ Все 5 таблиц созданы через alembic migration
- ✅ Health check проходит зелёным
- ✅ Старый бот (Chroma-based) продолжает работать БЕЗ изменений

**Никакой production-логики мы пока не тронули.** Следующий шаг — Day 2 — пишем `ingest_pg.py` который наполнит kb_chunks.

---

## Параллельно — Dev #2 в это время делает

1. Регистрирует Telegram бота через `@BotFather`:
   - `/newbot`
   - Имя: `Deadline Sales Manager` или твой вариант
   - Username: `@deadline_sales_bot` или твой вариант
   - Получить токен → положить в `.env` локально и в Railway env vars: `TELEGRAM_BOT_TOKEN=...`
2. Проверяет, что у Meta-приложения есть permissions:
   - Зайти в Facebook Developer Console → твоё приложение → Permissions and Features
   - Проверить статус: `instagram_business_manage_messages` и `pages_messaging`
   - Если **Approved** — отлично, едем дальше
   - Если **Submission Required** — submit немедленно (review идёт 5-10 рабочих дней)
3. Готовит webhook endpoints как заглушки (Day 4 будем заполнять):
   ```python
   # main.py — добавить:
   @app.post("/webhooks/telegram")
   async def telegram_webhook(payload: dict):
       # TODO Day 4
       return {"ok": True}

   @app.post("/webhooks/instagram")
   async def instagram_webhook(payload: dict):
       # TODO Day 4
       return {"ok": True}

   @app.post("/webhooks/messenger")
   async def messenger_webhook(payload: dict):
       # TODO Day 4
       return {"ok": True}
   ```

---

## Если что-то пойдёт не так

| Проблема | Лечение |
|---|---|
| `pip install pgvector` падает | Поставить `psycopg2-binary` отдельно: `pip install psycopg2-binary` |
| `alembic upgrade head` → `extension "vector" does not exist` | Не выполнил Шаг 1.4 — выполни SQL `CREATE EXTENSION vector;` |
| `DATABASE_URL not set` | Не подгрузился `.env` — проверь, что файл в корне проекта, не в подпапке |
| Railway сборка падает на `alembic upgrade head` | Перенеси в CMD (runtime), не в RUN (buildtime). Railway не имеет доступа к Postgres на этапе build |
| `relation "alembic_version" does not exist` | Первый запуск — нормально. Alembic создаст таблицу сам |
