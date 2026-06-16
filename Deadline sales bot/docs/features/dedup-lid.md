# Дедупликация и склейка @lid рекламных лидов

WhatsApp рекламные лиды приходят под скрытым peer `@lid` (13+ цифр) вместо телефона.
Один и тот же человек оказывается под РАЗНЫМИ идентификаторами → расходится на
несколько карточек с разбитой историей. Этот документ описывает корень и фикс.

---

## Корень рассинхрона (кейс Zaal, 2026-06-16)

Один человек = ДВЕ карточки с РАЗНЫМИ `customer_id`:

| Карточка | ключ `channel_conversation_id` | источник |
|---|---|---|
| live | `112919085858958` (**@lid**) | живой вебхук `/webhooks/waha` |
| stale | `995599918177` (**реальный телефон @c.us**) | history-sync из WAHA-стора |

- **Живой вебхук** заводит customer+conversation **по сырому @lid**.
- **History-sync** тянет тот же чат, но под реальным телефоном `@c.us` → **второй
  customer + вторая conversation** с пересекающейся/дублированной историей.
- `resolve_lid_phone` ЗНАЛ реальный номер, но раньше только **косметически
  штамповал** `customer.phone` — никогда не использовал его, чтобы СКЛЕИТЬ личность.
- Панель показывала застрявший фрагмент, а живые сообщения шли в другой.

Прогон `merge_wa_split` (dry-run) на проде 2026-06-16 нашёл **19 разорванных людей**.

---

## Фикс: phone-canonical (реальный телефон = КАНОН)

WhatsApp-карточка ключуется по **реальному телефону**, как только он известен; `@lid`
— временный псевдоним до резолва. Два уровня:

1. **Ingestion (`main._handle_message`)** — WhatsApp-сообщение под `@lid`, если телефон
   контакта уже известен (разрезолвлен/склеен), маршрутизируется в **phone-canonical**
   карточку. При первом резолве вешаем `(whatsapp, телефон)` идентичность на того же
   customer и апгрейдим свежую `@lid`-карточку на phone-ключ → **новые фрагменты не
   возникают**.
2. **`merge_wa_split` (само-исцеление существующих)** — группирует whatsapp-карточки по
   реальному телефону, **переносит сообщения** в канон (дедуп по WA-msg-id: полному И
   суффиксу — у `@lid` и `@c.us` префикс разный, суффикс один), перенацеливает
   идентичности `@lid`+телефон на одного customer, ставит канону phone-ключ, фрагмент
   → `ARCHIVED` (обратимо), осиротевшие задачи → `superseded`. **Идемпотентно.**

---

## Где код

| Слой | Файл |
|---|---|
| Структурная склейка | `services/whatsapp_sync.py` → `merge_wa_split` (главное), `dedup_wa_by_phone`, `dedup_wa_by_name`, `cancel_orphan_scheduled_actions`, `dedup_scheduled_actions`, `cleanup_wa_artifacts` |
| Phone-canonical ingestion | `main.py` → `_handle_message` (WhatsApp-ветка) |
| Крон-запуск | `services/cron.py` → каждый цикл (`merge_wa_split` + дедупы) |
| API | `admin_api.py` → `POST /admin/api/whatsapp/dedup` (`execute=false` → dry-run превью с полем `merge_split`) |
| LID-resolve | `channels/waha.py` → `resolve_lid_phone` (WAHA LID API) |

---

## Откуда берутся дубли (исторически)

1. Лид кликает рекламу → WAHA присылает вебхук с `peer = @lid`
2. `resolve_lid_phone` → LID API WAHA → телефон → раньше только в `customer.phone`
3. History-sync / прямой чат под `@c.us` → **второй Customer + диалог**
4. Два диалога у одного человека с разным peer → `merge_wa_split` их сливает

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
