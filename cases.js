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

  /* ───────── small SVG icon set for flow diagrams ───────── */
  var ICONS = {
    chains: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 12a3 3 0 0 1 3-3h2a3 3 0 0 1 0 6h-1"/><path d="M15 12a3 3 0 0 1-3 3h-2a3 3 0 0 1 0-6h1"/></svg>',
    stream: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 7h16M4 12h16M4 17h10"/></svg>',
    store:  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><ellipse cx="12" cy="6" rx="8" ry="3"/><path d="M4 6v12c0 1.7 3.6 3 8 3s8-1.3 8-3V6"/><path d="M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3"/></svg>',
    ai:     '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="5" y="5" width="14" height="14" rx="1"/><path d="M9 9h6v6H9zM2 10v4M22 10v4M10 2h4M10 22h4"/></svg>',
    agents: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="8" r="3"/><path d="M5 20a7 7 0 0 1 14 0"/></svg>',
    box:    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="4" y="4" width="16" height="16" rx="1"/></svg>'
  };

  /* ───────── helpers ───────── */
  function el(html) { var t = document.createElement('template'); t.innerHTML = html.trim(); return t.content.firstChild; }
  function esc(s) { return String(s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }
  function shortMark(c) { return (c.id || '').replace(/[^a-z0-9]/gi, '').slice(0, 3).toUpperCase() || 'D'; }

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
        var b = el('<div class="bubble ' + (m.from === 'bot' ? 'in' : 'out') + ' show"></div>');
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
        var b = el('<div class="bubble ' + (m.from === 'bot' ? 'in' : 'out') + '"></div>');
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

    if (c.type === 'data' && c.flow) {
      var fwrap = el('<div class="device" style="padding:8px;"></div>');
      var flow = buildFlow(c.flow);
      fwrap.appendChild(flow);
      fwrap._flow = flow;
      return fwrap;
    }

    if (mode === 'iframe' && m.liveUrl) {
      var url = m.liveUrl.replace(/^https?:\/\//, '');
      var br = el('<div class="device"><div class="browser"><div class="bar"><span class="dot"></span><span class="dot"></span><span class="dot"></span><span class="url">' + esc(url) + '</span></div><div class="viewport"></div></div></div>');
      var ifr = el('<iframe loading="lazy" title="' + esc(L(c.title)) + '"></iframe>');
      ifr.src = m.liveUrl;
      br.querySelector('.viewport').appendChild(ifr);
      return br;
    }

    if (mode === 'video' && m.video) {
      var box = el('<div style="width:100%;height:100%;position:relative;"></div>');
      var v = el('<video muted loop playsinline preload="metadata"></video>');
      if (m.poster) v.poster = m.poster;
      v.src = m.video;
      box.appendChild(v);
      box.appendChild(el('<span class="play-hint">▶ hover</span>'));
      box._video = v;
      return box;
    }

    if (mode === 'image' && m.images && m.images.length) {
      var img = el('<img loading="lazy" alt="' + esc(L(c.title)) + '">');
      img.src = m.images[0];
      return img;
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

    var media = el('<div class="case-media"></div>');
    var inner = buildMedia(c);
    media.appendChild(inner);
    media._inner = inner;
    card.appendChild(media);

    var body = el('<div class="case-body"></div>');
    body.appendChild(el('<div class="case-meta">' + esc(L(c.meta)) + '</div>'));
    body.appendChild(el('<h3 class="case-title">' + esc(L(c.title)) + '</h3>'));
    body.appendChild(el('<p class="case-summary">' + esc(L(c.summary)) + '</p>'));

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

    card.appendChild(body);
    card._case = c; card._media = media;
    return card;
  }

  /* ───────── render grid + filters ───────── */
  function renderFilters(onPick) {
    var bar = document.getElementById('filterBar');
    if (!bar) return;
    FILTERS.forEach(function (f) {
      var count = f.key === 'all' ? CASES.length : CASES.filter(function (c) { return c.type === f.key; }).length;
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
    var cards = CASES.map(function (c) { var card = buildCard(c); grid.appendChild(card); return card; });
    // если только один featured виден — растянуть на всю ширину
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
  }

  function activateCard(card) {
    // counters
    card.querySelectorAll('.num[data-count]').forEach(animateCount);
    // chat
    var inner = card._media && card._media._inner;
    if (inner && inner._chat) playChat(inner._chat);
    if (inner && inner._flow) playFlow(inner._flow);
    // video hover wiring
    card.querySelectorAll('video').forEach(function (v) {
      if (v._wired) return; v._wired = true;
      var host = card;
      host.addEventListener('mouseenter', function () { v.play().catch(function () {}); });
      host.addEventListener('mouseleave', function () { v.pause(); });
      host.addEventListener('focus', function () { v.play().catch(function () {}); });
      host.addEventListener('blur', function () { v.pause(); });
    });
  }

  function activateNode(n) {
    n.querySelectorAll('.num[data-count]').forEach(animateCount);
    if (n._chat) playChat(n._chat);
    if (n._flow) playFlow(n._flow);
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
    // KeyDrop full chat
    var chatHost = document.getElementById('scChat');
    if (chatHost) {
      var kd = CASES.filter(function (c) { return c.id === 'keydrop'; })[0];
      if (kd) { var chat = buildChat(kd.media.chat, L(kd.title)); chatHost.appendChild(chat); chatHost._chat = chat; }
    }
    // RA full flow
    var flowHost = document.getElementById('scFlow');
    if (flowHost) {
      var ra = CASES.filter(function (c) { return c.id === 'ra'; })[0];
      if (ra) { var flow = buildFlow(ra.flow); flowHost.appendChild(flow); flowHost._flow = flow; }
    }
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
