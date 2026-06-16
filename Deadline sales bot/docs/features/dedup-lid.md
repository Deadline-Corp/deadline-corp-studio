# Дедупликация @lid рекламных лидов

WhatsApp рекламные лиды приходят под скрытым peer `@lid` (13+ цифр) вместо телефона.
После разрешения телефона или при совпадении с существующим контактом возникают дубли.
Этот документ описывает полный цикл обнаружения и устранения дублей.

---

## Где код

| Слой | Файл |
|---|---|
| Дедуп + очистка | `services/whatsapp_sync.py` → `dedup_wa_by_phone`, `cancel_orphan_scheduled_actions`, `cleanup_wa_artifacts` |
| Крон-запуск | `services/cron.py` → вызов раз в 10 мин |
| API | `admin_api.py` → `POST /admin/api/whatsapp/dedup` |
| LID-resolve | `main.py` → `_resolve_wa_lid_phone` |

---

## Откуда берутся дубли

**Сценарий:**
1. Лид кликает рекламу → WAHA присылает вебхук с `peer = @lid`
2. `_resolve_wa_lid_phone` → LID API WAHA → телефон → сохраняем в `customer.phone`
3. Тот же лид пишет напрямую из чата WhatsApp → peer = `79001234567@c.us` → **создаётся второй Customer**
4. Теперь два диалога у одного человека с разным peer

Дополнительный источник: `/whatsapp/import-leads` (fable-import) — импорт из внешнего
источника может совпадать с уже созданным @lid-лидом.

---

## dedup_wa_by_phone(db)

**Что делает:**
1. Ищет группы WhatsApp-диалогов с одинаковым `customer.phone` (только цифры, strip)
2. В каждой группе: **оставляет** диалог с более продвинутой стадией воронки
3. Дубли (менее продвинутые) → `conv.status = ARCHIVED`, `conv.archived_at = now()`
4. После этого вызывает `cancel_orphan_scheduled_actions(db)`
5. Логирует: сколько дублей нашлось, какой диалог остался «главным»

**Не трогает:**
- Диалоги без phone (лиды с нераспознанным @lid)
- Уже ARCHIVED/LOST диалоги (только OPEN участвуют в дедупе)

---

## cancel_orphan_scheduled_actions(db)

После дедупа (или независимо) — чистит задачи у ARCHIVED диалогов:

```
scheduled_actions WHERE conversation_id IN (archived convs)
                  AND status IN ('pending', 'processing')
  → status = 'superseded'
```

Это обновляет «Мой день» и Календарь без ручного вмешательства.

---

## cleanup_wa_artifacts()

Дополнительная чистка «фантомных» сообщений WhatsApp:

**Фантомы (assistant без подтверждения):**
- Условие: `role='assistant'`, нет `waha_id`, нет `approved_via`, нет `delivered=True`
- Это черновики, которые бот «приготовил» но не отправил
- Действие: DELETE

**Эхо-дубли (оператор-дубль assistant):**
- Условие: `role='operator'` и тот же текст есть рядом как `role='assistant'`
- Возникает когда WAHA дублирует исходящее сообщение в вебхуке
- Действие: DELETE оператор-дубль

---

## Запуск

**Автоматически — в кроне каждые 10 мин** (`services/cron.py`):
```python
await asyncio.to_thread(dedup_wa_by_phone, db)
await asyncio.to_thread(cleanup_wa_artifacts)
```

**Вручную из Admin UI:**
```
POST /admin/api/whatsapp/dedup
```
Ответ: `{"deduped": N, "cancelled_actions": M}`.

**Только чистка артефактов:**
```
POST /admin/api/whatsapp/clean-phantoms
```

---

## LID API — resolve при входящем вебхуке

**_resolve_wa_lid_phone(peer, session_name):**
- Вызывается если `peer.endswith('@lid')` при новом вебхуке WAHA
- `GET {WAHA_BASE_URL}/api/contacts/about?contactId={peer}&session={session}`
- Из ответа берём `profile.phone` (или `phone` в зависимости от версии WAHA)
- Телефон нормализуется: убираем `+`, пробелы, скобки
- Сохраняется в `customer.phone` и `channel_identity.external_id`

---

## Грабли

**Два @lid у одного лида** — если лид кликал рекламу дважды (разные кампании), WAHA
создаёт разные @lid. У обоих LID API вернёт одинаковый телефон → дедуп сработает.

**LID API недоступен** — WAHA возвращает 404 или ошибку. В этом случае `phone` остаётся
пустым, дедуп по телефону не находит дубль. Решение: повторный вебхук (пересообщение лида)
или ручной ввод телефона в карточке.

**Некорректный архив** — если вручную сменить статус ARCHIVED на OPEN, задачи-орфаны
НЕ восстановятся автоматически. Нужно создать задачу вручную (`POST /tasks`).
