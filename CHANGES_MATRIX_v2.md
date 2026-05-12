# MATRIX v2 — Change Log (2026-05-12)

Все правки этого сеанса — изолированы и могут быть отменены по отдельности.

---

## v2.1 deltas (вторая итерация, тот же день)

После первого прогона пользователь дал три обратных пункта — все исправлены:

### v2.1-α: Decode-эффект теперь срабатывает при скролле вверх тоже
**Файл:** `modes.js`, IntersectionObserver на ~3169.
**Было:** после первого decode элемент уходил в `unobserve` — при возврате в viewport второй раз decode не запускался.
**Стало:** убран `unobserve`, добавлен per-element debounce 1.2с (через `dataset.mxLastDecode`).
**Эффект:** каждое появление в viewport (вверх или вниз) → decode. Дребезга при быстром скролле нет благодаря debounce.
**Откат:** вернуть `observer.unobserve(entry.target)` после `decodeSpan(...)`.

### v2.1-β: Magic-toggle на noir починен по-настоящему
**Файлы:** `modes.js` (enterNoir/exitNoir) + `modes.css` (v2.1 блок).
**Корень бага:** `modes.css:1857` скрывает все прямые потомки `body` в noir-режиме, КРОМЕ перечисленных. `.nav-bar` в исключения не входит → весь nav вместе с кнопкой исчезал. До моих правок кнопку отцепляли в `body` root, и `.magic-toggle` срабатывал по исключению.
**Решение v2.1:**
1. Восстановил detach/re-attach в `enterNoir()` / `exitNoir()`.
2. Пин позиции через CSS: `top: 22px; right: 96px;` (визуально совпадает с тем, где была бы кнопка в nav-bar для других режимов; `right: 96px` оставляет место под partner-овский noir-chrome справа).
**Откат:** удалить v2.1 CSS-блок + раскомментировать v2-detach-removal в modes.js (или оставить как сейчас — это рабочая стабильная конфигурация).

### v2.1-A/B/C/D: Расширенные mint-эффекты "присутствия"
По просьбе «пусть мерцание будет ещё каких-нибудь элементов»:

| ID | Эффект | Где |
|----|--------|-----|
| **A** | Firefly motes — 7 mint-точек мягко мерцают и дрейфуют по viewport | `.mx-firefly`, появляются в matrix |
| **B** | Card mint hover-glow — services / testimonials / process / stats | CSS only, на hover |
| **C** | Stats numbers ambient pulse — числа `12+`, `0`, `100%`, `8` пульсируют mint | `.stats-grid .num` |
| **D** | Section eyebrow + hero label flicker — вторичные подписи дышат | `.section-eyebrow`, `.hero-cta .label`, `.hero-meta .small` |

**Откат:** удалить v2.1 CSS-блок целиком (от заголовка `MATRIX v2.1 EFFECTS` до конца).

### v2.1: Прочее
- `prefers-reduced-motion` guard расширен на новые анимации.
- 7 firefly node'ов создаются один раз в JS, далее работают чистым CSS.

---

## v2.0 (исходные правки)


**Safety tag (полный откат всего):**
```bash
git checkout pre-matrix-v2-2026-05-12 -- modes.js modes.css
# или весь репо:
git reset --hard pre-matrix-v2-2026-05-12
```

---

## 1. Цикл режимов сокращён до 4 позиций

**Файл:** `modes.js`, строки ~11–18.

**Было:**
```js
const MODES = ['matrix', 'island', 'studio', 'noir', 'vault', 'kinetic'];
const CYCLE = [null, 'noir', 'matrix', 'kinetic', 'vault', 'island', 'studio'];
```

**Стало:**
```js
const MODES = ['matrix', 'island', 'noir'];
const CYCLE = [null, 'noir', 'matrix', 'island'];
```

**Эффект:** клик по magic-toggle проходит default → noir → matrix → island → default. Режимы studio / vault / kinetic недостижимы (их CSS и JS остался — на случай возврата).

**Откат:** вернуть массивы из «Было».

---

## 2. Magic-toggle больше не отцепляется в noir

**Файл:** `modes.js`, две точки — `enterNoir()` и `exitNoir()`.

**Было:** в `enterNoir()` кнопка перемещалась в `document.body`, в `exitNoir()` возвращалась.

**Стало:** обе операции закомментированы. Кнопка живёт в `<nav>` всегда.

**Эффект:** на всех 4 рабочих режимах magic-toggle стоит на одном месте — в правой части навигации, рядом с `RU|EN` и кнопкой «Связаться».

**Откат:** раскомментировать оригинальные блоки detach/re-attach в `enterNoir()` и `exitNoir()` (искать строку `// (v2 2026-05-12) Detach removed`).

---

## 3. Decode-on-scroll расширен на весь текст matrix

**Файл:** `modes.js`, массив `HEADLINE_SEL` (~строка 3110).

**Было:** 13 селекторов (только заголовки + статы + сервисы/процесс имена).

**Стало:** ~36 селекторов — добавлен hero, services body + bullets, тестимониалы, контакт, credo-items.

**Эффект:** при скролле в matrix-режиме почти весь текст «дешифруется» при первом появлении в viewport.

**Откат:** заменить расширенный массив обратно на 13 оригинальных селекторов.

---

## 4. Matrix-drip ставится на паузу когда вкладка не активна (рекомендация #9)

**Файл:** `modes.js`, функция `startDrip()` (~строка 3247).

**Было:** drip-цикл сыпал символы независимо от того, видна ли страница.

**Стало:** проверяется `document.hidden` — если вкладка не активна, спавн пропускается, но цикл живёт. На возврате — мгновенное возобновление.

**Эффект:** меньше CPU + батареи фоном.

**Откат:** убрать обе проверки `if (!document.hidden)`.

---

## 5. Семь визуальных эффектов v2 — JS блок

**Файл:** `modes.js`, новый IIFE-блок в самом конце перед закрытием верхнего IIFE.
**Маркер:** `// MATRIX v2 EFFECTS (added 2026-05-12)`

Реализованы:
- **#1** Lens-vignette + chromatic-aberration — JS только монтирует пустые `<div>`, всё остальное CSS.
- **#2** Cinematic letterbox — IntersectionObserver следит за `.manifesto-block / .credo-block.matrix-only`, добавляет `body.mx-letterbox-on`.
- **#3** Type-on-load hero headline — печатает три строки hero по очереди при входе в matrix, после печати исчезает каретка.
- **#4** Cursor-reactive ambient aura — слой 600×600 с mint-радиалом, lerp-следующий за курсором.
- **#6** Glyph-bleed на CTA hover — записывает текущий видимый текст кнопки в `data-mx-bleed`, остальное CSS.
- **#9** см. пункт 4 выше (smart drip pause).

**Откат:** удалить весь блок между `// MATRIX v2 EFFECTS` и закрывающим `})();` верхнего IIFE.

---

## 6. Семь визуальных эффектов v2 — CSS блок

**Файл:** `modes.css`, добавлен в самом конце файла.
**Маркер:** `MATRIX v2 EFFECTS (added 2026-05-12)`

Включает:
- `v2-0`: `.mode-indicator { display: none !important; }` — скрывает чип-индикатор позиции (раньше висел в правом верхнем углу).
- `v2-1`: `.mx-cinema-vignette`, `.mx-cinema-aberration`
- `v2-2`: `.mx-letterbox`, `.mx-letterbox-on` на body
- `v2-3`: `.mx-caret`, `@keyframes mx-caret-blink`
- `v2-4`: `.mx-cursor-aura`
- `v2-6`: `.btn-primary::before` / `.btn-secondary::before` с `attr(data-mx-bleed)`
- `v2-10`: `body { animation: mx-hue-drift 24s ... }` — медленный hue-shift 0°→4°→0° (рекомендация #10)
- `prefers-reduced-motion` guard для всего блока

**Откат:** удалить от строки `MATRIX v2 EFFECTS (added 2026-05-12)` до конца файла.

---

## Полный откат одной командой

```bash
cd /c/Users/user/Downloads/web-dev-studio-site
git checkout pre-matrix-v2-2026-05-12 -- modes.js modes.css
# index.html не менялся
```

## Откат конкретного эффекта

| Эффект | Где | Что делать |
|--------|-----|------------|
| Цикл (пункт 1) | `modes.js` | Вернуть оригинальные `MODES` / `CYCLE` |
| Magic-toggle в noir (пункт 2) | `modes.js` | Раскомментировать detach/re-attach |
| Decode-scope (пункт 3) | `modes.js` | Урезать массив `HEADLINE_SEL` |
| Smart drip pause (пункт 4 / #9) | `modes.js` | Убрать `if (!document.hidden)` |
| Vignette + aberration (#1) | оба файла | В CSS закомментировать `.mx-cinema-*`. В JS — два `document.createElement` блока. |
| Cinematic letterbox (#2) | оба файла | В CSS — `.mx-letterbox*`. В JS — блок с `lbTargets` / `lbIO`. |
| Type-on-load (#3) | `modes.js` | Удалить функции `typeLine` / `runHeroType` и их триггеры |
| Cursor-aura (#4) | оба файла | В CSS — `.mx-cursor-aura`. В JS — блок `auraEl` / `tickAura`. |
| Glyph-bleed (#6) | оба файла | В CSS — `::before` для `.btn-primary/.btn-secondary`. В JS — `bindCtaBleed`. |
| Hue-drift (#10) | `modes.css` | Удалить `mx-hue-drift` keyframe + правило на `html.mode-matrix body`. |
| Hidden mode-indicator (v2-0) | `modes.css` | Удалить `.mode-indicator { display: none !important; }`. |

---

## Что НЕ внедрено (рекомендации, отложенные пользователем)

- **#5** Scroll-driven monoline scan — отложено
- **#7** Тайм-код в углу секций — отложено
- **#8** Film-grain overlay — отложено

---

## Известные риски

1. **Hue-drift на body** — `filter: hue-rotate` на body может слегка замедлить paint на слабых машинах. Если будет лаг — удалить `v2-10` блок.
2. **Type-on-load** — печатает hero при первом входе в matrix-режим. Повторно срабатывает только при перезагрузке страницы (флаг `typedDone`).
3. **Glyph-bleed** использует `z-index: -1` относительно кнопки. Если фон сложный — проверь, что копия видна.
4. **Letterbox** триггерится на `.manifesto-block` + `.credo-block.matrix-only`. Если только один — работает на одном. Если оба — будут активны по очереди.

---

_Safety tag: `pre-matrix-v2-2026-05-12` (создан перед началом работ)._
