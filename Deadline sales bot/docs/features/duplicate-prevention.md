# Защита от дублей сообщений (все каналы)

Под видение «одно окно» (WhatsApp + Telegram + Instagram + Messenger + сайт в одной панели)
дубли недопустимы — иначе хаос. Здесь — полная архитектура: как дубли НЕ появляются и как
вычищаются, если всё-таки проскочили.

## Три уровня защиты

### 1. Идемпотентность приёма (не создаём дубль в источнике)
- Telegram — `_seen_update_db` / таблица `processed_updates` (`main.py`).
- WhatsApp/Green/Cloud/Meta — `_seen_inbound` + `_inbound_msg_id` по нативному msg-id
  (waha_id / greenapi_msg_id / wamid / mid / comment_id). Ретрай вебхука → дубль не пишется.

### 2. Эхо нашей же отправки (WhatsApp-специфика)
WAHA (NOWEB) отражает НАШИ исходящие обратно как `fromMe`. Автономная реплика бота
сохранялась как `assistant` БЕЗ `waha_id` → эхо приходило с `waha_id` и не «узнавалось».
- `main._record_wa_operator_message` (`main.py`): при эхо-матче по тексту (последние 20 реплик)
  **штампует `waha_id`+`delivered` на строку бота** и НЕ вставляет новую (back-fill — корень дублей).
- `whatsapp_sync.reconcile_wa_conversation`: перед вставкой WAHA-сообщения без совпадения по
  `waha_id` ищет недавнюю (±180с) строку `assistant`/`operator` с тем же текстом без `waha_id` →
  «забирает» её, а не плодит дубль.

### 3. Чистка-страховка (если дубль уже в БД)
Гоняется **в кроне каждые ~10 мин** (`cron.run_wa_maintenance`) И **по кнопке** «🔄 Синхронизировать
сейчас» (Настройки → расширенные, `POST /maintenance/dedup`) И при открытии карточки (авто-резинк):
- `cleanup_wa_artifacts` (`whatsapp_sync.py`): фантомы (бот сгенерил, не отправил, >15 мин) +
  эхо-дубли по тексту (assistant+operator, любой длины ЛИБО все-«наши» ≥15 симв — короткие «Хорошо»
  не трогаем) + дедуп по **суффиксу message-id** (operator+operator, user+user).
- `dedup_messages_global` (`whatsapp_sync.py`): **КАНАЛ-НЕЗАВИСИМЫЙ** дедуп по нативному message-id
  (`_msg_id_key`: waha-суффикс/telegram_msg_id/wamid/mid/greenapi/comment_id) — оставляет самую
  раннюю запись. Покрывает ВСЕ каналы → новый канал не принесёт дубль-хаос.
- `merge_wa_split` + `dedup_wa_by_phone` — склейка РАЗОРВАННЫХ карточек одного человека (@lid + @c.us).

## Почему суффикс message-id, а не текст
Суффикс (часть waha_id после последнего `_`) = глобально-уникальный WhatsApp message-id. Одинаковый
суффикс = буквально ОДНО сообщение (даже под разными префиксами @lid/@c.us). Текст-дедуп — лишь для
эха, где у одной из копий ещё нет id.

## Что возвращают функции (для логов/кнопки)
- `reconcile_wa_conversation` → `{added, restamped, deduped, fetched, chat_id}`.
- `cleanup_wa_artifacts` → `{phantoms, echo_dupes, sfx_dupes}`.
- `dedup_messages_global` → `{removed}`.
- `POST /maintenance/dedup` → `{removed_dupes, merged_cards, detail}`.

## Грабли
- НЕ держать DB-коннект во время сети (reconcile: короткая сессия → сеть вне сессии → короткая сессия).
- Ручные SQL-вставки сообщений ломают идемпотентность — писать только через ORM/хелперы.
- Дедуп оставляет САМУЮ РАННЮЮ запись (стабильно, идемпотентно — повторный проход без чурна).

## Связано
[dedup-lid.md](dedup-lid.md) — склейка @lid/@c.us карточек, скрытый номер.
[reply-engine-drafts.md](reply-engine-drafts.md), [autopilot-and-brain.md](autopilot-and-brain.md).
