# Фича: База знаний (KB) + RAG

## Что делает
Хранит факты о компании (услуги, кейсы, процесс, цены, УТП, FAQ) и подмешивает
релевантные в ответы лиду — система отвечает предметно («делали для VIP Rental Phuket…»).

## Файлы кода
- `services/kb_ingest.py::ingest_text(source, content)` — чанкует (500/overlap 50),
  эмбеддит (bge-m3), пишет в `kb_chunks` (заменяет чанки этого source).
- `db/vector.py::similarity_search(query, k)` — pgvector-поиск.
- `services/wa_drafts.py::_kb_context` — RAG в промпт черновика.
- `admin_api.py`: `GET /kb` (список источников), `POST /kb/upload {source, content}` (owner),
  `DELETE /kb/{source}` (owner). Конфиг-агент: `/onboarding/generate` + `/apply`.
- Заводская база при деплое: `kb/*.md` → `ingest_pg.py` (TRUNCATE + перезалив).

## Текущая база
`deadline_company.md` (12 чанков) — собрана с deadlinecorp.com. Исходник:
`D:\deadline-bot-local\deadline_kb.md`. Обновить: правишь файл → `POST /kb/upload`.

## Как обновить базу знаний
```
POST /admin/api/kb/upload   (Bearer owner)
{ "source": "deadline_company.md", "content": "<markdown>" }
```
Чанкует и заменяет старую версию этого source. Проверка: `POST /whatsapp/simulate-lead`
с профильным вопросом → ответ должен ссылаться на факт/кейс.

## Грабли
- `wa_drafts` использует KB через `_kb_context` (similarity_search) — добавлено 06-15;
  раньше черновики KB не читали.
- Эмбеддинг — CPU, вызывается через `asyncio.to_thread`, чтобы не блокировать loop.
