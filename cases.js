/* ============================================================================
   DEADLINE — CASES PAGE ENGINE
   Рендерит страницу из window.CASES, навешивает интерактив.
   Чистый ванильный JS, без модулей (работает по file:// и на GitHub Pages).
   ============================================================================ */
(function () {
  'use strict';

  var REDUCE = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  var COARSE = window.matchMedia('(pointer: coarse)').matches;
  var CASES = window.CASES || [];
  var FILTERS = window.CASE_FILTERS || [{ key: 'all', label: { ru: 'Все', en: 'All' } }];

  /* ───────── LANG (RU default, EN via ?lang=en / localStorage) ───────── */
  function curLang() {
    var p = new URLSearchParams(location.search).get('lang');
    if (p === 'en' || p === 'ru') return p;
    return localStorage.getItem('lang') === 'en' ? 'en' : 'ru';
  }
  function applyLang(lang) {
    document.body.classList.toggle('lang-en', lang === 'en');
    document.documentElement.lang = lang;
    document.querySelectorAll('.lang-toggle button[data-lang]').forEach(function (b) {
      var on = b.dataset.lang === lang;
      b.classList.toggle('active', on);
      b.setAttribute('aria-pressed', on ? 'true' : 'false');
    });
    localStorage.setItem('lang', lang);
  }
  function wireLang() {
    document.querySelectorAll('.lang-toggle button[data-lang]').forEach(function (b) {
      b.addEventListener('click', function () {
        var lang = b.dataset.lang;
        applyLang(lang);
        var u = new URL(location.href);
        if (lang === 'en') u.searchParams.set('lang', 'en'); else u.searchParams.delete('lang');
        history.replaceState(null, '', u);
      });
    });
  }
  function L(obj) { return obj ? (document.body.classList.contains('lang-en') ? obj.en : obj.ru) : ''; }
  function LL(v) { return (v && typeof v === 'object') ? L(v) : (v || ''); }
  function byId(id) { return CASES.filter(function (c) { return c.id === id; })[0]; }
  function bubbleCls(from) { return from === 'bot' ? 'in' : (from === 'sys' ? 'sys' : 'out'); }

  /* ───────── small SVG icon set for flow diagrams ───────── */
  var ICONS = {
    chains: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 12a3 3 0 0 1 3-3h2a3 3 0 0 1 0 6h-1"/><path d="M15 12a3 3 0 0 1-3 3h-2a3 3 0 0 1 0-6h1"/></svg>',
    stream: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 7h16M4 12h16M4 17h10"/></svg>',
    store:  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><ellipse cx="12" cy="6" rx="8" ry="3"/><path d="M4 6v12c0 1.7 3.6 3 8 3s8-1.3 8-3V6"/><path d="M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3"/></svg>',
    ai:     '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="5" y="5" width="14" height="14" rx="1"/><path d="M9 9h6v6H9zM2 10v4M22 10v4M10 2h4M10 22h4"/></svg>',
    agents: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="8" r="3"/><path d="M5 20a7 7 0 0 1 14 0"/></svg>',
    box:    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="4" y="4" width="16" height="16" rx="1"/></svg>'
  };
  var LOCK = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><rect x="5" y="11" width="14" height="9" rx="1"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/></svg>';

  /* ───────── helpers ───────── */
  function el(html) { var t = document.createElement('template'); t.innerHTML = html.trim(); return t.content.firstChild; }
  function esc(s) { return String(s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }
  function shortMark(c) { return (c.id || '').replace(/[^a-z0-9]/gi, '').slice(0, 3).toUpperCase() || 'D'; }
  /* browser bar with the real URL hidden (lock + dots) — link is never exposed */
  function maskedBar() {
    return '<div class="bar"><span class="dot"></span><span class="dot"></span><span class="dot"></span>' +
      '<span class="url masked"><span class="lock">' + LOCK + '</span><span class="mask">••••••••••</span></span></div>';
  }
  /* start gentle auto-scroll over a screenshot once it is laid out + in view */
  function wireScroll(host) {
    if (host._s) return; host._s = true;
    var img = host.querySelector('.scroll-img');
    if (!img) return;
    function setup() {
      var d = img.offsetHeight - host.clientHeight;
      host.style.setProperty('--sy', (d > 0 ? -d : 0) + 'px');
      if (!REDUCE && d > 0) host.classList.add('go');
    }
    if (img.complete && img.naturalHeight) setup();
    else { img.addEventListener('load', setup); img.addEventListener('error', function () { host._s = false; }); }
  }
  /* full-frame screen-recording inside a device frame (frame = ours, content = real case) */
  function buildVideoFrame(opts) {
    var box = el('<div class="vframe"></div>');
    if (opts.ar) box.style.aspectRatio = opts.ar;
    var v = el('<video class="vfill" muted loop playsinline preload="metadata"></video>');
    v.src = opts.src;
    box.appendChild(v);
    return box;
  }

  /* ───────── reusable: fake telegram chat ───────── */
  function buildChat(messages, botName) {
    var wrap = el(
      '<div class="tgchat">' +
        '<div class="tg-head"><div class="tg-ava">B</div>' +
          '<div><div class="tg-name">' + esc(botName || 'DEADLINE BOT') + '</div>' +
          '<div class="tg-status">bot · online</div></div></div>' +
        '<div class="tg-body"></div>' +
      '</div>');
    var body = wrap.querySelector('.tg-body');
    wrap._messages = messages || [];
    wrap._body = body;
    wrap._played = false;
    return wrap;
  }
  function playChat(wrap) {
    if (wrap._played) return; wrap._played = true;
    var body = wrap._body, msgs = wrap._messages, i = 0;
    if (REDUCE) {
      msgs.forEach(function (m) {
        var b = el('<div class="bubble ' + bubbleCls(m.from) + ' show"></div>');
        b.textContent = L(m.text); body.appendChild(b);
      });
      return;
    }
    function step() {
      if (i >= msgs.length) return;
      var m = msgs[i];
      var delay = m.from === 'bot' ? 850 : 450;
      var typing = null;
      if (m.from === 'bot') {
        typing = el('<div class="typing"><span></span><span></span><span></span></div>');
        body.appendChild(typing);
        scrollDown(body);
      }
      setTimeout(function () {
        if (typing) typing.remove();
        var b = el('<div class="bubble ' + bubbleCls(m.from) + '"></div>');
        b.textContent = L(m.text);
        body.appendChild(b);
        requestAnimationFrame(function () { b.classList.add('show'); });
        scrollDown(body);
        i++;
        setTimeout(step, m.from === 'bot' ? 520 : 360);
      }, delay);
    }
    step();
  }
  function scrollDown(body) { body.scrollTop = body.scrollHeight; }

  /* ───────── reusable: data flow diagram ───────── */
  function buildFlow(nodes) {
    var wrap = el('<div class="flow"></div>');
    (nodes || []).forEach(function (n, idx) {
      if (idx > 0) wrap.appendChild(el('<span class="flow-arrow">▸</span>'));
      var accent = idx === (nodes.length - 1) ? ' accent' : '';
      var node = el(
        '<div class="flow-node">' +
          '<div class="glyph' + accent + '">' + (ICONS[n.icon] || ICONS.box) + '</div>' +
          '<div class="cap">' + esc(L(n.label)) + '</div>' +
        '</div>');
      node.querySelector('.cap').setAttribute('data-ru', n.label.ru);
      node.querySelector('.cap').setAttribute('data-en', n.label.en);
      wrap.appendChild(node);
    });
    return wrap;
  }
  function playFlow(wrap) {
    if (wrap._played) return; wrap._played = true;
    var items = wrap.querySelectorAll('.flow-node, .flow-arrow');
    items.forEach(function (it, k) { setTimeout(function () { it.classList.add('show'); }, REDUCE ? 0 : k * 110); });
  }

  /* ───────── reusable: n8n-style node graph (blocks + flowing connections) ───────── */
  function gNode(spec, id) { return spec.nodes.filter(function (n) { return n.id === id; })[0]; }
  function buildGraph(spec, skin) {
    var SVGNS = 'http://www.w3.org/2000/svg';
    var wrap = el('<div class="graph' + (skin ? ' gskin-' + skin : '') + '"></div>');
    var svg = document.createElementNS(SVGNS, 'svg');
    svg.setAttribute('class', 'edges');
    svg.setAttribute('viewBox', '0 0 100 100');
    svg.setAttribute('preserveAspectRatio', 'none');
    (spec.edges || []).forEach(function (e) {
      var a = gNode(spec, e[0]), b = gNode(spec, e[1]);
      if (!a || !b) return;
      var mx = (a.x + b.x) / 2;
      var d = 'M ' + a.x + ' ' + a.y + ' C ' + mx + ' ' + a.y + ' ' + mx + ' ' + b.y + ' ' + b.x + ' ' + b.y;
      var base = document.createElementNS(SVGNS, 'path');
      base.setAttribute('class', 'edge-base'); base.setAttribute('d', d); svg.appendChild(base);
      var flow = document.createElementNS(SVGNS, 'path');
      flow.setAttribute('class', 'edge-flow'); flow.setAttribute('d', d); svg.appendChild(flow);
    });
    wrap.appendChild(svg);
    (spec.nodes || []).forEach(function (n) {
      var ico = n.logo
        ? '<span class="gn-logo" style="background:' + n.logo.c + '">' + esc(n.logo.t) + '</span>'
        : (n.ico ? '<span class="gn-ico">' + (ICONS[n.ico] || '') + '</span>' : '');
      var node = el('<div class="gnode ' + (n.cls || '') + '">' +
        '<div class="gn-title">' + ico + esc(LL(n.title)) + '</div>' +
        (n.sub ? '<span class="gn-sub">' + esc(LL(n.sub)) + '</span>' : '') + '</div>');
      node.style.left = n.x + '%'; node.style.top = n.y + '%';
      wrap.appendChild(node);
    });
    return wrap;
  }
  function playGraph(wrap) {
    if (wrap._g) return; wrap._g = true;
    var nodes = wrap.querySelectorAll('.gnode');
    nodes.forEach(function (n, k) { setTimeout(function () { n.classList.add('show'); }, REDUCE ? 0 : k * 120); });
    setTimeout(function () { wrap.classList.add('go'); }, REDUCE ? 0 : nodes.length * 120);
  }

  /* ───────── reusable: animated funnel (client token moves through stages) ───────── */
  function buildFunnel(stages) {
    var wrap = el('<div class="funnel-wrap"></div>');
    var track = el('<div class="funnel-track"></div>');
    (stages || []).forEach(function (s) {
      track.appendChild(el('<div class="fstage">' +
        '<div class="fs-emoji">' + esc(s.emoji || '') + '</div>' +
        '<div class="fs-label">' + esc(LL(s.label)) + '</div>' +
        (s.sub ? '<div class="fs-sub">' + esc(LL(s.sub)) + '</div>' : '') + '</div>'));
    });
    var token = el('<div class="client-token"><div class="ct-dot">🙋</div></div>');
    var pop = el('<div class="pop-text"></div>');
    wrap.appendChild(track); wrap.appendChild(token); wrap.appendChild(pop);
    wrap._stages = stages; wrap._track = track; wrap._token = token; wrap._pop = pop;
    return wrap;
  }
  function playFunnel(wrap) {
    if (wrap._f) return; wrap._f = true;
    var stages = wrap._stages, track = wrap._track, token = wrap._token, pop = wrap._pop;
    var cards = track.querySelectorAll('.fstage');
    function cx(card) { var w = wrap.getBoundingClientRect(), c = card.getBoundingClientRect(); return c.left - w.left + c.width / 2; }
    if (REDUCE) {
      cards.forEach(function (c) { c.classList.add('active'); });
      if (cards.length) token.style.left = cx(cards[cards.length - 1]) + 'px';
      return;
    }
    function at(i) {
      if (i >= stages.length) return;
      var card = cards[i], x = cx(card);
      token.style.left = x + 'px';
      card.classList.add('active');
      var s = stages[i];
      if (s.pop) { pop.textContent = LL(s.pop); pop.style.left = x + 'px'; pop.classList.add('show'); setTimeout(function () { pop.classList.remove('show'); }, 1400); }
      setTimeout(function () { at(i + 1); }, 1700);
    }
    setTimeout(function () { at(0); }, 350);
  }

  /* ───────── reusable: animated mock app interface (scroll + taps + reveals) ───────── */
  /* ───────── reusable: CRM kanban — client card moves through stages ───────── */
  function buildKanban(spec) {
    var stages = spec.stages || [];
    var wrap = el('<div class="kanban"></div>');
    var cols = el('<div class="kanban-cols"></div>');
    stages.forEach(function (s) {
      cols.appendChild(el('<div class="kcol"><div class="kcol-h"><span class="em">' + esc(s.emoji || '') + '</span>' + esc(LL(s.label)) + '</div></div>'));
    });
    var client = el('<div class="kclient"><div class="kc-card"><div class="kc-name">' + esc(LL(spec.client || { ru: 'Клиент', en: 'Client' })) + '</div><div class="kc-sub">' + esc(spec.value || '') + '</div></div></div>');
    var pop = el('<div class="kpop"></div>');
    wrap.appendChild(cols); wrap.appendChild(client); wrap.appendChild(pop);
    wrap._stages = stages; wrap._cols = cols; wrap._client = client; wrap._pop = pop;
    return wrap;
  }
  function playKanban(wrap) {
    if (wrap._k) return; wrap._k = true;
    var stages = wrap._stages, cols = wrap._cols.querySelectorAll('.kcol'), client = wrap._client, pop = wrap._pop;
    function cx(col) { var w = wrap.getBoundingClientRect(), c = col.getBoundingClientRect(); return c.left - w.left + c.width / 2; }
    if (REDUCE) { cols.forEach(function (c) { c.classList.add('active'); }); if (cols.length) client.style.left = cx(cols[cols.length - 1]) + 'px'; return; }
    function at(i) {
      if (i >= stages.length) return;
      var col = cols[i], x = cx(col);
      client.style.left = x + 'px';
      col.classList.add('active');
      var s = stages[i];
      if (s.pop) { pop.textContent = LL(s.pop); pop.style.left = x + 'px'; pop.classList.add('show'); setTimeout(function () { pop.classList.remove('show'); }, 1500); }
      setTimeout(function () { at(i + 1); }, 1800);
    }
    setTimeout(function () { at(0); }, 400);
  }

  function store2Html() {
    var games = [
      { n: 'CS2', p: '690 ₽', g: 'g1' },
      { n: 'GTA V', p: '1 490 ₽', g: 'g2' },
      { n: 'Cyberpunk 2077', p: '1 290 ₽', g: 'g3', buy: true },
      { n: 'Elden Ring', p: '2 100 ₽', g: 'g4' },
      { n: 'Hades II', p: '990 ₽', g: 'g5' }
    ];
    var cards = '';
    games.forEach(function (x) {
      cards += '<div class="s2card ' + x.g + '"><div class="s2art"></div><div class="s2meta"><div class="s2name">' + x.n + '</div><div class="s2price">' + x.p + '</div></div><button class="s2buy"' + (x.buy ? ' data-buy' : '') + '>Купить</button></div>';
    });
    return '<div class="store2">' +
      '<div class="s2-top"><div class="s2-brand"><span class="s2-logo">🎮</span>Backdoor</div><div class="s2-bal">⚡ instant</div></div>' +
      '<div class="s2-pay"><span>💳 Visa</span><span>₿ Crypto</span><span>СБП</span><span>🍎 Pay</span></div>' +
      '<div class="s2-scroll"><div class="s2-inner">' + cards + '</div></div>' +
      '<div class="s2news" data-news>🔥 <b>Hades II</b> — новинка в каталоге</div>' +
      '<div class="s2sheet"><div class="s2-ok">✓ Ключ выдан</div><div class="s2-key" data-key>————-————-————</div><div class="s2-sub">Активируйте в Steam · мгновенно</div></div>' +
      '</div>';
  }
  function typeKey(node, code) {
    if (!node) return;
    if (REDUCE) { node.textContent = code; return; }
    node.textContent = ''; var i = 0;
    (function step() { if (i > code.length) return; node.textContent = code.slice(0, i) + (i < code.length ? '▋' : ''); i++; setTimeout(step, 70); })();
  }
  function appStoreCard(name, price, buy) {
    return '<div class="acard"><div class="a-row"><div class="a-ttl">' + name + '</div><div class="a-price">' + price + '</div></div>' +
      '<div class="a-row" style="margin-top:7px;justify-content:flex-end;"><span class="abtn"' + (buy ? ' data-buy' : '') + '>Купить</span></div></div>';
  }
  function buildApp(kind, bare) {
    var wrap = el('<div class="appmock"' + (bare ? ' style="padding:0"' : '') + '></div>');
    var screen = el('<div class="appscreen' + (bare ? ' bare' : '') + '"></div>');
    var inner;
    if (kind === 'store') {
      screen.className += ' skin-store2';
      inner = store2Html();
    } else {
      inner = '<div class="as-top"><div class="as-logo">S</div><div class="as-name">SMM Easy</div></div>' +
        '<div class="appscroll"><div class="appscroll-inner">' +
        '<div class="acard"><div class="a-ttl">Ниша</div><div class="a-row" style="margin-top:8px;gap:6px;justify-content:flex-start;"><span class="achip" data-chip>☕ Кафе</span><span class="achip">🏨 Отель</span></div></div>' +
        '<div class="acard"><div class="a-ttl">Тема</div><div style="font-size:10px;color:var(--mute);font-family:\'JetBrains Mono\',monospace;margin-top:6px;">Утренний кофе ☕</div></div>' +
        '<div style="text-align:center;margin:2px 0;"><span class="abtn" data-gen>✨ Сгенерировать</span></div>' +
        '<div class="acard areveal" data-r><div class="a-ttl">📝 Текст</div><div style="font-size:9px;color:var(--mute);margin-top:5px;font-family:\'Inter\',sans-serif;line-height:1.5;">«Доброе утро начинается с аромата свежего кофе…»</div></div>' +
        '<div class="acard areveal" data-r><div class="a-ttl">🖼 Карусель · 5</div><div class="a-row" style="margin-top:6px;gap:4px;"><span style="flex:1;height:22px;background:linear-gradient(135deg,var(--copper),var(--rust));border:2px solid var(--border-dark)"></span><span style="flex:1;height:22px;background:linear-gradient(135deg,var(--sand),var(--copper));border:2px solid var(--border-dark)"></span><span style="flex:1;height:22px;background:var(--surface-2);border:2px solid var(--border-dark)"></span></div></div>' +
        '<div class="acard areveal" data-r><div class="a-ttl">🎬 Reel · voice-first</div><div style="font-size:9px;color:var(--mute);margin-top:5px;font-family:\'JetBrains Mono\',monospace;">00:14 · озвучка ✓</div></div>' +
        '</div></div>';
    }
    screen.innerHTML = inner;
    wrap.appendChild(screen);
    wrap._kind = kind;
    return wrap;
  }
  function playApp(wrap) {
    if (wrap._a) return; wrap._a = true;
    var inner = wrap.querySelector('.appscroll-inner');
    function sc(px) { if (inner && !REDUCE) inner.style.transform = 'translateY(' + px + 'px)'; }
    function seq(steps) { if (REDUCE) { steps.forEach(function (s) { s[1](); }); return; } steps.forEach(function (s) { setTimeout(s[1], s[0]); }); }
    if (wrap._kind === 'store') {
      var s2inner = wrap.querySelector('.s2-inner');
      var buy = wrap.querySelector('[data-buy]'), sheet = wrap.querySelector('.s2sheet'), key = wrap.querySelector('[data-key]'), news = wrap.querySelector('[data-news]');
      function s2sc(px) { if (s2inner && !REDUCE) s2inner.style.transform = 'translateY(' + px + 'px)'; }
      seq([
        [500, function () { news && news.classList.add('show'); }],
        [2400, function () { news && news.classList.remove('show'); }],
        [2900, function () { s2sc(-66); }],
        [3700, function () { buy && buy.classList.add('tap'); }],
        [3950, function () { if (buy) { buy.classList.remove('tap'); buy.classList.add('load'); buy.textContent = 'Оплата…'; } }],
        [4900, function () { sheet && sheet.classList.add('show'); typeKey(key, 'A7K9-X2QF-M4ZP'); if (buy) { buy.classList.remove('load'); buy.textContent = 'Куплено'; } }],
        [7800, function () { sheet && sheet.classList.remove('show'); s2sc(0); if (buy) buy.textContent = 'Купить'; }]
      ]);
    } else {
      var chip = wrap.querySelector('[data-chip]'), gen = wrap.querySelector('[data-gen]'), rev = wrap.querySelectorAll('[data-r]');
      seq([[500, function () { chip && chip.classList.add('tap'); }], [1200, function () { gen && gen.classList.add('tap'); }], [1700, function () { gen && gen.classList.remove('tap'); }], [1900, function () { rev.forEach(function (r, i) { setTimeout(function () { r.classList.add('show'); sc(-(i * 46)); }, i * 550); }); }]]);
    }
  }

  /* ───────── reusable: TG bot menu mock (recreated from the real bot) ───────── */
  function buildBotMenu() {
    var btns = [
      { t: '✨ Открыть приложение', full: true },
      { t: '🎮 Техника' }, { t: '🚗 Транспорт' },
      { t: '⛵ Яхты' }, { t: '🍎 Apple' },
      { t: '💃 Услуги' }, { t: '🎁 Акции' },
      { t: '❓ FAQ' }, { t: '⭐ Отзывы' },
      { t: '👤 Мой кабинет' }, { t: '📞 Контакт' }
    ];
    var html = '<div class="botmenu"><div class="bm-head"><div class="bm-t">Главное меню VIP Rental Phuket</div><div class="bm-q">Что вас интересует?</div></div><div class="bm-grid">';
    btns.forEach(function (b) { html += '<div class="bm-btn' + (b.full ? ' full' : '') + '">' + b.t + '</div>'; });
    html += '</div></div>';
    return el(html);
  }
  function playBotMenu(wrap) {
    if (wrap._b) return; wrap._b = true;
    wrap.querySelectorAll('.bm-btn').forEach(function (b, k) { setTimeout(function () { b.classList.add('show'); }, REDUCE ? 0 : k * 85); });
  }

  /* ───────── reusable: number counter ───────── */
  function animateCount(node) {
    if (node._done) return; node._done = true;
    var target = parseFloat(node.dataset.count);
    var dec = (String(node.dataset.count).split('.')[1] || '').length;
    var prefix = node.dataset.prefix || '', suffix = node.dataset.suffix || '';
    function fmt(v) {
      var s = dec ? v.toFixed(dec) : Math.round(v).toLocaleString('ru-RU');
      return prefix + s + suffix;
    }
    if (REDUCE) { node.textContent = fmt(target); return; }
    var dur = 1100, t0 = null;
    function tick(t) {
      if (t0 === null) t0 = t;
      var p = Math.min((t - t0) / dur, 1);
      var eased = 1 - Math.pow(1 - p, 3);
      node.textContent = fmt(target * eased);
      if (p < 1) requestAnimationFrame(tick);
    }
    requestAnimationFrame(tick);
  }

  /* ───────── media block per case ───────── */
  function buildMedia(c) {
    var m = c.media || {}, mode = m.mode || 'placeholder';

    if (mode === 'chat') {
      var dev = el('<div class="device"></div>');
      var phone = el('<div class="phone"><div class="notch"></div><div class="viewport"></div></div>');
      var chat = buildChat(m.chat, L(c.title));
      phone.querySelector('.viewport').appendChild(chat);
      dev.appendChild(phone);
      dev._chat = chat;
      return dev;
    }

    if (mode === 'app') {
      return buildApp(m.app || 'store');
    }

    if (c.type === 'data') {
      return el('<div class="datacover"><div class="dc-grid"></div>' +
        '<div class="dc-badge">ON-CHAIN · AI</div>' +
        '<div class="dc-stats">' +
          '<div class="dc-stat"><b>12</b><span><span class="lang-ru">сетей</span><span class="lang-en">chains</span></span></div>' +
          '<div class="dc-stat"><b>41M+</b><span><span class="lang-ru">блоков</span><span class="lang-en">blocks</span></span></div>' +
          '<div class="dc-stat"><b>4</b><span><span class="lang-ru">агента</span><span class="lang-en">agents</span></span></div>' +
        '</div></div>');
    }

    if (mode === 'scroll') {
      var sdev = el('<div class="device"></div>');
      var sframe = (c.frame === 'phone')
        ? el('<div class="phone"><div class="notch"></div><div class="viewport"></div></div>')
        : el('<div class="browser">' + maskedBar() + '<div class="viewport"></div></div>');
      var host = el('<div class="scroll-host"></div>');
      if (m.image) {
        var sim = el('<img class="scroll-img" loading="lazy" alt="' + esc(L(c.title)) + '">');
        sim.src = m.image; host.appendChild(sim);
      } else {
        host.appendChild(el('<div class="ph"><div class="ph-mark">' + esc(shortMark(c)) + '</div><div class="ph-note"><span class="lang-ru">скрин для скролла</span><span class="lang-en">scroll screenshot</span></div></div>'));
      }
      sframe.querySelector('.viewport').appendChild(host);
      sdev.appendChild(sframe);
      return sdev;
    }

    if (mode === 'iframe' && m.liveUrl) {
      var br = el('<div class="device"><div class="browser">' + maskedBar() + '<div class="viewport"></div></div></div>');
      var ifr = el('<iframe loading="lazy" title="' + esc(L(c.title)) + '"></iframe>');
      ifr.src = m.liveUrl;
      br.querySelector('.viewport').appendChild(ifr);
      return br;
    }

    if (mode === 'video' && m.video) {
      var vc = el('<div class="vcard' + (m.appSize ? ' app-small' : '') + '"></div>');
      var vv = el('<video class="vfill' + (m.pop ? ' vpop' : '') + '" muted loop playsinline preload="metadata"></video>');
      vv.src = m.video;
      vc.appendChild(vv);
      return vc;
    }

    if (mode === 'image' && m.images && m.images.length) {
      var img = el('<img loading="lazy" alt="' + esc(L(c.title)) + '">');
      img.src = m.images[0];
      return img;
    }

    /* CTA placeholder — «ваш кейс здесь» */
    if (c.cta) {
      return el(
        '<div class="ph ph-cta">' +
          '<div class="ph-mark">+</div>' +
          '<div class="ph-note"><span class="lang-ru">ваш кейс здесь</span><span class="lang-en">your case here</span></div>' +
        '</div>');
    }
    /* placeholder */
    return el(
      '<div class="ph">' +
        '<div class="ph-mark">' + esc(shortMark(c)) + '</div>' +
        '<div class="ph-note"><span class="lang-ru">слот для ассета</span><span class="lang-en">asset slot</span></div>' +
      '</div>');
  }

  /* ───────── one case card ───────── */
  function buildCard(c) {
    var card = el('<article class="case-card' + (c.featured ? ' featured' : '') + ' reveal" data-type="' + c.type + '" tabindex="0"></article>');
    card.style.position = 'relative';
    card.appendChild(el('<span class="type-tag t-' + c.type + '">' + c.type + '</span>'));

    var media = null;
    if (!c.cta) {
      media = el('<div class="case-media"></div>');
      if (c.media && (c.media.mode === 'video' || c.media.mode === 'app') && c.media.ar) media.style.aspectRatio = c.media.ar;
      var inner = buildMedia(c);
      media.appendChild(inner);
      media._inner = inner;
      card.appendChild(media);
    } else {
      card.classList.add('cta-nomedia');
    }

    var body = el('<div class="case-body"></div>');
    body.appendChild(el('<div class="case-meta">' + esc(L(c.meta)) + '</div>'));
    body.appendChild(el('<h3 class="case-title">' + esc(L(c.title)) + '</h3>'));
    body.appendChild(el('<p class="case-summary">' + esc(L(c.summary)) + '</p>'));

    if (c.stack && c.stack.length) {
      var tc = el('<div class="tech-chips"></div>');
      c.stack.forEach(function (s) { tc.appendChild(el('<span class="tech-chip">' + esc(s) + '</span>')); });
      body.appendChild(tc);
    }

    if (c.metrics && c.metrics.length) {
      var mx = el('<div class="case-metrics"></div>');
      c.metrics.forEach(function (mt) {
        var metric = el('<div class="metric"><span class="num" data-count="' + mt.value + '" data-prefix="' + (mt.prefix || '') + '" data-suffix="' + (mt.suffix || '') + '">0</span><span class="lbl">' + esc(L(mt.label)) + '</span></div>');
        mx.appendChild(metric);
      });
      body.appendChild(mx);
    }

    var live = c.media && c.media.liveUrl;
    if (live) {
      var act = el('<div class="case-actions"></div>');
      var a = el('<a class="btn-chip-copper" target="_blank" rel="noopener"><span class="lang-ru">Открыть вживую ↗</span><span class="lang-en">Open live ↗</span></a>');
      a.href = live; act.appendChild(a); body.appendChild(act);
    }

    if (c.cta) {
      card.classList.add('is-cta');
      card.style.cursor = 'pointer';
      body.appendChild(el('<div class="case-actions"><a class="btn-chip-copper" href="' + c.cta + '"><span class="lang-ru">Оставить заявку →</span><span class="lang-en">Get a quote →</span></a></div>'));
      card.addEventListener('click', function (e) { if (e.target.closest('a')) return; window.location.href = c.cta; });
    }

    card.appendChild(body);
    card._case = c; card._media = media;
    return card;
  }

  /* ───────── render grid + filters ───────── */
  function renderFilters(onPick) {
    var bar = document.getElementById('filterBar');
    if (!bar) return;
    var pool = CASES.filter(function (c) { return !c.hideInGrid; });
    FILTERS.forEach(function (f) {
      var count = f.key === 'all' ? pool.length : pool.filter(function (c) { return c.type === f.key; }).length;
      if (f.key !== 'all' && count === 0) return;
      var btn = el('<button class="filter-btn" data-key="' + f.key + '" aria-pressed="' + (f.key === 'all' ? 'true' : 'false') + '">' +
        '<span class="lang-ru">' + esc(f.label.ru) + '</span><span class="lang-en">' + esc(f.label.en) + '</span>' +
        '<span class="filter-count">' + count + '</span></button>');
      btn.addEventListener('click', function () { onPick(f.key, btn); });
      bar.appendChild(btn);
    });
  }

  function renderGrid() {
    var grid = document.getElementById('bento');
    if (!grid) return [];
    var cards = CASES.filter(function (c) { return !c.hideInGrid; }).map(function (c) { var card = buildCard(c); grid.appendChild(card); return card; });
    return cards;
  }

  function applyFilter(cards, key, btn) {
    document.querySelectorAll('.filter-btn').forEach(function (b) { b.setAttribute('aria-pressed', b === btn ? 'true' : 'false'); });
    var shown = 0;
    cards.forEach(function (card) {
      var match = key === 'all' || card._case.type === key;
      if (match) {
        card.classList.remove('is-hidden');
        if (!REDUCE) { card.classList.add('filtering'); requestAnimationFrame(function () { setTimeout(function () { card.classList.remove('filtering'); }, shown * 45); }); }
        shown++;
      } else {
        card.classList.add('is-hidden');
      }
    });
  }

  /* ───────── observers: counters / chat / flow / reveal / video ───────── */
  function setupObservers(cards) {
    if (!('IntersectionObserver' in window)) {
      // fallback: всё сразу
      document.querySelectorAll('.reveal').forEach(function (e) { e.classList.add('visible'); });
      cards.forEach(activateCard);
      return;
    }
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (en, i) {
        if (!en.isIntersecting) return;
        var t = en.target;
        setTimeout(function () { t.classList.add('visible'); }, REDUCE ? 0 : i * 60);
        if (t.classList.contains('case-card')) activateCard(t);
        io.unobserve(t);
      });
    }, { rootMargin: '0px 0px -8% 0px', threshold: 0.15 });
    document.querySelectorAll('.reveal').forEach(function (e) { io.observe(e); });

    // showcase deep-dives (chat/flow) вне грида
    document.querySelectorAll('[data-activate]').forEach(function (n) {
      var so = new IntersectionObserver(function (es) {
        es.forEach(function (e) { if (e.isIntersecting) { activateNode(n); so.unobserve(n); } });
      }, { threshold: 0.25 });
      so.observe(n);
    });
    // full-frame demo videos: play when visible, pause when out of view
    var vo = new IntersectionObserver(function (es) {
      es.forEach(function (e) { var v = e.target; if (e.isIntersecting) v.play().catch(function () {}); else v.pause(); });
    }, { threshold: 0.2 });
    document.querySelectorAll('video.vfill').forEach(function (v) { vo.observe(v); });
  }

  function activateCard(card) {
    // counters
    card.querySelectorAll('.num[data-count]').forEach(animateCount);
    // chat
    var inner = card._media && card._media._inner;
    if (inner && inner._chat) playChat(inner._chat);
    if (inner && inner._flow) playFlow(inner._flow);
    if (inner && inner._graph) playGraph(inner._graph);
    card.querySelectorAll('.appmock').forEach(playApp);
    // auto-scroll screenshots
    card.querySelectorAll('.scroll-host').forEach(wireScroll);
    // full-frame demo videos play via the visibility observer in setupObservers
  }

  function activateNode(n) {
    n.querySelectorAll('.num[data-count]').forEach(animateCount);
    n.querySelectorAll('.scroll-host').forEach(wireScroll);
    n.querySelectorAll('.appmock').forEach(playApp);
    n.querySelectorAll('.botmenu').forEach(playBotMenu);
    if (n._chat) playChat(n._chat);
    if (n._flow) playFlow(n._flow);
    if (n._graph) playGraph(n._graph);
    if (n._funnel) playFunnel(n._funnel);
    if (n._kanban) playKanban(n._kanban);
  }

  /* ───────── 3D tilt on cards (skip touch / reduced-motion) ───────── */
  function setupTilt() {
    if (REDUCE || COARSE) return;
    document.querySelectorAll('.case-card').forEach(function (card) {
      var raf = null;
      card.addEventListener('pointermove', function (e) {
        var r = card.getBoundingClientRect();
        var px = (e.clientX - r.left) / r.width - 0.5;
        var py = (e.clientY - r.top) / r.height - 0.5;
        if (raf) cancelAnimationFrame(raf);
        raf = requestAnimationFrame(function () {
          card.style.transform = 'perspective(800px) rotateX(' + (-py * 5).toFixed(2) + 'deg) rotateY(' + (px * 6).toFixed(2) + 'deg) translateZ(0)';
        });
      });
      card.addEventListener('pointerleave', function () {
        if (raf) cancelAnimationFrame(raf);
        card.style.transform = '';
      });
    });
  }

  /* ───────── deep-dive showcase wiring (chat / flow / scroll-frame) ───────── */
  function setupShowcases() {
    // KeyDrop — ONE case: app interface first, then auto-sync schema
    var kd = byId('keydrop');
    var kdApp = document.getElementById('scKeydropApp');
    if (kdApp) kdApp.appendChild(buildApp('store', true));
    var kdG = document.getElementById('scKeydropGraph');
    if (kdG && kd && kd.graph) { var kg = buildGraph(kd.graph, 'blueprint'); kdG.appendChild(kg); kdG._graph = kg; }
    // RA — n8n-style graph
    var flowHost = document.getElementById('scFlow');
    if (flowHost) {
      var ra = byId('ra');
      if (ra) { var dg = ra.graph ? buildGraph(ra.graph, 'cyber') : buildFlow(ra.flow); flowHost.appendChild(dg); if (ra.graph) flowHost._graph = dg; else flowHost._flow = dg; }
    }
    // Vasiliy — bot executing complex tasks (mock)
    var vHost = document.getElementById('scVasiliy');
    if (vHost) { var vb = byId('vasiliy'); if (vb && vb.task) { var vchat = buildChat(vb.task, L(vb.title)); vHost.appendChild(vchat); vHost._chat = vchat; } }
    // VRP — architecture graph + TG bot menu mock
    var vrpG = document.getElementById('scVrpGraph');
    var vrp = byId('vrp');
    if (vrpG && vrp && vrp.graph) { var vg = buildGraph(vrp.graph, 'forge'); vrpG.appendChild(vg); vrpG._graph = vg; }
    var vrpSite = document.getElementById('scVrpSite');
    if (vrpSite) vrpSite.appendChild(buildVideoFrame({ frame: 'browser', src: 'assets/cases/vrp/site.mp4', ar: '1896/868' }));
    var vrpBot = document.getElementById('scVrpBot');
    if (vrpBot) vrpBot.appendChild(buildVideoFrame({ frame: 'browser', src: 'assets/cases/vrp/bot.mp4', ar: '880/782' }));
    var vrpAdmin = document.getElementById('scVrpAdmin');
    if (vrpAdmin) vrpAdmin.appendChild(buildVideoFrame({ frame: 'browser', src: 'assets/cases/vrp/admin.mp4', ar: '1920/888' }));
    // Sales Bot — n8n graph + funnel
    var gHost = document.getElementById('scGraph');
    var sb = byId('salesbot');
    if (gHost && sb && sb.graph) { var g = buildGraph(sb.graph); gHost.appendChild(g); gHost._graph = g; }
    var fHost = document.getElementById('scFunnel');
    if (fHost && sb && sb.funnel) { var f = buildFunnel(sb.funnel); fHost.appendChild(f); fHost._funnel = f; }
    // SMM Easy — automation schema
    var smHost = document.getElementById('scSmm');
    var sm = byId('smmeasy');
    if (smHost && sm && sm.graph) { var sg = buildGraph(sm.graph, 'soft'); smHost.appendChild(sg); smHost._graph = sg; }
    // Lead-bot — CRM kanban
    var lk = document.getElementById('scLeadKanban');
    var lb = byId('leadbot');
    if (lk && lb && lb.kanban) { var kb = buildKanban(lb.kanban); lk.appendChild(kb); lk._kanban = kb; }
    // Research bot — content autopilot schema
    var rsH = document.getElementById('scResearch');
    var rs = byId('research');
    if (rsH && rs && rs.graph) { var rg = buildGraph(rs.graph, 'cyber'); rsH.appendChild(rg); rsH._graph = rg; }
    // VRP scroll-inside-frame
    setupScrollFrame();
  }

  function setupScrollFrame() {
    var win = document.getElementById('scrollWindow');
    var track = document.getElementById('scrollTrack');
    if (!win || !track) return;
    function maxShift() { return Math.max(0, track.scrollHeight - win.clientHeight); }
    if (REDUCE) { return; }
    if (window.gsap && window.ScrollTrigger) {
      window.gsap.registerPlugin(window.ScrollTrigger);
      window.gsap.to(track, {
        y: function () { return -maxShift(); },
        ease: 'none',
        scrollTrigger: { trigger: win.closest('.showcase'), start: 'top 70%', end: 'bottom 30%', scrub: 0.6, invalidateOnRefresh: true }
      });
    } else {
      // fallback без GSAP — двигаем трек пропорционально прокрутке секции
      var sec = win.closest('.showcase');
      window.addEventListener('scroll', function () {
        var r = sec.getBoundingClientRect();
        var vh = window.innerHeight;
        var prog = Math.min(Math.max((vh - r.top) / (vh + r.height), 0), 1);
        track.style.transform = 'translateY(' + (-maxShift() * prog) + 'px)';
      }, { passive: true });
    }
  }

  /* ───────── boot ───────── */
  function boot() {
    applyLang(curLang());
    wireLang();
    var cards = renderGrid();
    renderFilters(function (key, btn) { applyFilter(cards, key, btn); });
    setupShowcases();
    setupObservers(cards);
    setupTilt();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})();
