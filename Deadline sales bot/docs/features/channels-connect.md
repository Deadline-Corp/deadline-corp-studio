# Подключение каналов из панели (без редеплоя)

Раньше токены каналов задавались только в Railway → Variables (нужен доступ к Railway +
редеплой). Теперь их можно ввести прямо в панели **Настройки → Каналы**: статус, webhook-URL
для копирования, поля токенов, кнопка «Проверить». Применяется **без редеплоя**.

---

## Как это устроено (безопасно, без правки 50+ мест чтения)

Токены каналов читаются в коде в десятках мест как `settings.telegram_bot_token` и т.п.
Рефакторить каждое — рискованно. Вместо этого:

1. Токен сохраняется в **`bot_settings`** (whitelist `KNOWN_KEYS`, `services/bot_settings.py`).
2. **`main.apply_channel_settings_overrides()`** подтягивает override-значения в **живой
   `settings`-объект** (`setattr`): на старте процесса и сразу после сохранения в панели.
3. Все существующие `settings.<token>` автоматически видят новое значение. Ноль изменений в
   путях приёма/отправки.

**Откат к env:** при импорте снимается `_CHANNEL_ENV_BASELINE` (исходные env-значения). Каждый
`apply` ставит ключ = override (если задан) ИЛИ env-baseline → **очистка поля в панели корректно
возвращает к Railway-переменной**, без застревания на старом значении.

**Webhook-СЕКРЕТЫ НЕ редактируются в панели** (`telegram_webhook_secret`, `meta_app_secret`,
`whatsapp_app_secret`) — это fail-closed подпись вебхуков, runtime-своп опасен. Остаются env-only;
панель лишь показывает, заданы ли они.

---

## Где код

| Слой | Файл |
|---|---|
| Whitelist ключей каналов | `services/bot_settings.py` → `KNOWN_KEYS` |
| Применение override → settings | `main.py` → `apply_channel_settings_overrides`, `_CHANNEL_OVERRIDE_KEYS`, `_CHANNEL_ENV_BASELINE` (вызов в `startup`) |
| API (owner-only) | `admin_api.py` → `GET /channels/config`, `POST /channels/{id}/config`, `POST /channels/{id}/test` |
| UI | `admin-ui/src/pages/Channels.tsx` → `ChannelConfig` в карточке каждого канала |

---

## Эндпоинты

- `GET /channels/config` — на канал: поля {key, label, set, **masked** (last4), source=env/панель}, webhook-path;
  + какие webhook-секреты заданы. Сами токены НЕ отдаются (только маска).
- `POST /channels/{channel}/config` `{values:{key:"…"|"" }}` — сохранить (пусто = стереть override →
  env); перед сохранением авто-снимок конфигурации (`reason=auto:channel:*`); затем применить в живой settings.
- `POST /channels/{channel}/test` — живая проверка без отправки клиентам:
  - telegram → `getMe` (вернёт @username)
  - whatsapp → WAHA session status (WORKING) или Green-API `getStateInstance` (authorized)
  - instagram/messenger → Meta Graph `/me` (вернёт имя страницы)

## Поля по каналам

| Канал | Поля (редактируемые) |
|---|---|
| telegram | `telegram_bot_token`, `telegram_operator_group_id` |
| whatsapp | WAHA: `waha_base_url`/`waha_api_key`/`waha_session` · Cloud API: `whatsapp_token`/`whatsapp_phone_number_id` · Green-API: `greenapi_id_instance`/`greenapi_api_token` |
| instagram / messenger | `meta_page_access_token`, `meta_verify_token` |

---

## Как пользоваться

Настройки → Каналы → карточка канала → блок «🔌 Подключить в панели»:
1. Скопируй **Webhook URL** и вставь в кабинете провайдера (BotFather/Meta App) — пошаговая
   инструкция «как получить токен» рядом, в раскрывашке «Как подключить».
2. Введи токен(ы) → **«💾 Сохранить токены»** (применяется сразу).
3. **«🔍 Проверить»** → ✅/⚠️ с деталью.

---

## Безопасность / грабли

- Только owner. Токены наружу не отдаются (маска last4). 🔒 ссылку/токены не пересылать.
- `bot_settings` JSONB не шифруется (как и все настройки) — приемлемо для v1; шифрование секретов
  каналов — задел на будущее.
- Webhook-секреты менять в панели нельзя (см. выше) — для них Railway + перерегистрация вебхука.
- При рестарте процесса override-значения восстанавливаются из `bot_settings` на старте (не теряются).
