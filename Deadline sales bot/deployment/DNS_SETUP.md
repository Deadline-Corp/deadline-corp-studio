# Настройка deadlinecorp.com → GitHub Pages

Пошаговый план перевода сайта `deadline-corp.github.io/deadline-corp-studio/` на собственный домен `deadlinecorp.com`.

---

## Шаг 1 — Купить домен

Открой одно из:

- **Cloudflare Registrar** (рекомендую) — https://dash.cloudflare.com → Domain Registration
- **Porkbun** — https://porkbun.com
- **Namecheap** — https://namecheap.com

Цена `deadlinecorp.com`:
- Cloudflare: ~$10.44/год (at-cost)
- Porkbun: ~$11/год
- Namecheap: ~$13/год (часто скидки)

**Не покупай через GoDaddy** — renewal будет в 2-3 раза дороже.

Privacy WHOIS — включи (на Cloudflare и Porkbun включён по умолчанию).

---

## Шаг 2 — Добавить файл `CNAME` в репозиторий сайта

В репо `deadline-corp-studio` (где лежит сайт):

```bash
cd /path/to/deadline-corp-studio
echo "deadlinecorp.com" > CNAME
git add CNAME
git commit -m "add custom domain"
git push
```

**Важно:**
- Имя файла — ровно `CNAME` (заглавными, без расширения)
- Внутри — одна строка: `deadlinecorp.com` без `https://`, без `www`, без `/`
- Должен лежать в **корне** репозитория, не во вложенной папке

Готовый файл — `deployment/CNAME` в этом проекте, можешь скопировать его.

---

## Шаг 3 — Custom domain в GitHub Pages

1. https://github.com/deadline-corp/deadline-corp-studio (или как у тебя называется)
2. Settings → Pages
3. **Custom domain:** введи `deadlinecorp.com` → **Save**
4. **Enforce HTTPS:** пока не включай — DNS ещё не готов. Включишь на Шаге 6.

GitHub запишет custom domain и попытается сделать DNS check — пока он будет failing, это нормально.

---

## Шаг 4 — DNS записи

Зайди в DNS-управление у того регистратора, где купил домен (если Cloudflare — DNS → Records).

### Если регистратор — Cloudflare

Добавь следующие записи. **КРИТИЧНО: Proxy status = DNS only (серое облако), не Proxied.** GitHub Pages не работает через Cloudflare Proxy.

```
Type    Name    Content                       Proxy       TTL
A       @       185.199.108.153              DNS only    Auto
A       @       185.199.109.153              DNS only    Auto
A       @       185.199.110.153              DNS only    Auto
A       @       185.199.111.153              DNS only    Auto
AAAA    @       2606:50c0:8000::153          DNS only    Auto
AAAA    @       2606:50c0:8001::153          DNS only    Auto
AAAA    @       2606:50c0:8002::153          DNS only    Auto
AAAA    @       2606:50c0:8003::153          DNS only    Auto
CNAME   www     deadline-corp.github.io      DNS only    Auto
```

(`@` означает apex / корневой домен `deadlinecorp.com`)

### Если регистратор — Porkbun / Namecheap

Те же записи, только UI другой:
- В Porkbun: Manage → DNS Records → Add Record
- В Namecheap: Domain List → Manage → Advanced DNS → Add New Record

В колонке "Host" вместо `@` иногда нужно написать `deadlinecorp.com` или оставить пустым — зависит от регистратора.

### Что делает каждая запись

- **4 × A записи** — apex домен `deadlinecorp.com` → IPv4 GitHub Pages
- **4 × AAAA записи** — apex → IPv6 (всё больше клиентов на IPv6)
- **1 × CNAME `www`** — `www.deadlinecorp.com` → `deadline-corp.github.io` (редирект на apex GitHub сделает сам)

---

## Шаг 5 — Подождать DNS propagation

От 5 минут до 24 часов. Обычно — 15-60 минут.

Проверка из терминала:

```bash
dig deadlinecorp.com +short
# Должно вернуть:
# 185.199.108.153
# 185.199.109.153
# 185.199.110.153
# 185.199.111.153

dig www.deadlinecorp.com +short
# Должно вернуть:
# deadline-corp.github.io.
# 185.199.108.153
# ...
```

Или онлайн: https://www.whatsmydns.net/#A/deadlinecorp.com

---

## Шаг 6 — Включить HTTPS

После того как DNS заработал (Шаг 5 показывает правильные IP'шки):

1. GitHub → Settings → Pages
2. Дождись зелёной галки **"DNS check successful"**
3. Поставь галку **Enforce HTTPS**

GitHub автоматически выпустит Let's Encrypt сертификат за 5-30 минут. После этого сайт открывается **только** по HTTPS, HTTP редиректит на HTTPS.

---

## Шаг 7 — Финальная проверка

```bash
# Apex
curl -I https://deadlinecorp.com
# Должно: HTTP/2 200, Server: GitHub.com

# www → apex
curl -I https://www.deadlinecorp.com
# Должно: HTTP/2 301, Location: https://deadlinecorp.com/

# HTTP → HTTPS
curl -I http://deadlinecorp.com
# Должно: HTTP/2 301, Location: https://deadlinecorp.com/
```

Открой в браузере https://deadlinecorp.com — должен открыться сайт, в адресной строке зелёный замок.

---

## Шаг 8 — Обновить бот

Когда домен заработает, обнови переменные на Railway (Project → Variables):

```
ALLOWED_ORIGINS=https://deadlinecorp.com,https://www.deadlinecorp.com,https://deadline-corp.github.io,http://localhost:5500
```

(GitHub Pages адрес оставляем на пару недель — на случай если где-то ещё ссылается)

В `widget.js` на сайте (если api URL не относительный) проверь что бэкенд бота вызывается по абсолютному URL — переезд домена сайта на бот не влияет, если виджет указывает на Railway URL бота напрямую.

---

## Что НЕ делать

- ❌ Не включай Cloudflare Proxy (оранжевое облако) на A/AAAA записях — GitHub Pages не выпустит SSL
- ❌ Не делай `CNAME @` apex — apex домен не может быть CNAME (RFC). Только A/AAAA. CNAME — только для поддоменов типа `www`
- ❌ Не клади CNAME файл во вложенную папку — должен быть в корне репо
- ❌ Не пиши в CNAME файле `https://deadlinecorp.com` или `www.deadlinecorp.com` — только `deadlinecorp.com`
- ❌ Не убирай Custom domain в GitHub после настройки — GitHub удалит CNAME файл, придётся восстанавливать

---

## Типичные грабли

| Симптом | Причина | Лечение |
|---|---|---|
| `DNS check failed` в GitHub | DNS ещё не пропагировался | Подожди до 24ч, потом сделай "Remove" и "Save" custom domain заново |
| Сайт открывается, но HTTPS не работает | Cloudflare Proxy включён | Выключи (серое облако) |
| `www.deadlinecorp.com` не работает | Нет CNAME записи на `www` | Добавь `CNAME www → deadline-corp.github.io` |
| Apex `deadlinecorp.com` 404 | Не все 4 A-записи прописаны | Должны быть ВСЕ четыре IP'шки |
| Виджет на сайте ругается на CORS | Не обновил Railway variables | Обнови `ALLOWED_ORIGINS` |
| После клика на ссылку с www сайт ушёл на github.io | CNAME файл утерян (часто после merge'а) | Восстанови `CNAME` файл в корне репо |

---

## Стоимость

- Домен: ~$10-13/год
- DNS у Cloudflare: бесплатно
- GitHub Pages: бесплатно
- SSL (Let's Encrypt через GitHub): бесплатно
- **Итого: $10-13/год за весь брендинг**

---

## Чек-лист готовности

- [ ] Купил `deadlinecorp.com`
- [ ] Добавил `CNAME` файл в репозиторий сайта
- [ ] Прописал 4×A + 4×AAAA + 1×CNAME в DNS
- [ ] Указал Custom domain в GitHub Pages
- [ ] DNS propagated (проверил через `dig`)
- [ ] Включил Enforce HTTPS
- [ ] Открыл `https://deadlinecorp.com` — работает
- [ ] Обновил `ALLOWED_ORIGINS` на Railway
- [ ] Виджет на новом домене работает (не CORS error)
