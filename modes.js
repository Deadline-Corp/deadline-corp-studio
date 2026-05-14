/* ═══════════════════════════════════════════════════════════════════════
   MAGIC MODE TOGGLE — orchestrator
     Phase 1: button + transition lock + class swap
     Phase 2: Matrix theatre + idle signatures
     Phase 3: Island theatre + idle signatures
     Phase 4: Studio theatre + idle signatures (curtain, parallax, spotlight)
   ═══════════════════════════════════════════════════════════════════════ */
(() => {
  'use strict';

  // ── v2 (2026-05-12): cycle trimmed to 4 positions per user spec ──
  //   pos 1 = ORIGINAL (default, no mode-class)
  //   pos 2 = NOIR     (graphics / Three.js scenes)
  //   pos 3 = MATRIX   ("третий скин" — the cinematic CRT one)
  //   pos 4 = ISLAND   (sunset / island)
  // studio / vault / kinetic are hidden from cycle but their CSS is left
  // intact — see CHANGES_MATRIX_v2.md for full rollback.
  const MODES = ['matrix', 'island', 'noir'];
  const CYCLE = [null, 'noir', 'matrix', 'island'];
  // ── v2.3: trimmed labels — only the 4 reachable modes ──
  const MODE_LABELS = {
    'null':    'ORIGINAL',
    'noir':    'NOIR',
    'matrix':  'MATRIX',
    'island':  'ISLAND',
  };
  const TRANSITION_LOCK_CLASS = 'is-transitioning';
  const HTML = document.documentElement;
  const BODY = document.body;

  const btn = document.getElementById('magic-toggle');
  if (!btn) return;

  // ─────────────────────────────────────────────────────────────────────
  // Helpers
  // ─────────────────────────────────────────────────────────────────────
  const wait = ms => new Promise(r => setTimeout(r, ms));

  function getCurrentMode() {
    for (const m of MODES) if (HTML.classList.contains(`mode-${m}`)) return m;
    return null;
  }

  function clearModeClasses() {
    MODES.forEach(m => HTML.classList.remove(`mode-${m}`));
  }

  // Sequential pick — finds current in CYCLE, returns the next entry,
  // wrapping past STUDIO back to ORIGINAL. Result `null` clears all
  // mode classes (default DOM visible).
  function pickNextMode() {
    const current = getCurrentMode();
    const idx = CYCLE.indexOf(current);
    return CYCLE[(idx + 1) % CYCLE.length];
  }

  function getModeLabel(mode) {
    const idx = CYCLE.indexOf(mode);
    const pos = (idx === -1 ? 0 : idx) + 1;
    const name = MODE_LABELS[String(mode)] || 'ORIGINAL';
    return { pos: String(pos).padStart(2, '0'), name };
  }

  // ─────────────────────────────────────────────────────────────────────
  // Theme overlay element
  // ─────────────────────────────────────────────────────────────────────
  const overlay = document.createElement('div');
  overlay.className = 'mode-overlay';
  overlay.setAttribute('aria-hidden', 'true');
  document.body.appendChild(overlay);

  // ─────────────────────────────────────────────────────────────────────
  // Mode indicator chip — always visible under the magic-toggle. Shows
  // the current position in the sequential cycle (e.g. "02 / NOIR") so
  // the user can predict the next design by clicking. Updates inside
  // transitionTo() the moment the target class is applied.
  // ─────────────────────────────────────────────────────────────────────
  const indicator = document.createElement('div');
  indicator.className = 'mode-indicator';
  indicator.innerHTML =
    '<span class="num" data-num></span>' +
    '<span class="sep">/</span>' +
    '<span class="name" data-name></span>';
  document.body.appendChild(indicator);
  const indicatorNum  = indicator.querySelector('[data-num]');
  const indicatorName = indicator.querySelector('[data-name]');

  function updateIndicator() {
    const { pos, name } = getModeLabel(getCurrentMode());
    if (indicatorNum)  indicatorNum.textContent  = pos;
    if (indicatorName) indicatorName.textContent = name;
  }
  updateIndicator();

  // ═══════════════════════════════════════════════════════════════════════
  // MATRIX
  // ═══════════════════════════════════════════════════════════════════════
  function createMatrixRain() {
    const canvas = document.createElement('canvas');
    overlay.appendChild(canvas);
    const ctx = canvas.getContext('2d', { alpha: true });

    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    // Smaller charSize → more columns → denser rain fill.
    // Desktop 1440px: ~110 columns (was ~90). Mobile 375px: ~34 (was ~26).
    const charSize = window.matchMedia('(max-width: 640px)').matches ? 11 : 13;

    const charPool =
      '01アイウエオカキクケコサシスセソタチツテトナニヌネノハヒフヘホマミムメモヤユヨラリルレロワヲン' +
      '<>{}/\\=*+-|.?:!ABCDEFGHIJKLMNOPRSTUVXYZ0123456789ДЕАДЛАЙН';
    const chars = charPool.split('');

    const easterWord = 'DEADLINE';
    let easterColumn = -1;
    let easterIndex = 0;
    let easterCooldown = 0;

    let drops = [];
    let columns = 0;
    let width = 0, height = 0;

    let alpha = 0;
    let speedMul = 1;
    let running = false;
    let frameId = null;

    function resize() {
      width = window.innerWidth;
      height = window.innerHeight;
      canvas.width = Math.floor(width * dpr);
      canvas.height = Math.floor(height * dpr);
      canvas.style.width = width + 'px';
      canvas.style.height = height + 'px';
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.scale(dpr, dpr);

      columns = Math.ceil(width / charSize);
      drops = new Array(columns).fill(0).map(() => ({
        // Pre-warm initial Y across the full viewport (some above,
        // some already mid-screen, some near bottom) so the screen
        // is populated with falling chars from frame 1. Without this
        // all drops start above viewport, and the 2.2s entry-theatre
        // window isn't long enough for them to reach the bottom.
        y: Math.random() * (height * 1.6) - height * 0.5,
        speed: 1.2 + Math.random() * 2.6,             // slightly faster (1.2-3.8)
        len: 10 + Math.floor(Math.random() * 22),     // longer streams (10-32)
      }));
      easterColumn = Math.floor(columns * 0.34);
    }

    function frame() {
      ctx.fillStyle = 'rgba(0, 0, 0, 0.085)';
      ctx.fillRect(0, 0, width, height);
      ctx.font = `${charSize - 2}px 'JetBrains Mono', ui-monospace, monospace`;
      ctx.textBaseline = 'top';

      for (let i = 0; i < columns; i++) {
        const drop = drops[i];
        const x = i * charSize;
        let ch;
        if (i === easterColumn && easterCooldown <= 0 && Math.random() < 0.12) {
          ch = easterWord[easterIndex];
          easterIndex = (easterIndex + 1) % easterWord.length;
          if (easterIndex === 0) easterCooldown = 280;
        } else {
          ch = chars[(Math.random() * chars.length) | 0];
        }
        ctx.fillStyle = `rgba(190, 255, 210, ${alpha})`;
        ctx.fillText(ch, x, drop.y);
        ctx.fillStyle = `rgba(0, 255, 65, ${alpha * 0.62})`;
        ctx.fillText(ch, x, drop.y - charSize);
        drop.y += drop.speed * speedMul;
        if (drop.y > height + drop.len * charSize) {
          drop.y = -drop.len * charSize - Math.random() * 200;
          drop.speed = 1 + Math.random() * 2.4;
        }
      }
      if (easterCooldown > 0) easterCooldown--;
      if (running) frameId = requestAnimationFrame(frame);
    }

    return {
      canvas,
      start() {
        if (running) return;
        running = true;
        resize();
        window.addEventListener('resize', resize);
        frameId = requestAnimationFrame(frame);
      },
      stop() {
        running = false;
        if (frameId) cancelAnimationFrame(frameId);
        window.removeEventListener('resize', resize);
      },
      destroy() { this.stop(); canvas.remove(); },
      setAlpha(a) { alpha = Math.max(0, Math.min(1, a)); },
      setSpeed(s) { speedMul = s; },
      async fadeAlphaTo(target, durationMs) {
        const start = alpha;
        const t0 = performance.now();
        return new Promise(resolve => {
          const tick = (t) => {
            const k = Math.min(1, (t - t0) / durationMs);
            alpha = start + (target - start) * k;
            if (k < 1) requestAnimationFrame(tick);
            else resolve();
          };
          requestAnimationFrame(tick);
        });
      },
    };
  }

  let activeRain = null;
  let idleMatrixRain = null;  // persistent low-alpha rain as Matrix ambient

  async function enterMatrix() {
    if (!activeRain) {
      activeRain = createMatrixRain();
      activeRain.setAlpha(0);
      activeRain.start();
    }
    // Make sure the overlay is up so the rain renders on the dark plate
    // (defensive — exitX from the previous mode usually already added it).
    overlay.classList.add('is-visible');

    // Mirror exitMatrix theatre: fade-in → FAST top-to-bottom sweep → fade-out.
    // Before this fix the entry ran at default speed 1× then slowed to 0.4×,
    // so drops only crawled mid-screen and never reached the bottom. With
    // setSpeed(3.2) the columns sweep the full viewport top→bottom for the
    // 800 ms dwell, matching the way exitMatrix looks.
    await activeRain.fadeAlphaTo(1, 500);
    activeRain.setSpeed(3.2);
    await wait(800);

    // Inject the cinematic video backdrop NOW (under the still-visible
    // overlay), so it's already playing when overlay fades to clear.
    // (Status-bar chrome stays disabled per user feedback — clean canvas.)
    injectMatrixVideo();
    injectThemeCursor('matrix');

    await activeRain.fadeAlphaTo(0, 500);
    overlay.classList.remove('is-visible');
    await wait(420);

    activeRain.destroy();
    activeRain = null;
    idleMatrixRain = null;
  }

  async function exitMatrix() {
    // Tear chrome FIRST
    removeMatrixChrome();
    removeMatrixVideo();
    removeThemeCursor();

    // Pull the persistent rain back into the overlay for the exit sequence
    if (idleMatrixRain) {
      activeRain = idleMatrixRain;
      idleMatrixRain = null;
      activeRain.canvas.classList.remove('matrix-rain-ambient');
      overlay.appendChild(activeRain.canvas);
    } else if (!activeRain) {
      activeRain = createMatrixRain();
      activeRain.setAlpha(0);
      activeRain.start();
    }
    overlay.classList.add('is-visible');
    await activeRain.fadeAlphaTo(1, 500);
    activeRain.setSpeed(3.2);
    await wait(800);
    await activeRain.fadeAlphaTo(0, 500);
    activeRain.destroy();
    activeRain = null;
  }

  // ═══════════════════════════════════════════════════════════════════════
  // ISLAND
  // ═══════════════════════════════════════════════════════════════════════
  const PALM_SVG = `
    <svg viewBox="0 0 1920 1080" preserveAspectRatio="xMidYMax slice"
         xmlns="http://www.w3.org/2000/svg">
      <path d="M0 760 Q 480 740, 960 752 Q 1440 760, 1920 745 L 1920 1080 L 0 1080 Z"
            fill="#06080f" opacity="0.92"/>
      <g transform="translate(220, 1080)">
        <path d="M0 0 Q -25 -180, -10 -380 T 0 -560"
              stroke="#06080f" stroke-width="14" fill="none" stroke-linecap="round"/>
        <path d="M0 -555 Q -90 -620, -200 -595 Q -110 -555, 0 -555 Z" fill="#06080f"/>
        <path d="M0 -555 Q  90 -620,  200 -595 Q 110 -555, 0 -555 Z" fill="#06080f"/>
        <path d="M0 -555 Q -55 -700, -130 -715 Q -45 -625, 0 -555 Z" fill="#06080f"/>
        <path d="M0 -555 Q  55 -700,  130 -715 Q  45 -625, 0 -555 Z" fill="#06080f"/>
        <path d="M0 -555 Q -120 -660, -195 -670 Q -55 -595, 0 -555 Z" fill="#06080f"/>
        <path d="M0 -555 Q  120 -660,  195 -670 Q  55 -595, 0 -555 Z" fill="#06080f"/>
        <circle cx="-8" cy="-545" r="9" fill="#06080f"/>
        <circle cx="8"  cy="-548" r="7" fill="#06080f"/>
      </g>
      <g transform="translate(1680, 1080) scale(-0.85, 0.85)">
        <path d="M0 0 Q -20 -180, -8 -370 T 0 -540"
              stroke="#06080f" stroke-width="13" fill="none" stroke-linecap="round"/>
        <path d="M0 -540 Q -85 -605, -190 -580 Q -100 -540, 0 -540 Z" fill="#06080f"/>
        <path d="M0 -540 Q  85 -605,  190 -580 Q 100 -540, 0 -540 Z" fill="#06080f"/>
        <path d="M0 -540 Q -50 -680, -120 -695 Q -40 -610, 0 -540 Z" fill="#06080f"/>
        <path d="M0 -540 Q  50 -680,  120 -695 Q  40 -610, 0 -540 Z" fill="#06080f"/>
        <path d="M0 -540 Q -110 -640, -180 -650 Q -50 -580, 0 -540 Z" fill="#06080f"/>
        <path d="M0 -540 Q  110 -640,  180 -650 Q  50 -580, 0 -540 Z" fill="#06080f"/>
      </g>
    </svg>
  `;

  const BIRD_SVG = `
    <svg viewBox="0 0 28 16" xmlns="http://www.w3.org/2000/svg">
      <path d="M2 12 Q 6 5, 10 9 Q 14 5, 18 9 Q 22 5, 26 12"
            fill="none" stroke="currentColor"
            stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/>
    </svg>
  `;

  function createIslandScene() {
    const scene = document.createElement('div');
    scene.className = 'island-scene';
    scene.innerHTML = `
      <div class="sky"></div>
      <div class="sea"></div>
      <div class="silhouette">${PALM_SVG}</div>
      <div class="sun"></div>
      <div class="flash"></div>
    `;
    overlay.appendChild(scene);
    void scene.offsetWidth;
    return scene;
  }

  let islandBirds = [];
  function injectIslandBirds() {
    removeIslandBirds();
    for (let i = 0; i < 2; i++) {
      const bird = document.createElement('div');
      bird.className = 'island-bird' + (i === 1 ? ' b2' : '');
      bird.innerHTML = BIRD_SVG;
      document.body.appendChild(bird);
      islandBirds.push(bird);
    }
  }
  function removeIslandBirds() {
    islandBirds.forEach(b => b.remove());
    islandBirds = [];
  }

  // ───────── Per-mode page chrome (injected/removed on enter/exit) ─────────

  let matrixChrome = null;
  let matrixChromeTimer = null;
  function injectMatrixChrome() {
    removeMatrixChrome();
    const bar = document.createElement('div');
    bar.className = 'matrix-statusbar';
    bar.innerHTML = `
      <span class="seg">[ DEADLINE.SH ]</span>
      <span class="seg">PID: 4096</span>
      <span class="seg ok">✓ READY</span>
      <span class="seg">PROJECTS=<b>12</b></span>
      <span class="seg">MISSED=<b>0</b></span>
      <span class="seg">RETURN=<b>100%</b></span>
      <span class="seg">CPU=<b id="mx-cpu">23%</b></span>
      <span class="seg">UPTIME=<b id="mx-up">00:00:00</b></span>
      <span class="seg blinker">█</span>
    `;
    document.body.appendChild(bar);
    matrixChrome = bar;
    // Live updates: random CPU jitter + uptime ticker so it feels alive
    const start = Date.now();
    matrixChromeTimer = setInterval(() => {
      if (!matrixChrome) return;
      const cpu = matrixChrome.querySelector('#mx-cpu');
      const up  = matrixChrome.querySelector('#mx-up');
      if (cpu) cpu.textContent = (18 + Math.floor(Math.random() * 14)) + '%';
      if (up) {
        const s = Math.floor((Date.now() - start) / 1000);
        up.textContent =
          String((s / 3600) | 0).padStart(2,'0') + ':' +
          String(((s / 60) | 0) % 60).padStart(2,'0') + ':' +
          String(s % 60).padStart(2,'0');
      }
    }, 900);
  }
  function removeMatrixChrome() {
    if (matrixChromeTimer) { clearInterval(matrixChromeTimer); matrixChromeTimer = null; }
    if (matrixChrome) { matrixChrome.remove(); matrixChrome = null; }
  }

  let islandChrome = null;
  function injectIslandChrome() {
    removeIslandChrome();
    const frame = document.createElement('div');
    frame.className = 'island-chrome';
    frame.innerHTML = `
      <div class="corner tl">✦</div>
      <div class="corner tr">✦</div>
      <div class="corner bl">✦</div>
      <div class="corner br">✦</div>
      <div class="signature">~ от руки ~</div>
    `;
    document.body.appendChild(frame);
    islandChrome = frame;
  }
  function removeIslandChrome() {
    if (islandChrome) { islandChrome.remove(); islandChrome = null; }
  }

  // ───────── Cinematic video backdrop for Matrix mode ─────────
  // Real HTML5 video (morpheus loop) for that "expensive, portfolio-grade"
  // feel. A canvas fallback (3D tunnel) kicks in only if video fails.
  let matrixVideo = null;
  let matrixVignette = null;
  let matrixTunnelStop = null;

  function injectMatrixVideo() {
    removeMatrixVideo();

    // 1) Real video element — autoplay, muted, looping, no controls.
    //    Mobile gets the 720p file (smaller, faster).
    const isMobile = window.innerWidth <= 768;
    const video = document.createElement('video');
    video.className = 'matrix-video-bg matrix-video-element';
    video.setAttribute('aria-hidden', 'true');
    video.autoplay = true;
    video.loop = true;
    video.muted = true;
    video.defaultMuted = true;
    video.playsInline = true;
    video.preload = 'auto';

    // (v3.7) On mobile use ONLY the 720p file (455 KB instead of 1.9 MB).
    // No 1080p fallback on phones — saves ~1.5 MB per matrix entry on
    // cellular. Desktop still gets a tiered 1080p loop + 1080p hero.
    const src1 = document.createElement('source');
    src1.src = isMobile
      ? 'assets/video/morpheus-hero-720p.mp4'
      : 'assets/video/morpheus-loop-1080p.mp4';
    src1.type = 'video/mp4';
    video.appendChild(src1);

    if (!isMobile) {
      const src2 = document.createElement('source');
      src2.src = 'assets/video/morpheus-hero-1080p.mp4';
      src2.type = 'video/mp4';
      video.appendChild(src2);
    }

    document.body.appendChild(video);
    matrixVideo = video;

    // If video errors out, fall back to the canvas tunnel
    video.addEventListener('error', () => {
      if (!matrixVideo) return;
      matrixVideo.remove();
      matrixVideo = null;
      injectMatrixTunnel();
    }, { once: true });

    // Force-play (muted+playsInline is allowed by browsers)
    const playPromise = video.play();
    if (playPromise && typeof playPromise.catch === 'function') {
      playPromise.catch(() => {
        const retry = () => { video.play().catch(() => {}); document.removeEventListener('click', retry); };
        document.addEventListener('click', retry, { once: true });
      });
    }

    // 2) Cinematic vignette overlay on top of video
    const vignette = document.createElement('div');
    vignette.className = 'matrix-video-vignette';
    vignette.setAttribute('aria-hidden', 'true');
    document.body.appendChild(vignette);
    matrixVignette = vignette;
  }

  // Canvas tunnel — fallback when video can't load
  function injectMatrixTunnel() {
    const canvas = document.createElement('canvas');
    canvas.className = 'matrix-video-bg matrix-tunnel-canvas';
    canvas.setAttribute('aria-hidden', 'true');
    document.body.appendChild(canvas);
    matrixVideo = canvas;
    const ctx = canvas.getContext('2d', { alpha: true });
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    let W = 0, H = 0, cx = 0, cy = 0;

    const RING_COUNT = 18;
    const SPEED = 0.012;
    let rings = [];
    function reset() {
      rings = [];
      for (let i = 0; i < RING_COUNT; i++) rings.push({ z: 1 - (i / RING_COUNT) });
    }
    function resize() {
      W = window.innerWidth; H = window.innerHeight;
      canvas.width = W * dpr; canvas.height = H * dpr;
      canvas.style.width = W + 'px'; canvas.style.height = H + 'px';
      ctx.setTransform(1, 0, 0, 1, 0, 0); ctx.scale(dpr, dpr);
      cx = W / 2; cy = H * 0.55;
    }
    function project(z) { return 1 / Math.max(z, 0.001); }
    function frame() {
      if (!running) return;
      ctx.clearRect(0, 0, W, H);
      const fog = ctx.createRadialGradient(cx, cy, 20, cx, cy, Math.max(W, H));
      fog.addColorStop(0, 'rgba(0, 70, 30, 0.18)');
      fog.addColorStop(0.4, 'rgba(0, 40, 18, 0.12)');
      fog.addColorStop(1, 'rgba(0, 0, 0, 0)');
      ctx.fillStyle = fog; ctx.fillRect(0, 0, W, H);
      ctx.strokeStyle = 'rgba(0, 255, 65, 0.18)'; ctx.lineWidth = 1;
      for (let s = 0; s < 16; s++) {
        const a = (s / 16) * Math.PI * 2;
        ctx.beginPath(); ctx.moveTo(cx, cy);
        ctx.lineTo(cx + Math.cos(a) * W, cy + Math.sin(a) * H); ctx.stroke();
      }
      for (const r of rings) {
        r.z -= SPEED; if (r.z <= 0.0) r.z = 1.0;
        const scale = project(r.z);
        const halfW = (W * 0.4) * scale, halfH = (H * 0.32) * scale;
        const left = cx - halfW, right = cx + halfW;
        const top = cy - halfH, bottom = cy + halfH;
        const fade = Math.min(1, (1 - r.z) * 1.6);
        ctx.strokeStyle = `rgba(0, 255, 65, ${0.55 * fade})`;
        ctx.lineWidth = 1 + fade * 1.2;
        ctx.shadowBlur = 14 * fade; ctx.shadowColor = 'rgba(0, 255, 65, 0.45)';
        ctx.strokeRect(left, top, right - left, bottom - top);
        ctx.fillStyle = `rgba(190, 255, 210, ${0.7 * fade})`;
        const sp = 2 + fade * 2;
        ctx.fillRect(left - sp / 2, top - sp / 2, sp, sp);
        ctx.fillRect(right - sp / 2, top - sp / 2, sp, sp);
        ctx.fillRect(left - sp / 2, bottom - sp / 2, sp, sp);
        ctx.fillRect(right - sp / 2, bottom - sp / 2, sp, sp);
      }
      ctx.shadowBlur = 0;
      raf = requestAnimationFrame(frame);
    }
    let running = true, raf = null;
    reset(); resize();
    window.addEventListener('resize', resize);
    raf = requestAnimationFrame(frame);
    matrixTunnelStop = () => {
      running = false;
      if (raf) cancelAnimationFrame(raf);
      window.removeEventListener('resize', resize);
    };
  }

  function removeMatrixVideo() {
    if (matrixTunnelStop) { matrixTunnelStop(); matrixTunnelStop = null; }
    if (matrixVideo) {
      try { if (typeof matrixVideo.pause === 'function') matrixVideo.pause(); } catch (e) {}
      matrixVideo.remove();
      matrixVideo = null;
    }
    if (matrixVignette) { matrixVignette.remove(); matrixVignette = null; }
  }

  // ───────── Themed cursor trail (canvas, per mode) ─────────
  let cursorTrail = null;
  function injectThemeCursor(theme) {
    removeThemeCursor();
    cursorTrail = new ThemeCursor(theme);
  }
  function removeThemeCursor() {
    if (cursorTrail) { cursorTrail.destroy(); cursorTrail = null; }
  }

  class ThemeCursor {
    constructor(theme) {
      this.theme = theme;
      this.particles = [];
      this.lastSpawn = 0;
      this.mouseX = -9999;
      this.mouseY = -9999;
      this.canvas = document.createElement('canvas');
      this.canvas.className = 'theme-cursor-canvas';
      document.body.appendChild(this.canvas);
      this.ctx = this.canvas.getContext('2d');
      this.dpr = Math.min(window.devicePixelRatio || 1, 2);
      this.resize();
      this.running = true;

      this._resize = () => this.resize();
      this._move = (e) => this.onMove(e);
      window.addEventListener('resize', this._resize);
      document.addEventListener('mousemove', this._move, { passive: true });

      this._frame = this.frame.bind(this);
      requestAnimationFrame(this._frame);

      // Matrix glyph pool for trail
      this.glyphs = '01アイウエオカキクケコ<>{}[]/\\=ABCDEF0123456789'.split('');
    }
    resize() {
      const w = window.innerWidth, h = window.innerHeight;
      this.w = w; this.h = h;
      this.canvas.width  = w * this.dpr;
      this.canvas.height = h * this.dpr;
      this.canvas.style.width  = w + 'px';
      this.canvas.style.height = h + 'px';
      this.ctx.setTransform(1, 0, 0, 1, 0, 0);
      this.ctx.scale(this.dpr, this.dpr);
    }
    onMove(e) {
      this.mouseX = e.clientX;
      this.mouseY = e.clientY;
      const now = performance.now();
      // Throttle spawn to ~60Hz
      if (now - this.lastSpawn < 14) return;
      this.lastSpawn = now;
      this.spawn();
    }
    spawn() {
      if (this.theme === 'matrix') {
        this.particles.push({
          x: this.mouseX + (Math.random() - 0.5) * 4,
          y: this.mouseY + (Math.random() - 0.5) * 4,
          vx: (Math.random() - 0.5) * 0.4,
          vy: 0.6 + Math.random() * 0.9,
          ch: this.glyphs[(Math.random() * this.glyphs.length) | 0],
          life: 1,
          decay: 0.018 + Math.random() * 0.018,
        });
      } else if (this.theme === 'island') {
        // golden water dot, multiple at once for a soft sparkle
        for (let i = 0; i < 2; i++) {
          this.particles.push({
            x: this.mouseX + (Math.random() - 0.5) * 8,
            y: this.mouseY + (Math.random() - 0.5) * 8,
            vx: (Math.random() - 0.5) * 0.6,
            vy: (Math.random() - 0.5) * 0.6,
            r: 4 + Math.random() * 5,
            life: 1,
            decay: 0.022 + Math.random() * 0.015,
          });
        }
      } else if (this.theme === 'studio') {
        this.particles.push({
          x: this.mouseX,
          y: this.mouseY,
          r: 3 + Math.random() * 3,
          life: 1,
          decay: 0.045 + Math.random() * 0.02,
        });
      }
    }
    frame() {
      if (!this.running) return;
      this.ctx.clearRect(0, 0, this.w, this.h);

      this.particles = this.particles.filter(p => {
        p.life -= p.decay;
        return p.life > 0;
      });

      if (this.theme === 'matrix') {
        this.ctx.font = '15px "Share Tech Mono", monospace';
        this.ctx.textBaseline = 'top';
        for (const p of this.particles) {
          p.x += p.vx; p.y += p.vy;
          const a = p.life;
          this.ctx.fillStyle = `rgba(190, 255, 210, ${a})`;
          this.ctx.fillText(p.ch, p.x, p.y);
          if (Math.random() < 0.18) p.ch = this.glyphs[(Math.random() * this.glyphs.length) | 0];
        }
      } else if (this.theme === 'island') {
        for (const p of this.particles) {
          p.x += p.vx; p.y += p.vy;
          const r = p.r * (1.4 - p.life * 0.6);
          const grad = this.ctx.createRadialGradient(p.x, p.y, 0, p.x, p.y, r);
          grad.addColorStop(0, `rgba(255, 230, 160, ${p.life * 0.55})`);
          grad.addColorStop(0.6, `rgba(229, 184, 104, ${p.life * 0.22})`);
          grad.addColorStop(1, 'rgba(201, 137, 45, 0)');
          this.ctx.fillStyle = grad;
          this.ctx.beginPath();
          this.ctx.arc(p.x, p.y, r, 0, Math.PI * 2);
          this.ctx.fill();
        }
      } else if (this.theme === 'studio') {
        for (const p of this.particles) {
          const r = p.r * (1 + (1 - p.life) * 6);
          const grad = this.ctx.createRadialGradient(p.x, p.y, 0, p.x, p.y, r);
          grad.addColorStop(0, `rgba(0, 122, 255, ${p.life * 0.28})`);
          grad.addColorStop(0.7, `rgba(90, 200, 250, ${p.life * 0.10})`);
          grad.addColorStop(1, 'rgba(255, 255, 255, 0)');
          this.ctx.fillStyle = grad;
          this.ctx.beginPath();
          this.ctx.arc(p.x, p.y, r, 0, Math.PI * 2);
          this.ctx.fill();
        }
      }

      requestAnimationFrame(this._frame);
    }
    destroy() {
      this.running = false;
      window.removeEventListener('resize', this._resize);
      document.removeEventListener('mousemove', this._move);
      this.canvas.remove();
    }
  }

  // ───────── Studio 3D-tilt cards (cursor-driven perspective) ─────────
  let tiltTargets = [];
  let tiltHandlers = new Map();

  function enableStudio3DTilt() {
    disableStudio3DTilt();
    tiltTargets = [...document.querySelectorAll(
      '.svc-card, .testimonial-card, .work-grid > *, .process-step'
    )];
    tiltTargets.forEach(el => {
      el.style.transformStyle = 'preserve-3d';
      el.style.transition = 'transform 220ms cubic-bezier(0.22, 1, 0.36, 1)';

      const onMove = (e) => {
        const rect = el.getBoundingClientRect();
        const x = ((e.clientX - rect.left) / rect.width  - 0.5);
        const y = ((e.clientY - rect.top)  / rect.height - 0.5);
        el.style.transform =
          `perspective(900px) rotateY(${x * 6}deg) rotateX(${-y * 6}deg) translateZ(0)`;
        el.style.transition = 'transform 60ms linear';
      };
      const onLeave = () => {
        el.style.transition = 'transform 360ms cubic-bezier(0.22, 1, 0.36, 1)';
        el.style.transform = '';
      };
      el.addEventListener('mousemove', onMove);
      el.addEventListener('mouseleave', onLeave);
      tiltHandlers.set(el, { onMove, onLeave });
    });
  }
  function disableStudio3DTilt() {
    tiltTargets.forEach(el => {
      const h = tiltHandlers.get(el);
      if (h) {
        el.removeEventListener('mousemove', h.onMove);
        el.removeEventListener('mouseleave', h.onLeave);
      }
      el.style.transform = '';
      el.style.transformStyle = '';
      el.style.transition = '';
    });
    tiltTargets = [];
    tiltHandlers.clear();
  }

  // ───────── Studio magnetic CTA (button pulls toward the cursor) ─────────
  let magneticTargets = [];
  let magneticHandlers = new Map();

  function enableMagneticCTA() {
    disableMagneticCTA();
    magneticTargets = [...document.querySelectorAll('.btn-primary')];
    magneticTargets.forEach(el => {
      el.style.transition = 'transform 220ms cubic-bezier(0.22, 1, 0.36, 1)';
      const onMove = (e) => {
        const rect = el.getBoundingClientRect();
        const cx = rect.left + rect.width / 2;
        const cy = rect.top  + rect.height / 2;
        const dx = (e.clientX - cx) * 0.28;
        const dy = (e.clientY - cy) * 0.28;
        el.style.transition = 'transform 90ms linear';
        el.style.transform = `translate(${dx.toFixed(1)}px, ${dy.toFixed(1)}px)`;
      };
      const onLeave = () => {
        el.style.transition = 'transform 360ms cubic-bezier(0.34, 1.56, 0.64, 1)';
        el.style.transform = '';
      };
      el.addEventListener('mousemove', onMove);
      el.addEventListener('mouseleave', onLeave);
      magneticHandlers.set(el, { onMove, onLeave });
    });
  }
  function disableMagneticCTA() {
    magneticTargets.forEach(el => {
      const h = magneticHandlers.get(el);
      if (h) {
        el.removeEventListener('mousemove', h.onMove);
        el.removeEventListener('mouseleave', h.onLeave);
      }
      el.style.transform = '';
      el.style.transition = '';
    });
    magneticTargets = [];
    magneticHandlers.clear();
  }

  let studioChrome = null;
  let studioProgressHandler = null;
  function injectStudioChrome() {
    removeStudioChrome();
    const bar = document.createElement('div');
    bar.className = 'studio-progress';
    bar.innerHTML = `<div class="studio-progress-fill"></div>`;
    document.body.appendChild(bar);
    studioChrome = bar;
    const fill = bar.querySelector('.studio-progress-fill');

    studioProgressHandler = () => {
      const max = Math.max(document.documentElement.scrollHeight - window.innerHeight, 1);
      const p = Math.min(1, Math.max(0, window.scrollY / max));
      if (fill) fill.style.transform = `scaleX(${p.toFixed(4)})`;
    };
    document.addEventListener('scroll', studioProgressHandler, { passive: true });
    studioProgressHandler();
  }
  function removeStudioChrome() {
    if (studioProgressHandler) {
      document.removeEventListener('scroll', studioProgressHandler);
      studioProgressHandler = null;
    }
    if (studioChrome) { studioChrome.remove(); studioChrome = null; }
  }

  // (v3.5 2026-05-13) Island mode = "Resort" skin — the 4th of 4 skins.
  // Loads Prototypes/Resort_skins/00_master.html into a full-viewport
  // <iframe>. Partner's old sunset-island theatre (createIslandScene +
  // injectIslandBirds + injectIslandChrome + injectThemeCursor) replaced
  // entirely. Magic-toggle stays in body root, above the iframe via CSS
  // z-index, so the user can keep cycling through skins.
  async function enterIsland() {
    overlay.style.background = '#08070A';
    overlay.classList.add('is-visible');
    await wait(380);

    let frame = document.querySelector('.mx-resort-frame');
    if (!frame) {
      frame = document.createElement('iframe');
      frame.className = 'mx-resort-frame';
      // (v3.6.2 2026-05-13) Cache-bust the iframe src — without ?v=
      // Chrome aggressively caches HTML loaded inside iframes, even
      // when ETag/Last-Modified would otherwise invalidate. Bump this
      // string whenever 00_master.html is meaningfully edited.
      frame.src = 'Prototypes/Resort_skins/00_master.html?v=3.7.47';
      frame.setAttribute('title', 'DEADLINE — Resort');
      frame.setAttribute('loading', 'eager');
      document.body.appendChild(frame);
    }

    await wait(160);
    overlay.classList.remove('is-visible');
    await wait(200);
    overlay.style.background = '';
  }

  async function exitIsland() {
    overlay.style.background = '#08070A';
    overlay.classList.add('is-visible');
    await wait(380);

    const frame = document.querySelector('.mx-resort-frame');
    if (frame) frame.remove();

    await wait(160);
    overlay.classList.remove('is-visible');
    await wait(200);
    overlay.style.background = '';
  }

  // ═══════════════════════════════════════════════════════════════════════
  // STUDIO
  // ═══════════════════════════════════════════════════════════════════════
  function createStudioScene() {
    const scene = document.createElement('div');
    scene.className = 'studio-scene';
    scene.innerHTML = `<div class="curtain"></div>`;
    overlay.appendChild(scene);
    void scene.offsetWidth;
    return scene;
  }

  function setBodyChildIndices() {
    const children = [...document.body.children].filter(el =>
      !el.classList || (
        !el.classList.contains('mode-overlay') &&
        !el.classList.contains('island-bird') &&
        !el.classList.contains('studio-spot')
      )
    );
    const total = children.length;
    children.forEach((el, i) => {
      el.style.setProperty('--idx', i);
      el.style.setProperty('--idx-rev', total - 1 - i);
    });
    return children;
  }

  function clearBodyChildIndices() {
    [...document.body.children].forEach(el => {
      el.style.removeProperty('--idx');
      el.style.removeProperty('--idx-rev');
    });
  }

  // Studio idle: a JS-driven follow-spot halo
  let studioSpot = null;
  let studioMouseHandler = null;
  let studioScrollHandler = null;
  let studioObserver = null;
  let studioParallaxHandler = null;

  function injectStudioIdle() {
    removeStudioIdle();

    // Follow-spot
    studioSpot = document.createElement('div');
    studioSpot.className = 'studio-spot';
    document.body.appendChild(studioSpot);

    let pendingMove = null;
    studioMouseHandler = (e) => {
      pendingMove = { x: e.clientX, y: e.clientY };
      requestAnimationFrame(() => {
        if (!pendingMove || !studioSpot) return;
        studioSpot.style.transform = `translate(${pendingMove.x}px, ${pendingMove.y}px)`;
        pendingMove = null;
      });
    };
    document.addEventListener('mousemove', studioMouseHandler, { passive: true });

    // Background parallax
    studioParallaxHandler = (e) => {
      const x = (e.clientX / window.innerWidth - 0.5) * 8;
      const y = (e.clientY / window.innerHeight - 0.5) * 5;
      document.body.style.setProperty('--studio-px', x.toFixed(2) + 'px');
      document.body.style.setProperty('--studio-py', y.toFixed(2) + 'px');
    };
    document.addEventListener('mousemove', studioParallaxHandler, { passive: true });

    // Scroll → frosted nav
    studioScrollHandler = () => {
      if (window.scrollY > 24) BODY.classList.add('studio-scrolled');
      else                     BODY.classList.remove('studio-scrolled');
    };
    document.addEventListener('scroll', studioScrollHandler, { passive: true });
    studioScrollHandler(); // initial check

    // Reveal-on-scroll: tag major children + observe
    const revealTargets = document.querySelectorAll(
      'section, .hero-meta, .stats-row, .svc-card, .work-card, .testimonial, .proc-step'
    );
    revealTargets.forEach(el => el.classList.add('studio-reveal'));

    studioObserver = new IntersectionObserver((entries) => {
      entries.forEach(entry => {
        if (entry.isIntersecting) {
          entry.target.classList.add('is-in');
          studioObserver.unobserve(entry.target);
        }
      });
    }, { threshold: 0.12, rootMargin: '0px 0px -10% 0px' });
    revealTargets.forEach(el => studioObserver.observe(el));
  }

  function removeStudioIdle() {
    if (studioSpot) { studioSpot.remove(); studioSpot = null; }
    if (studioMouseHandler) {
      document.removeEventListener('mousemove', studioMouseHandler);
      studioMouseHandler = null;
    }
    if (studioParallaxHandler) {
      document.removeEventListener('mousemove', studioParallaxHandler);
      studioParallaxHandler = null;
    }
    if (studioScrollHandler) {
      document.removeEventListener('scroll', studioScrollHandler);
      studioScrollHandler = null;
    }
    if (studioObserver) {
      studioObserver.disconnect();
      studioObserver = null;
    }
    BODY.classList.remove('studio-scrolled');
    document.body.style.removeProperty('--studio-px');
    document.body.style.removeProperty('--studio-py');
    document.querySelectorAll('.studio-reveal').forEach(el => {
      el.classList.remove('studio-reveal', 'is-in');
    });
  }

  async function enterStudio() {
    // Prepare child indices for the stagger animation
    setBodyChildIndices();

    const scene = createStudioScene();
    overlay.classList.add('is-visible');   // ensure overlay is solid black
    await wait(40);

    // 1. Thin white line draws across the centre of the dark stage
    scene.classList.add('is-line');
    await wait(500);
    // 2. Pause — anticipation
    await wait(300);
    // 3. Line opens vertically — keynote crystal opens
    scene.classList.add('is-opening');
    await wait(820);
    // 4. Briefest hold on full white
    await wait(160);

    // 5. Hide overlay so the now-studio-themed page reveals underneath,
    //    and trigger the body-child stagger animation simultaneously
    HTML.classList.add('is-revealing');
    overlay.classList.remove('is-visible');
    await wait(700);

    // Cleanup
    scene.remove();
    HTML.classList.remove('is-revealing');
    clearBodyChildIndices();

    // Install idle signatures + chrome + cursor + 3D tilt + magnetic CTA
    injectStudioIdle();
    injectStudioChrome();
    injectThemeCursor('studio');
    enableStudio3DTilt();
    enableMagneticCTA();
  }

  async function exitStudio() {
    // Tear idle + chrome FIRST so spot/parallax don't flicker through the transition
    removeStudioIdle();
    removeStudioChrome();
    removeThemeCursor();
    disableStudio3DTilt();
    disableMagneticCTA();
    setBodyChildIndices();

    // 1. Body children rise off-screen in reverse stagger
    HTML.classList.add('is-collapsing-content');
    await wait(50);

    // 2. Bring up overlay with a full-white curtain that will collapse into a line
    const scene = createStudioScene();
    scene.classList.add('is-collapsing'); // start as full white panel
    overlay.classList.add('is-visible');
    await wait(560); // let body children finish rising

    // 3. Curtain shrinks back to a thin line
    scene.classList.add('is-line-collapse');
    await wait(620);

    // 4. Line dies (scaleX → 0)
    scene.classList.add('is-line-gone');
    await wait(380);

    // Cleanup — overlay stays black for the next phase
    scene.remove();
    HTML.classList.remove('is-collapsing-content');
    clearBodyChildIndices();
  }

  // ═══════════════════════════════════════════════════════════════════════
  // NOIR — Ultranoir-inspired
  //   Signature: difference-mode cursor (dot + ring with mix-blend-mode:
  //   difference) + magnetic hover on headlines/CTAs + expo-out scroll
  //   reveal. Two divs, vanilla lerp, IntersectionObserver. Zero WebGL.
  // ═══════════════════════════════════════════════════════════════════════
  let noirCursor = null;
  let noirMagnetic = null;
  let noirBook = null;
  let noirScrollNav = null;
  let noirRevealIO = null;
  let noirReveals = null;             // GSAP-driven reveals disposer (if GSAP loaded)
  let noirGrainEl = null;
  let noirRunningEl = null;
  let noirPagesEl = null;
  let noirProgressEl = null;
  let noirProgressHandler = null;
  let noirScene = null;               // Three.js scene controller for the cover
  let magicBtnHome = null;
  let CONTENT = null;

  // ─────────────────────────────────────────────────────────────────────
  // 3D — dynamic Three.js loader. Three.js ESM module is fetched only
  // when noir mode is entered for the first time, then cached on window.
  // ─────────────────────────────────────────────────────────────────────
  let _threePromise = null;
  function loadThree() {
    if (window.THREE) return Promise.resolve(window.THREE);
    if (_threePromise) return _threePromise;
    _threePromise = import('https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.module.min.js')
      .then(mod => { window.THREE = mod; return mod; })
      .catch(err => { console.warn('[noir] Three.js failed to load:', err); _threePromise = null; throw err; });
    return _threePromise;
  }

  // ─────────────────────────────────────────────────────────────────────
  // mountNoirScene — wireframe icosahedron behind the DEADLINE display
  // on the cover chapter. Slow continuous rotation + mouse parallax +
  // subtle breathing scale. Pure WebGL via Three.js. Replaceable later
  // with a <spline-viewer> element loading the user's Spline scene URL.
  // Returns { canvas, destroy }.
  // ─────────────────────────────────────────────────────────────────────
  // ─────────────────────────────────────────────────────────────────────
  // SCENE_BUILDERS — each returns { update(t, mx, my), dispose() } where
  // t is elapsed time, mx/my are lerped normalized mouse coords [-1..1].
  // All scenes use the warm-gold palette (#B8985C primary, #C9AB6E
  // highlight, #8A704A deep). They're swapped into a single shared scene
  // graph by mountNoirBackdrop() based on the active chapter.
  // ─────────────────────────────────────────────────────────────────────
  const GOLD = 0xB8985C, GOLD_HI = 0xC9AB6E, GOLD_DK = 0x8A704A;

  const SCENE_BUILDERS = {
    // 00 COVER — wireframe icosahedron + inner ghost-sphere
    icosahedron(T, scene) {
      const geo = new T.IcosahedronGeometry(1.7, 1);
      const eg  = new T.EdgesGeometry(geo);
      const outer = new T.LineSegments(eg, new T.LineBasicMaterial({ color: GOLD, transparent: true, opacity: 0.42 }));
      const innerGeo = new T.SphereGeometry(0.9, 18, 14);
      const innerEg  = new T.WireframeGeometry(innerGeo);
      const inner = new T.LineSegments(innerEg, new T.LineBasicMaterial({ color: GOLD_HI, transparent: true, opacity: 0.18 }));
      scene.add(outer, inner);
      return {
        update(t, mx, my) {
          outer.rotation.y = t * 0.4 + mx * 0.5;
          outer.rotation.x = mx * 0.28 + my * 0.42 + Math.sin(t * 0.3) * 0.05;
          outer.rotation.z = Math.cos(t * 0.2) * 0.02;
          const s = 1 + Math.sin(t * 0.45) * 0.018;
          outer.scale.set(s, s, s);
          inner.rotation.y = -t * 0.18;
          inner.rotation.x =  t * 0.12 + my * -0.2;
        },
        dispose() { [geo, eg, innerGeo, innerEg, outer.material, inner.material].forEach(o => o.dispose()); },
      };
    },

    // 01 MANIFESTO — neural dot-network with proximity-linked edges
    dotNetwork(T, scene) {
      const N = 60, maxLines = 220;
      const pos = new Float32Array(N * 3);
      const vel = new Float32Array(N * 3);
      for (let i = 0; i < N; i++) {
        pos[i*3]   = (Math.random() - 0.5) * 5.2;
        pos[i*3+1] = (Math.random() - 0.5) * 3.5;
        pos[i*3+2] = (Math.random() - 0.5) * 3.5;
        vel[i*3]   = (Math.random() - 0.5) * 0.006;
        vel[i*3+1] = (Math.random() - 0.5) * 0.006;
        vel[i*3+2] = (Math.random() - 0.5) * 0.006;
      }
      const ptGeo = new T.BufferGeometry();
      ptGeo.setAttribute('position', new T.BufferAttribute(pos, 3));
      const points = new T.Points(ptGeo, new T.PointsMaterial({ color: GOLD_HI, size: 0.045, transparent: true, opacity: 0.85, sizeAttenuation: true }));
      const lineGeo = new T.BufferGeometry();
      const linePos = new Float32Array(maxLines * 6);
      lineGeo.setAttribute('position', new T.BufferAttribute(linePos, 3));
      const lines = new T.LineSegments(lineGeo, new T.LineBasicMaterial({ color: GOLD, transparent: true, opacity: 0.28 }));
      const group = new T.Group();
      group.add(points, lines);
      scene.add(group);
      const thr = 1.25, thr2 = thr * thr;
      return {
        update(t, mx, my) {
          for (let i = 0; i < N; i++) {
            pos[i*3]   += vel[i*3];
            pos[i*3+1] += vel[i*3+1];
            pos[i*3+2] += vel[i*3+2];
            if (Math.abs(pos[i*3])   > 2.6) vel[i*3]   *= -1;
            if (Math.abs(pos[i*3+1]) > 1.8) vel[i*3+1] *= -1;
            if (Math.abs(pos[i*3+2]) > 1.8) vel[i*3+2] *= -1;
          }
          ptGeo.attributes.position.needsUpdate = true;
          let li = 0;
          for (let i = 0; i < N && li < maxLines; i++) {
            for (let j = i + 1; j < N && li < maxLines; j++) {
              const dx = pos[i*3] - pos[j*3];
              const dy = pos[i*3+1] - pos[j*3+1];
              const dz = pos[i*3+2] - pos[j*3+2];
              if (dx*dx + dy*dy + dz*dz < thr2) {
                linePos[li*6  ] = pos[i*3];   linePos[li*6+1] = pos[i*3+1]; linePos[li*6+2] = pos[i*3+2];
                linePos[li*6+3] = pos[j*3];   linePos[li*6+4] = pos[j*3+1]; linePos[li*6+5] = pos[j*3+2];
                li++;
              }
            }
          }
          for (let i = li * 6; i < maxLines * 6; i++) linePos[i] = 0;
          lineGeo.attributes.position.needsUpdate = true;
          group.rotation.y = t * 0.06 + mx * 0.22;
          group.rotation.x = my * 0.15;
        },
        dispose() { ptGeo.dispose(); lineGeo.dispose(); points.material.dispose(); lines.material.dispose(); },
      };
    },

    // 02 WEB — wave-grid floor ("spacetime fabric") + WORMHOLE funnel.
    // LatheGeometry sweeps a hyperbolic throat profile around Y, giving
    // a faceted hourglass shape with concentric rings + radial spokes.
    // Glowing core at the throat + halo ring at the rim. Slow Y-axis
    // rotation gives the Interstellar-tier "fabric is being pulled
    // through a hole" sensation.
    waveGrid(T, scene) {
      // ─── FLOOR — wave-grid wireframe (fabric of spacetime) ───
      const w = 7, h = 4.5, segX = 56, segY = 28;
      const geo = new T.PlaneGeometry(w, h, segX, segY);
      const mesh = new T.LineSegments(
        new T.WireframeGeometry(geo),
        new T.LineBasicMaterial({ color: GOLD, transparent: true, opacity: 0.4 })
      );
      mesh.rotation.x = -Math.PI / 2.5;
      scene.add(mesh);
      const basePositions = geo.attributes.position.array.slice();

      // ─── WORMHOLE THROAT — hyperbolic lathe ───
      // r(y) = sqrt(a^2 + (y * b)^2)  → narrow waist at y=0, flares at extremes.
      // Sampled along Y, then LatheGeometry sweeps 360° around Y for the
      // 3D funnel. Wireframe gives the iconic grid look (latitude rings
      // + longitude spokes).
      const Nh = 28;
      const profile = [];
      for (let i = 0; i <= Nh; i++) {
        const y = -1.5 + (i / Nh) * 3.0;          // -1.5 .. +1.5
        const r = Math.sqrt(0.18 + (y * 0.85) ** 2);
        profile.push(new T.Vector2(r, y));
      }
      const latheGeo = new T.LatheGeometry(profile, 40);
      const throat = new T.LineSegments(
        new T.WireframeGeometry(latheGeo),
        new T.LineBasicMaterial({ color: GOLD, transparent: true, opacity: 0.55 })
      );

      // ─── CORE — bright glowing center at the throat ───
      const coreGeo = new T.SphereGeometry(0.32, 20, 18);
      const coreMat = new T.MeshBasicMaterial({
        color: 0xFFEBC0, transparent: true, opacity: 0.85,
        blending: T.AdditiveBlending,
      });
      const core = new T.Mesh(coreGeo, coreMat);

      // ─── HALO — bright ring sitting at the throat rim ───
      const haloGeo = new T.TorusGeometry(0.46, 0.02, 6, 56);
      const haloMat = new T.LineBasicMaterial({
        color: GOLD_HI, transparent: true, opacity: 0.9,
      });
      const halo = new T.LineSegments(new T.WireframeGeometry(haloGeo), haloMat);
      halo.rotation.x = Math.PI / 2;            // ring horizontal at y=0

      // ─── OUTER HALO — wider, dimmer secondary ring ───
      const halo2Geo = new T.TorusGeometry(0.72, 0.008, 4, 64);
      const halo2 = new T.LineSegments(
        new T.WireframeGeometry(halo2Geo),
        new T.LineBasicMaterial({ color: GOLD, transparent: true, opacity: 0.4 })
      );
      halo2.rotation.x = Math.PI / 2;

      // Group + tilt — top of wormhole leans slightly toward camera so
      // the viewer "looks into" the throat.
      const wormholeGroup = new T.Group();
      wormholeGroup.add(throat, halo2, halo, core);
      wormholeGroup.position.set(0, 0.5, 0.4);
      wormholeGroup.rotation.x = -Math.PI / 6;  // -30° tilt
      scene.add(wormholeGroup);

      return {
        update(t, mx, my) {
          // Wave-grid floor displacement (unchanged)
          const arr = geo.attributes.position.array;
          for (let i = 0; i < arr.length; i += 3) {
            const x = basePositions[i];
            const z = basePositions[i + 1];
            arr[i + 2] = Math.sin(x * 1.4 + t * 1.2) * 0.18 + Math.cos(z * 1.6 + t * 0.9) * 0.18;
          }
          geo.attributes.position.needsUpdate = true;
          mesh.rotation.z = mx * 0.18;
          mesh.position.y = -0.5 + my * 0.25;

          // Wormhole — slow spin around its vertical axis (the throat axis)
          // gives the "spacetime fabric pulled through a hole" rotation.
          // x-tilt softly responds to mouse for parallax depth.
          wormholeGroup.rotation.y = t * 0.42;
          wormholeGroup.rotation.x = -Math.PI / 6 + my * 0.14;
          wormholeGroup.rotation.z = mx * 0.08;

          // Core pulse — additive-blended sphere breathes 0.78x → 1.22x.
          // Opacity left to the crossfade system so scene transitions
          // fade in/out cleanly without fighting our local animation.
          core.scale.setScalar(1 + Math.sin(t * 1.9) * 0.22);

          // Primary halo counter-rotates (subtle gold flicker)
          halo.rotation.z = -t * 0.55;
          halo.scale.setScalar(1 + Math.sin(t * 1.3) * 0.07);

          // Outer halo slow counter
          halo2.rotation.z = t * 0.28;

          // Scene mouse parallax
          scene.rotation.y = mx * 0.06;
        },
        dispose() {
          geo.dispose();
          mesh.material.dispose();
          mesh.geometry.dispose();
          latheGeo.dispose();
          throat.geometry.dispose();
          throat.material.dispose();
          coreGeo.dispose();
          coreMat.dispose();
          haloGeo.dispose();
          halo.geometry.dispose();
          haloMat.dispose();
          halo2Geo.dispose();
          halo2.geometry.dispose();
          halo2.material.dispose();
        },
      };
    },

    // 03 AUTOMATION — 3 orbital rings (gimbal)
    orbitalRings(T, scene) {
      const mat = new T.LineBasicMaterial({ color: GOLD, transparent: true, opacity: 0.5 });
      const matHi = new T.LineBasicMaterial({ color: GOLD_HI, transparent: true, opacity: 0.32 });
      const ringGeo1 = new T.TorusGeometry(1.7, 0.012, 6, 80);
      const ringGeo2 = new T.TorusGeometry(1.3, 0.01,  6, 70);
      const ringGeo3 = new T.TorusGeometry(0.95, 0.01, 6, 60);
      const r1 = new T.Mesh(ringGeo1, mat);
      const r2 = new T.Mesh(ringGeo2, matHi); r2.rotation.x = Math.PI / 2;
      const r3 = new T.Mesh(ringGeo3, mat);   r3.rotation.y = Math.PI / 2;
      const dotGeo = new T.SphereGeometry(0.04, 12, 12);
      const dotMat = new T.MeshBasicMaterial({ color: GOLD_HI });
      const dot = new T.Mesh(dotGeo, dotMat);
      scene.add(r1, r2, r3, dot);
      return {
        update(t, mx, my) {
          r1.rotation.z = t * 0.25 + mx * 0.3;
          r2.rotation.z = t * -0.18 + my * 0.25;
          r3.rotation.x = t * 0.32 + mx * 0.25;
          const ang = t * 0.55;
          dot.position.set(Math.cos(ang) * 1.7, Math.sin(ang * 0.7) * 0.4, Math.sin(ang) * 1.7);
        },
        dispose() { [ringGeo1, ringGeo2, ringGeo3, dotGeo, mat, matHi, dotMat].forEach(o => o.dispose()); },
      };
    },

    // 04 AI — particle swarm in pseudo-noise field
    particleSwarm(T, scene) {
      const N = 480;
      const pos = new Float32Array(N * 3);
      const seeds = new Float32Array(N * 3);
      for (let i = 0; i < N; i++) {
        const r = 1.3 + Math.random() * 1.5;
        const a = Math.random() * Math.PI * 2;
        const b = (Math.random() - 0.5) * Math.PI;
        pos[i*3]   = Math.cos(a) * Math.cos(b) * r;
        pos[i*3+1] = Math.sin(b) * r * 0.7;
        pos[i*3+2] = Math.sin(a) * Math.cos(b) * r;
        seeds[i*3]   = Math.random() * Math.PI * 2;
        seeds[i*3+1] = Math.random() * Math.PI * 2;
        seeds[i*3+2] = Math.random() * Math.PI * 2;
      }
      const geo = new T.BufferGeometry();
      geo.setAttribute('position', new T.BufferAttribute(pos, 3));
      const points = new T.Points(geo, new T.PointsMaterial({ color: GOLD_HI, size: 0.028, transparent: true, opacity: 0.7, sizeAttenuation: true }));
      scene.add(points);
      const base = pos.slice();
      return {
        update(t, mx, my) {
          const arr = geo.attributes.position.array;
          for (let i = 0; i < N; i++) {
            arr[i*3]   = base[i*3]   + Math.sin(t * 0.6 + seeds[i*3])   * 0.18;
            arr[i*3+1] = base[i*3+1] + Math.cos(t * 0.7 + seeds[i*3+1]) * 0.18;
            arr[i*3+2] = base[i*3+2] + Math.sin(t * 0.5 + seeds[i*3+2]) * 0.18;
          }
          geo.attributes.position.needsUpdate = true;
          points.rotation.y = t * 0.08 + mx * 0.25;
          points.rotation.x = my * 0.18;
        },
        dispose() { geo.dispose(); points.material.dispose(); },
      };
    },

    // 05 VRP — voxel stack (8 thin rotating layers, property floors)
    voxelStack(T, scene) {
      const mat = new T.LineBasicMaterial({ color: GOLD, transparent: true, opacity: 0.42 });
      const matHi = new T.LineBasicMaterial({ color: GOLD_HI, transparent: true, opacity: 0.6 });
      const boxes = [];
      const geos = [];
      for (let i = 0; i < 9; i++) {
        const sz = 1.8 - i * 0.085;
        const g = new T.BoxGeometry(sz, 0.04, sz);
        const eg = new T.EdgesGeometry(g);
        const m = new T.LineSegments(eg, i % 2 === 0 ? mat : matHi);
        m.position.y = -1.4 + i * 0.35;
        scene.add(m);
        boxes.push(m);
        geos.push(g, eg);
      }
      return {
        update(t, mx, my) {
          boxes.forEach((b, i) => {
            b.rotation.y = t * (0.15 + i * 0.03) + mx * 0.18;
            b.position.x = Math.sin(t * 0.4 + i * 0.5) * 0.05;
            b.position.z = Math.cos(t * 0.35 + i * 0.6) * 0.05;
          });
        },
        dispose() { geos.forEach(g => g.dispose()); mat.dispose(); matHi.dispose(); },
      };
    },

    // 06 KD — flow field (horizontal scanlines drifting like a stream)
    flowField(T, scene) {
      const L = 26;
      const mat = new T.LineBasicMaterial({ color: GOLD, transparent: true, opacity: 0.35 });
      const matHi = new T.LineBasicMaterial({ color: GOLD_HI, transparent: true, opacity: 0.55 });
      const lines = [];
      const geos = [];
      for (let i = 0; i < L; i++) {
        const pts = [];
        const y = -1.6 + i * 0.13;
        for (let x = -3; x <= 3; x += 0.08) pts.push(new T.Vector3(x, y, 0));
        const g = new T.BufferGeometry().setFromPoints(pts);
        const l = new T.Line(g, i % 5 === 0 ? matHi : mat);
        scene.add(l);
        lines.push({ line: l, y, base: pts.map(p => p.clone()), geo: g, phase: Math.random() * Math.PI * 2 });
        geos.push(g);
      }
      return {
        update(t, mx, my) {
          lines.forEach((ln, i) => {
            const arr = ln.geo.attributes.position.array;
            for (let k = 0; k < ln.base.length; k++) {
              const bx = ln.base[k].x;
              arr[k*3+1] = ln.y + Math.sin(bx * 0.6 + t * 1.4 + ln.phase) * 0.06;
              arr[k*3+2] = Math.cos(bx * 0.8 + t * 0.9 + ln.phase + i * 0.4) * 0.18;
            }
            ln.geo.attributes.position.needsUpdate = true;
          });
          scene.rotation.y = mx * 0.18;
          scene.rotation.x = my * 0.10;
        },
        dispose() { geos.forEach(g => g.dispose()); mat.dispose(); matHi.dispose(); scene.rotation.set(0,0,0); },
      };
    },

    // 07 RA — torus-knot with breathing pulse
    torusKnot(T, scene) {
      const geo = new T.TorusKnotGeometry(1.1, 0.018, 220, 12);
      const mesh = new T.LineSegments(new T.WireframeGeometry(geo), new T.LineBasicMaterial({ color: GOLD, transparent: true, opacity: 0.55 }));
      const innerGeo = new T.SphereGeometry(0.3, 16, 14);
      const inner = new T.LineSegments(new T.WireframeGeometry(innerGeo), new T.LineBasicMaterial({ color: GOLD_HI, transparent: true, opacity: 0.3 }));
      scene.add(mesh, inner);
      return {
        update(t, mx, my) {
          mesh.rotation.y = t * 0.3 + mx * 0.4;
          mesh.rotation.x = t * 0.18 + my * 0.3;
          const s = 1 + Math.sin(t * 0.6) * 0.04;
          mesh.scale.set(s, s, s);
          inner.rotation.y = -t * 0.5;
        },
        dispose() { geo.dispose(); innerGeo.dispose(); mesh.geometry.dispose(); inner.geometry.dispose(); mesh.material.dispose(); inner.material.dispose(); },
      };
    },

    // 08 VOICE 01 — ripple rings (sound-wave pulses outward)
    rippleRings(T, scene) {
      const mats = [
        new T.LineBasicMaterial({ color: GOLD,    transparent: true, opacity: 0.5 }),
        new T.LineBasicMaterial({ color: GOLD_HI, transparent: true, opacity: 0.35 }),
        new T.LineBasicMaterial({ color: GOLD_DK, transparent: true, opacity: 0.25 }),
      ];
      const geos = [];
      const rings = [];
      for (let i = 0; i < 3; i++) {
        const g = new T.TorusGeometry(0.5 + i * 0.4, 0.008, 6, 96);
        const r = new T.Mesh(g, mats[i]);
        r.rotation.x = Math.PI / 2;
        scene.add(r);
        rings.push(r);
        geos.push(g);
      }
      return {
        update(t, mx, my) {
          rings.forEach((r, i) => {
            const phase = (t * 0.4 + i * 0.66) % 2;
            r.scale.setScalar(0.5 + phase * 1.4);
            mats[i].opacity = Math.max(0, 0.55 - phase * 0.3);
          });
          scene.rotation.z = mx * 0.18;
        },
        dispose() { geos.forEach(g => g.dispose()); mats.forEach(m => m.dispose()); scene.rotation.set(0,0,0); },
      };
    },

    // 09 VOICE 02 — spiral curve (helix tube)
    spiralCurve(T, scene) {
      const pts = [];
      for (let i = 0; i < 240; i++) {
        const a = i * 0.18;
        const y = -1.8 + (i / 240) * 3.6;
        const r = 1.2 + Math.sin(a * 0.4) * 0.2;
        pts.push(new T.Vector3(Math.cos(a) * r, y, Math.sin(a) * r));
      }
      const curve = new T.CatmullRomCurve3(pts);
      const tubeGeo = new T.TubeGeometry(curve, 220, 0.015, 6, false);
      const tube = new T.LineSegments(new T.WireframeGeometry(tubeGeo), new T.LineBasicMaterial({ color: GOLD, transparent: true, opacity: 0.55 }));
      scene.add(tube);
      return {
        update(t, mx, my) {
          tube.rotation.y = t * 0.35 + mx * 0.3;
          tube.rotation.x = my * 0.15;
          tube.position.y = Math.sin(t * 0.4) * 0.06;
        },
        dispose() { tubeGeo.dispose(); tube.geometry.dispose(); tube.material.dispose(); },
      };
    },

    // 10 VOICE 03 — orbiting dots (12 dots on circles, gentle audio cadence)
    orbitingDots(T, scene) {
      const N = 12;
      const dotGeo = new T.SphereGeometry(0.04, 12, 12);
      const mat = new T.MeshBasicMaterial({ color: GOLD_HI, transparent: true, opacity: 0.78 });
      const orbits = [];
      for (let i = 0; i < N; i++) {
        const r = 0.8 + (i % 4) * 0.4;
        const phase = (i / N) * Math.PI * 2;
        const dot = new T.Mesh(dotGeo, mat);
        scene.add(dot);
        orbits.push({ dot, r, phase, speed: 0.3 + (i % 3) * 0.12 });
      }
      const ringGeo = new T.TorusGeometry(2, 0.005, 4, 96);
      const ring = new T.Mesh(ringGeo, new T.MeshBasicMaterial({ color: GOLD_DK, transparent: true, opacity: 0.18, wireframe: true }));
      ring.rotation.x = Math.PI / 2;
      scene.add(ring);
      return {
        update(t, mx, my) {
          orbits.forEach(o => {
            const a = t * o.speed + o.phase;
            o.dot.position.set(Math.cos(a) * o.r, Math.sin(a * 0.7) * 0.3, Math.sin(a) * o.r);
            const s = 0.9 + Math.sin(t * 1.4 + o.phase * 3) * 0.3;
            o.dot.scale.setScalar(s);
          });
          ring.rotation.z = t * 0.1 + mx * 0.2;
        },
        dispose() { dotGeo.dispose(); ringGeo.dispose(); mat.dispose(); ring.material.dispose(); },
      };
    },

    // 03 AUTOMATION (replaces orbitalRings per user feedback) —
    // 4×4×4 lattice of connected dots, rotating slowly. Reads as a
    // structured 3D automation graph, distinct from random dotNetwork.
    latticeCube(T, scene) {
      const N = 4;
      const sp = 0.7;
      const off = -((N - 1) / 2) * sp;
      const pos = new Float32Array(N * N * N * 3);
      let p = 0;
      for (let x = 0; x < N; x++)
        for (let y = 0; y < N; y++)
          for (let z = 0; z < N; z++) {
            pos[p++] = off + x * sp;
            pos[p++] = off + y * sp;
            pos[p++] = off + z * sp;
          }
      const ptGeo = new T.BufferGeometry();
      ptGeo.setAttribute('position', new T.BufferAttribute(pos, 3));
      const points = new T.Points(ptGeo, new T.PointsMaterial({ color: GOLD_HI, size: 0.058, transparent: true, opacity: 0.8 }));

      const idx = (x, y, z) => x * N * N + y * N + z;
      const pairs = [];
      for (let x = 0; x < N; x++)
        for (let y = 0; y < N; y++)
          for (let z = 0; z < N; z++) {
            if (x < N - 1) pairs.push(idx(x, y, z), idx(x + 1, y, z));
            if (y < N - 1) pairs.push(idx(x, y, z), idx(x, y + 1, z));
            if (z < N - 1) pairs.push(idx(x, y, z), idx(x, y, z + 1));
          }
      const linePos = new Float32Array(pairs.length * 3);
      for (let k = 0; k < pairs.length; k++) {
        const i = pairs[k];
        linePos[k * 3]     = pos[i * 3];
        linePos[k * 3 + 1] = pos[i * 3 + 1];
        linePos[k * 3 + 2] = pos[i * 3 + 2];
      }
      const lineGeo = new T.BufferGeometry();
      lineGeo.setAttribute('position', new T.BufferAttribute(linePos, 3));
      const lines = new T.LineSegments(lineGeo, new T.LineBasicMaterial({ color: GOLD, transparent: true, opacity: 0.35 }));

      const group = new T.Group();
      group.add(points, lines);
      scene.add(group);
      return {
        update(t, mx, my) {
          group.rotation.y = t * 0.22 + mx * 0.32;
          group.rotation.x = t * 0.1 + my * 0.26;
          const s = 1 + Math.sin(t * 0.5) * 0.04;
          group.scale.setScalar(s);
        },
        dispose() { ptGeo.dispose(); lineGeo.dispose(); points.material.dispose(); lines.material.dispose(); },
      };
    },

    // 08 VOICES (background) — HORIZONTAL golden helix wrapping the
    // marquee cards. Long axis = X. Rotates around X axis = corkscrew
    // illusion of flow LEFT→RIGHT, opposing the marquee cards which
    // scroll RIGHT→LEFT in the foreground. Constant tube radius so the
    // spiral reads as a uniform "wrapping" around the cards.
    dotSpiral(T, scene) {
      const N = 180;
      const turns = 7;          // tighter helix for "wrap" feel
      const length = 7;         // X extent — beyond viewport, fades out
      const radius = 1.55;      // ring radius — around the cards
      const pos = new Float32Array(N * 3);
      for (let i = 0; i < N; i++) {
        const tn = i / (N - 1);
        const a = tn * Math.PI * 2 * turns;
        // X is the LONG axis (horizontal); Y/Z form the helix wrap
        pos[i * 3]     = -length / 2 + tn * length;
        pos[i * 3 + 1] = Math.cos(a) * radius;
        pos[i * 3 + 2] = Math.sin(a) * radius;
      }
      const ptGeo = new T.BufferGeometry();
      ptGeo.setAttribute('position', new T.BufferAttribute(pos, 3));
      const points = new T.Points(ptGeo, new T.PointsMaterial({
        color: GOLD_HI, size: 0.048, transparent: true, opacity: 0.9, sizeAttenuation: true,
      }));

      // Lines between consecutive points = traced helix line
      const linePos = new Float32Array((N - 1) * 6);
      for (let i = 0; i < N - 1; i++) {
        linePos[i * 6]     = pos[i * 3];
        linePos[i * 6 + 1] = pos[i * 3 + 1];
        linePos[i * 6 + 2] = pos[i * 3 + 2];
        linePos[i * 6 + 3] = pos[(i + 1) * 3];
        linePos[i * 6 + 4] = pos[(i + 1) * 3 + 1];
        linePos[i * 6 + 5] = pos[(i + 1) * 3 + 2];
      }
      const lineGeo = new T.BufferGeometry();
      lineGeo.setAttribute('position', new T.BufferAttribute(linePos, 3));
      const lines = new T.LineSegments(lineGeo, new T.LineBasicMaterial({
        color: GOLD, transparent: true, opacity: 0.5,
      }));

      const group = new T.Group();
      group.add(points, lines);
      scene.add(group);
      return {
        update(t, mx, my) {
          // Corkscrew rotation around X axis = visual illusion of
          // material flowing LEFT→RIGHT along the long axis. Combined
          // with the helical geometry, eyes track the rotating spiral
          // and perceive rightward motion.
          group.rotation.x = t * 0.7 + my * 0.18;
          // Subtle Y yaw for depth on mouse parallax — keeps long axis
          // mostly horizontal, just a hint of perspective shift.
          group.rotation.y = mx * 0.14;
          // Tiny Z tilt so the helix doesn't lie flat-on against
          // viewport plane when mouse is centered.
          group.rotation.z = -0.04 + my * 0.05;
        },
        dispose() { ptGeo.dispose(); lineGeo.dispose(); points.material.dispose(); lines.material.dispose(); },
      };
    },

    // 11 WRITE — pulse-grid 5x5 (heartbeat matrix)
    pulseGrid(T, scene) {
      const N = 5, dotGeo = new T.SphereGeometry(0.06, 16, 16);
      const matBase = new T.MeshBasicMaterial({ color: GOLD, transparent: true, opacity: 0.55 });
      const matHi   = new T.MeshBasicMaterial({ color: GOLD_HI, transparent: true, opacity: 0.95 });
      const dots = [];
      for (let i = 0; i < N; i++) {
        for (let j = 0; j < N; j++) {
          const isCenter = i === 2 && j === 2;
          const dot = new T.Mesh(dotGeo, isCenter ? matHi : matBase);
          dot.position.set((i - 2) * 0.55, (j - 2) * 0.55, 0);
          scene.add(dot);
          dots.push({ dot, ph: (i + j) * 0.7, d: Math.sqrt((i-2)**2 + (j-2)**2) });
        }
      }
      const ringGeo = new T.TorusGeometry(1.8, 0.005, 6, 80);
      const ring = new T.Mesh(ringGeo, new T.MeshBasicMaterial({ color: GOLD_DK, transparent: true, opacity: 0.25, wireframe: true }));
      scene.add(ring);
      return {
        update(t, mx, my) {
          dots.forEach(o => {
            const s = 1 + Math.sin(t * 1.6 - o.d * 0.8) * 0.45;
            o.dot.scale.setScalar(Math.max(0.4, s));
          });
          ring.rotation.z = t * 0.12 + mx * 0.18;
        },
        dispose() { dotGeo.dispose(); ringGeo.dispose(); matBase.dispose(); matHi.dispose(); ring.material.dispose(); },
      };
    },
  };

  // Map chapter id → scene name
  const CHAPTER_SCENES = {
    'noir-ch-0':  'icosahedron',
    'noir-ch-1':  'dotNetwork',
    'noir-ch-2':  'waveGrid',
    'noir-ch-3':  'latticeCube',     // user feedback: orbitalRings replaced with connected-grid lattice
    'noir-ch-4':  'particleSwarm',
    'noir-ch-5':  'voxelStack',
    'noir-ch-6':  'flowField',
    'noir-ch-7':  'torusKnot',
    'noir-ch-8':  'dotSpiral',       // single voices-marquee chapter — spiral runs RIGHT, cards run LEFT
    'noir-ch-9':  'pulseGrid',       // contact (was ch-11)
  };

  // ─────────────────────────────────────────────────────────────────────
  // mountNoirBackdrop — single body-level canvas, one renderer, one rAF.
  // Active scene swaps in/out via setScene(name) called from the active-
  // chapter handler. Single WebGL context for all 12 chapters keeps us
  // under per-page WebGL context limits and rAF stays cheap.
  // Returns { setScene, destroy }.
  // ─────────────────────────────────────────────────────────────────────
  async function mountNoirBackdrop() {
    let T;
    try { T = await loadThree(); }
    catch { return null; }

    // Two canvases for true 3D wrap-around effect — back canvas sits
    // BEHIND the .noir-book content (z-index 50), front canvas SITS
    // ABOVE it (z-index 200). HTML cards in voices marquee live between
    // them at z-index 100. Each renderer draws the same scene/camera
    // but with opposing clipping planes at world z=0: back renderer
    // keeps z ≤ 0 (far side of helix), front renderer keeps z ≥ 0
    // (near side). As the helix rotates, points cross the z=0 plane
    // and migrate between canvases — visually wrapping around cards.
    const canvasBack = document.createElement('canvas');
    canvasBack.className = 'noir-bg noir-bg-back';
    document.body.appendChild(canvasBack);

    const canvasFront = document.createElement('canvas');
    canvasFront.className = 'noir-bg noir-bg-front';
    document.body.appendChild(canvasFront);

    const rendererBack = new T.WebGLRenderer({ canvas: canvasBack, alpha: true, antialias: true });
    const rendererFront = new T.WebGLRenderer({ canvas: canvasFront, alpha: true, antialias: true });
    rendererBack.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    rendererFront.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    rendererBack.localClippingEnabled = true;
    rendererFront.localClippingEnabled = true;
    // Plane(normal, constant) — keeps points where dot(normal, p) + constant ≥ 0.
    // 0.02 epsilon overlap avoids a 1-pixel seam at z=0.
    rendererBack.clippingPlanes  = [new T.Plane(new T.Vector3(0, 0, -1), 0.02)];   // keeps z ≤ 0.02
    rendererFront.clippingPlanes = [new T.Plane(new T.Vector3(0, 0,  1), 0.02)];   // keeps z ≥ -0.02

    const camera = new T.PerspectiveCamera(38, 1, 0.1, 100);
    camera.position.z = 5.4;
    let scene = new T.Scene();

    function resize() {
      const w = window.innerWidth, h = window.innerHeight;
      rendererBack.setSize(w, h, false);
      rendererFront.setSize(w, h, false);
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
      // Portrait viewports (phones) have aspect < 1 — geometries that
      // fit comfortably in landscape get cropped horizontally. Scale
      // the whole scene root down so the figures stay within the
      // narrow visible width. Linear interpolation between aspect 0.4
      // (very tall phone) → 0.45x and aspect 1.0 → 1.0x.
      const aspect = w / h;
      const portraitScale = aspect >= 1
        ? 1
        : Math.max(0.45, 0.45 + (aspect - 0.4) * 0.92);  // 0.4→0.45, 1.0→1.0
      scene.scale.setScalar(portraitScale);
    }
    resize();

    let mx = 0, my = 0, mxLerp = 0, myLerp = 0, t = 0;
    function onMouse(e) {
      mx =  (e.clientX / window.innerWidth)  * 2 - 1;
      my = -(e.clientY / window.innerHeight) * 2 + 1;
    }
    window.addEventListener('resize', resize);
    window.addEventListener('mousemove', onMouse, { passive: true });

    // Crossfade between scenes — each scene lives in its own Group.
    // On setScene: a new Group is created and added to the THREE scene,
    // its materials start at opacity 0. The previous active Group is
    // demoted to `fadingOut`. Each frame, active.fade ramps 0→1 and
    // fadingOut items ramp 1→0 (over ~700ms). When an outgoing item
    // reaches fade=0, it's disposed and removed.
    const FADE_STEP = 0.022;   // ~45 frames @ 60fps = 750ms transition
    let active = null;     // { group, data, name, fade: 0..1 }
    let fadingOut = [];    // [{ group, data, name, fade: 1..0 }, ...]

    function cacheBaseOpacities(group) {
      group.traverse(obj => {
        if (obj.material && obj.material.transparent && obj.userData.baseOpacity === undefined) {
          obj.userData.baseOpacity = obj.material.opacity;
        }
      });
    }

    function applyFade(group, fade) {
      group.traverse(obj => {
        if (obj.material && obj.userData.baseOpacity !== undefined) {
          obj.material.opacity = obj.userData.baseOpacity * fade;
        }
      });
    }

    function buildSceneGroup(name) {
      const builder = SCENE_BUILDERS[name];
      if (!builder) return null;
      const group = new T.Group();
      scene.add(group);
      const data = builder(T, group);   // builders use scene.add → adds into the group
      cacheBaseOpacities(group);
      applyFade(group, 0);              // start fully invisible
      return { group, data, name, fade: 0 };
    }

    function setScene(name) {
      if (active && active.name === name) return;
      // Demote current active to fading-out queue
      if (active) fadingOut.push(active);
      // Build new
      const next = buildSceneGroup(name);
      active = next;
    }

    function disposeAndRemove(item) {
      if (item.data && item.data.dispose) item.data.dispose();
      if (item.group && item.group.parent) item.group.parent.remove(item.group);
    }

    let running = true;
    let raf = null;
    function frame() {
      t += 0.0085;
      mxLerp += (mx - mxLerp) * 0.045;
      myLerp += (my - myLerp) * 0.045;

      // Update + fade-in active
      if (active) {
        if (active.fade < 1) {
          active.fade = Math.min(1, active.fade + FADE_STEP);
          applyFade(active.group, active.fade);
        }
        if (active.data.update) active.data.update(t, mxLerp, myLerp);
      }

      // Update + fade-out scenes leaving the stage
      for (let i = fadingOut.length - 1; i >= 0; i--) {
        const s = fadingOut[i];
        s.fade = Math.max(0, s.fade - FADE_STEP);
        applyFade(s.group, s.fade);
        if (s.data.update) s.data.update(t, mxLerp, myLerp);
        if (s.fade <= 0) {
          disposeAndRemove(s);
          fadingOut.splice(i, 1);
        }
      }

      // Render same scene/camera through both renderers — clipping
      // planes split it at z=0. Back goes behind cards, front goes above.
      rendererBack.render(scene, camera);
      rendererFront.render(scene, camera);
      if (running) raf = requestAnimationFrame(frame);
    }
    raf = requestAnimationFrame(frame);

    return {
      setScene,
      destroy() {
        running = false;
        if (raf) cancelAnimationFrame(raf);
        window.removeEventListener('resize', resize);
        window.removeEventListener('mousemove', onMouse);
        if (active) disposeAndRemove(active);
        fadingOut.forEach(disposeAndRemove);
        active = null;
        fadingOut = [];
        rendererBack.dispose();
        rendererFront.dispose();
        canvasBack.remove();
        canvasFront.remove();
      },
    };
  }

  // ─────────────────────────────────────────────────────────────────────
  // GSAP-driven chapter reveals + per-char letter splits. Uses Intersection
  // Observer to fire each chapter's paused timeline (IO is reliable for
  // viewport intersection regardless of scroll history — works with
  // programmatic jumps as well as natural scrolling). GSAP gives precise
  // easing curves and frame-perfect stagger that CSS can't match.
  // ─────────────────────────────────────────────────────────────────────
  function gsapNoirReveals(book) {
    if (!window.gsap) return null;
    const gsap = window.gsap;

    book.classList.add('gsap-on');
    const chapters = [...book.querySelectorAll('.noir-chapter')];
    const items = [];

    chapters.forEach((ch) => {
      const fades = [...ch.querySelectorAll('.noir-fade')];
      const chars = [...ch.querySelectorAll('.noir-split .char')];
      if (fades.length === 0 && chars.length === 0) return;

      const tl = gsap.timeline({ paused: true });

      if (chars.length > 0) {
        tl.fromTo(chars,
          { opacity: 0, yPercent: 70 },
          { opacity: 1, yPercent: 0, stagger: 0.028, duration: 0.72, ease: 'expo.out' },
          0
        );
      }
      fades.forEach((el) => {
        const d = el.classList.contains('d4') ? 4
                : el.classList.contains('d3') ? 3
                : el.classList.contains('d2') ? 2 : 1;
        tl.fromTo(el,
          { opacity: 0, y: 24 },
          { opacity: 1, y: 0, duration: 0.85, ease: 'expo.out' },
          0.12 + (d - 1) * 0.18
        );
      });
      items.push({ ch, tl });
    });

    // Manual scroll-driven visibility check — more reliable than IO with
    // a custom root + position:fixed in some browsers. Fires each chapter
    // timeline exactly once when ≥ 18% of its area enters the book viewport.
    function check() {
      const vh = book.clientHeight;
      const scroll = book.scrollTop;
      items.forEach((it) => {
        if (it.played) return;
        const top = it.ch.offsetTop;
        const bot = top + it.ch.offsetHeight;
        const viewBot = scroll + vh;
        // Overlap area between [top, bot] and [scroll, viewBot]
        const visible = Math.max(0, Math.min(bot, viewBot) - Math.max(top, scroll));
        if (visible / vh >= 0.18) {
          it.played = true;
          it.tl.play();
        }
      });
    }
    check();
    book.addEventListener('scroll', check, { passive: true });
    items.__check = check;
    items.__detach = () => book.removeEventListener('scroll', check);

    return {
      destroy() {
        if (items.__detach) items.__detach();
        items.forEach(it => it.tl.kill());
        book.classList.remove('gsap-on');
        const all = book.querySelectorAll('.noir-fade, .noir-split .char');
        gsap.set(all, { clearProps: 'all' });
      },
    };
  }

  // ─────────────────────────────────────────────────────────────────────
  // CONTENT EXTRACTION — runs once on first new-mode entry. Reads the
  // original DOM, keeps the bilingual <span class="lang-ru/en"> markup
  // intact so existing language CSS still hides the inactive language.
  // ─────────────────────────────────────────────────────────────────────
  function extractContent() {
    const $  = s => document.querySelector(s);
    const $$ = s => [...document.querySelectorAll(s)];
    const html = el => (el && el.innerHTML.trim()) || '';
    const text = el => (el && el.textContent.trim()) || '';
    const bil  = (ruText, enText) =>
      `<span class="lang-ru">${ruText || ''}</span><span class="lang-en">${enText || ''}</span>`;

    return {
      hero: {
        meta:  html($('.hero-meta .small')),
        line1: html($('.hero-text .line-1')),
        line2: html($('.hero-text .line-2')),
        sub:   html($('.hero-text .sub')),
      },
      services: $$('#services .sticker-card').map(c => {
        const ruLis = $$('.svc-bullets.lang-ru li', c).map(l => l.textContent.trim());
        const enLis = $$('.svc-bullets.lang-en li', c).map(l => l.textContent.trim());
        return {
          num:     text(c.querySelector('.svc-num')),       // "// 01 · WEB"
          name:    html(c.querySelector('.svc-name')),      // bilingual HTML
          bullets: ruLis.map((ru, i) => bil(ru, enLis[i] || '')),
          closing: html(c.querySelector('.svc-closing')),
        };
      }),
      cases: $$('.work-grid .sticker-card').map(c => {
        const ru = c.querySelector('.svc-body.lang-ru')?.textContent.trim() || '';
        const en = c.querySelector('.svc-body.lang-en')?.textContent.trim() || '';
        return {
          art:    text(c.querySelector('.case-art')),
          meta:   text(c.querySelector('.case-meta')),
          name:   html(c.querySelector('.svc-name')),
          body:   bil(ru, en),
          metric: text(c.querySelector('.btn-chip-copper')),
        };
      }),
      testimonials: $$('#testimonials .testimonial-card').slice(0, 12).map(c => ({
        quote:  html(c.querySelector('.quote')),
        author: text(c.querySelector('.author')),
        role:   html(c.querySelector('.role')),
      })),
      contact: {
        email:       'corpdeadline@gmail.com',
        telegram:    '@deadline_corp',
        telegramUrl: 'https://t.me/deadline_corp',
      },
    };
  }

  // ─────────────────────────────────────────────────────────────────────
  // NOIR LAYOUT BUILDER — assembles the photobook HTML from CONTENT.
  // Structure: 12 full-viewport chapters with scroll-snap:
  //   00 cover · 01 manifesto · 02-04 services · 05-07 cases ·
  //   08-10 voices (testimonials) · 11 contact
  // ─────────────────────────────────────────────────────────────────────
  // Abstract SVG glyph per case study — gives each case chapter visual
  // texture without stock photography. Concentric squares for VRP (layers
  // of property), vertical bar pattern for KD (key drops), orbital chain
  // graph for RA (blockchain rings).
  function caseGlyph(art) {
    const id = (art || '').toUpperCase();
    if (id.includes('VRP')) {
      const rects = Array.from({ length: 8 }, (_, i) => {
        const ins = i * 11;
        const o = (0.7 - i * 0.075).toFixed(2);
        return `<rect x="${10 + ins}" y="${10 + ins}" width="${180 - ins * 2}" height="${180 - ins * 2}" opacity="${o}"/>`;
      }).join('');
      return `<svg viewBox="0 0 200 200" fill="none" stroke="currentColor" stroke-width="0.6">${rects}<line x1="100" y1="20" x2="100" y2="180" opacity="0.18"/><line x1="20" y1="100" x2="180" y2="100" opacity="0.18"/><text x="100" y="107" text-anchor="middle" font-family="JetBrains Mono, monospace" font-size="11" letter-spacing="5" fill="currentColor">VRP</text></svg>`;
    }
    if (id.includes('KD')) {
      const bars = Array.from({ length: 18 }, (_, i) => {
        const x = (18 + i * 9.4).toFixed(1);
        const y1 = 20 + (i % 4) * 6;
        const h  = 150 - (i % 5) * 24;
        const o  = (0.32 + (i % 4) * 0.15).toFixed(2);
        return `<line x1="${x}" y1="${y1}" x2="${x}" y2="${y1 + h}" opacity="${o}"/>`;
      }).join('');
      return `<svg viewBox="0 0 200 200" fill="none" stroke="currentColor" stroke-width="0.6">${bars}<circle cx="100" cy="100" r="8" fill="currentColor" opacity="0.5" stroke="none"/><text x="100" y="107" text-anchor="middle" font-family="JetBrains Mono, monospace" font-size="11" letter-spacing="5" fill="currentColor">KD</text></svg>`;
    }
    // RA — orbital chain
    const nodes = Array.from({ length: 12 }, (_, i) => {
      const a = (i / 12) * Math.PI * 2;
      const cx = (100 + Math.cos(a) * 70).toFixed(1);
      const cy = (100 + Math.sin(a) * 70).toFixed(1);
      const o  = (0.38 + (i % 3) * 0.17).toFixed(2);
      return `<circle cx="${cx}" cy="${cy}" r="4" opacity="${o}" fill="currentColor" stroke="none"/>`;
    }).join('');
    const chords = Array.from({ length: 6 }, (_, i) => {
      const a1 = (i / 6) * Math.PI * 2;
      const a2 = ((i + 3) / 6) * Math.PI * 2;
      return `<line x1="${(100 + Math.cos(a1) * 70).toFixed(1)}" y1="${(100 + Math.sin(a1) * 70).toFixed(1)}" x2="${(100 + Math.cos(a2) * 70).toFixed(1)}" y2="${(100 + Math.sin(a2) * 70).toFixed(1)}" opacity="0.22"/>`;
    }).join('');
    return `<svg viewBox="0 0 200 200" fill="none" stroke="currentColor" stroke-width="0.6">${chords}${nodes}<circle cx="100" cy="100" r="24" opacity="0.3"/><circle cx="100" cy="100" r="50" opacity="0.16"/><text x="100" y="107" text-anchor="middle" font-family="JetBrains Mono, monospace" font-size="11" letter-spacing="5" fill="currentColor">RA</text></svg>`;
  }

  function buildNoirHTML(c) {
    const parts = [];

    // Letter-split helper — wraps each char in a span with data-i for
    // staggered transition-delays defined in CSS. Spaces become &nbsp;.
    const splitWord = (txt) => {
      const chars = [...(txt || '')].map((ch, i) =>
        `<span class="char" data-i="${Math.min(i, 11)}">${ch === ' ' ? '&nbsp;' : ch}</span>`
      ).join('');
      return `<span class="noir-split">${chars}</span>`;
    };

    // 00 COVER — large 5+5 split: DEADLINE display | rule + italic lead.
    // Bottom: 4-cell stats strip with gold numbers + mono labels.
    parts.push(`
      <section class="noir-chapter noir-cover" id="noir-ch-0" data-ch="0">
        <div class="noir-cover-top noir-fade d1">
          <span><span class="gold">●</span> DEADLINE / EST. 2025 / PHUKET × BANGKOK</span>
          <span class="rhs"><span class="lang-ru">// дедлайны нас боятся</span><span class="lang-en">// deadlines fear us</span></span>
        </div>
        <div class="noir-cover-center">
          <h1 class="noir-display">${splitWord('DEADLINE')}</h1>
          <div class="noir-display-rhs noir-fade d2">
            <div class="noir-display-rule"></div>
            <p class="noir-display-lead">
              <span class="lang-ru">Веб · Автоматизация · AI-агенты в production — к согласованной дате, без сюрпризов в счёте.</span>
              <span class="lang-en">Web · Automation · AI agents shipped to production — by the agreed date, no surprises on the invoice.</span>
            </p>
          </div>
        </div>
        <div class="noir-cover-meta noir-fade d3">
          <div class="noir-meta-cell">
            <div class="num">12+</div>
            <div class="lbl"><span class="lang-ru">в production</span><span class="lang-en">in production</span></div>
          </div>
          <div class="noir-meta-cell">
            <div class="num">0</div>
            <div class="lbl"><span class="lang-ru">пропущенных дедлайнов</span><span class="lang-en">missed deadlines</span></div>
          </div>
          <div class="noir-meta-cell">
            <div class="num">100%</div>
            <div class="lbl"><span class="lang-ru">возвращаются</span><span class="lang-en">come back</span></div>
          </div>
          <div class="noir-meta-cell">
            <div class="num">8</div>
            <div class="lbl"><span class="lang-ru">индустрий</span><span class="lang-en">industries</span></div>
          </div>
        </div>
      </section>`);

    // 01 MANIFESTO — ornate quote-glyph above italic statement,
    // mono editorial header + footer rules.
    parts.push(`
      <section class="noir-chapter noir-manifesto" id="noir-ch-1" data-ch="1">
        <div class="noir-mh-top noir-fade d1">
          <span>01 / MANIFESTO</span>
          <span class="rule"></span>
          <span>since 2025</span>
        </div>
        <div class="noir-mh-stage">
          <p class="noir-statement noir-fade d2">
            <span class="lang-ru">Мы — DEADLINE.<br>У нас ничего не горит.</span>
            <span class="lang-en">We are DEADLINE.<br>Nothing's on fire here.</span>
          </p>
        </div>
        <span class="noir-mh-bottom noir-fade d3">DEADLINE / since 2025 / always</span>
      </section>`);

    // 02-04 SERVICE — 5+7 asymmetric: title block left, numbered editorial
    // list right, italic closing with gold dash.
    c.services.slice(0, 3).forEach((s, i) => {
      const idx = i + 2;
      const num = String(idx).padStart(2, '0');
      const tag = (s.num || '').replace(/^\/\/\s*\d+\s*·\s*/, '').trim();
      parts.push(`
        <section class="noir-chapter noir-service" id="noir-ch-${idx}" data-ch="${idx}">
          <div class="lhs">
            <div class="ch-tag noir-fade d1">${num} / SERVICE</div>
            <h2 class="ch-title">${s.name}</h2>
            <div class="ch-italic noir-fade d2">${tag.toLowerCase()}</div>
            <p class="ch-closing noir-fade d3">${s.closing}</p>
          </div>
          <div class="rhs noir-fade d2">
            <ol class="noir-numbered">
              ${s.bullets.slice(0, 5).map(b => `<li>${b}</li>`).join('')}
            </ol>
          </div>
        </section>`);
    });

    // 05-07 CASE — single-column text now (no left mark — the body-level
    // 3D backdrop is the visual; we don't want a card blocking it). Case
    // meta + Fraunces title + Inter body + bordered metric span the
    // chapter's content area centered.
    c.cases.slice(0, 3).forEach((cs, i) => {
      const idx = i + 5;
      const num = String(idx).padStart(2, '0');
      parts.push(`
        <section class="noir-chapter noir-case noir-case-solo" id="noir-ch-${idx}" data-ch="${idx}">
          <div class="rhs">
            <div class="meta noir-fade d1">${num} / CASE · ${cs.meta}</div>
            <h2 class="title noir-fade d2">${cs.name}</h2>
            <p class="desc noir-fade d3">${cs.body}</p>
            <div class="metric noir-fade d4">
              <span>${cs.metric}</span>
              <span class="lbl"><span class="lang-ru">верифицированный результат</span><span class="lang-en">verified result</span></span>
              <span class="arrow">↗</span>
            </div>
          </div>
        </section>`);
    });

    // 08 VOICES — single chapter, all testimonials in a left-scrolling
    // horizontal marquee. Cards loop seamlessly via duplicated track +
    // CSS keyframes translateX(0 → -50%). Pauses on hover so you can read.
    const allVoices = c.testimonials.slice(0, 12);
    const voiceCards = allVoices.map(t => `
      <div class="noir-voice-card">
        <p class="quote">${t.quote}</p>
        <div class="byline"><span class="author">${t.author}</span> · ${t.role}</div>
      </div>
    `).join('');
    parts.push(`
      <section class="noir-chapter noir-voices" id="noir-ch-8" data-ch="8">
        <div class="noir-mh-top noir-fade d1">
          <span>08 / VOICES</span>
          <span class="rule"></span>
          <span>returning clients</span>
        </div>
        <div class="noir-voices-stage noir-fade d2">
          <div class="noir-voices-track">${voiceCards}${voiceCards}</div>
        </div>
        <span class="noir-mh-bottom noir-fade d3">— from those who came back —</span>
      </section>`);

    // 09 CONTACT — grand farewell (renumbered from 11; we collapsed the
    // 3 voice chapters into the marquee above).
    parts.push(`
      <section class="noir-chapter noir-contact" id="noir-ch-9" data-ch="9">
        <div class="stage">
          <div class="label noir-fade d1">
            <span class="lang-ru">END · НАПИШИ НАМ</span>
            <span class="lang-en">END · MAIL US</span>
          </div>
          <h2 class="head noir-fade d2">
            <span class="lang-ru">Пиши.</span>
            <span class="lang-en">Write.</span>
          </h2>
          <div class="links noir-fade d3">
            <a class="link" href="mailto:${c.contact.email}">${c.contact.email}</a>
            <a class="link" href="${c.contact.telegramUrl}">${c.contact.telegram}</a>
          </div>
          <div class="signoff noir-fade d4">DEADLINE / since 2025</div>
        </div>
      </section>`);

    const tocLabels = [
      '00 / COVER', '01 / MANIFESTO', '02 / WEB', '03 / AUTO', '04 / AI',
      '05 / VRP', '06 / KD', '07 / RA', '08 / VOICES', '09 / WRITE',
    ];
    const toc = `<nav class="noir-toc">${
      tocLabels.map((l, i) =>
        `<a class="noir-toc-item${i === 0 ? ' is-active' : ''}" href="#noir-ch-${i}" data-ch="${i}">${l}</a>`
      ).join('')
    }</nav>`;

    return parts.join('') + toc;
  }

  function createNoirCursor() {
    const dot  = document.createElement('div');
    const ring = document.createElement('div');
    dot.className  = 'noir-cursor-dot';
    ring.className = 'noir-cursor-ring';
    document.body.appendChild(dot);
    document.body.appendChild(ring);

    let mx = window.innerWidth / 2, my = window.innerHeight / 2;
    let rx = mx, ry = my;
    let raf = null;
    let running = false;
    let onScreen = false;

    function onMove(e) {
      mx = e.clientX;
      my = e.clientY;
      if (!onScreen) {
        dot.classList.add('is-on');
        ring.classList.add('is-on');
        onScreen = true;
      }
    }
    function onLeave() {
      dot.classList.remove('is-on');
      ring.classList.remove('is-on');
      onScreen = false;
    }
    function tick() {
      dot.style.transform  = `translate3d(${mx}px, ${my}px, 0)`;
      rx += (mx - rx) * 0.14;
      ry += (my - ry) * 0.14;
      ring.style.transform = `translate3d(${rx}px, ${ry}px, 0)`;
      if (running) raf = requestAnimationFrame(tick);
    }

    // Ring expands on hover over interactive elements
    const hoverables = document.querySelectorAll(
      'a, button, .btn-chip, .btn-chip-copper, .btn-primary, .svc-card, .testimonial-card, .work-grid > *, h1, h2'
    );
    const onEnter = () => ring.classList.add('is-hovering');
    const onLeaveHover = () => ring.classList.remove('is-hovering');
    hoverables.forEach(el => {
      el.addEventListener('mouseenter', onEnter);
      el.addEventListener('mouseleave', onLeaveHover);
    });

    return {
      start() {
        running = true;
        window.addEventListener('mousemove', onMove);
        document.addEventListener('mouseleave', onLeave);
        raf = requestAnimationFrame(tick);
      },
      destroy() {
        running = false;
        if (raf) cancelAnimationFrame(raf);
        window.removeEventListener('mousemove', onMove);
        document.removeEventListener('mouseleave', onLeave);
        hoverables.forEach(el => {
          el.removeEventListener('mouseenter', onEnter);
          el.removeEventListener('mouseleave', onLeaveHover);
        });
        dot.remove();
        ring.remove();
      },
    };
  }

  function installNoirMagnetic() {
    // Pulls big display elements in the photobook toward the cursor.
    const targets = document.querySelectorAll('.noir-book .noir-mega, .noir-book .noir-big, .noir-book .noir-link');
    const handlers = [];
    targets.forEach(el => {
      const onMove = (e) => {
        const r = el.getBoundingClientRect();
        const dx = (e.clientX - (r.left + r.width / 2)) * 0.08;
        const dy = (e.clientY - (r.top + r.height / 2)) * 0.08;
        el.style.transform = `translate(${dx}px, ${dy}px)`;
      };
      const onLeave = () => {
        el.style.transition = 'transform 580ms cubic-bezier(0.16, 1, 0.3, 1)';
        el.style.transform = '';
        setTimeout(() => { el.style.transition = ''; }, 600);
      };
      el.addEventListener('mousemove', onMove);
      el.addEventListener('mouseleave', onLeave);
      handlers.push({ el, onMove, onLeave });
    });
    return {
      destroy() {
        handlers.forEach(h => {
          h.el.removeEventListener('mousemove', h.onMove);
          h.el.removeEventListener('mouseleave', h.onLeave);
          h.el.style.transform = '';
          h.el.style.transition = '';
        });
      },
    };
  }

  function installNoirScrollNav() {
    // Right-rail TOC highlights the chapter currently in view + handles
    // click → smooth-scroll inside the scroll-snap container.
    const book  = document.querySelector('.noir-book');
    const items = [...document.querySelectorAll('.noir-toc-item')];
    if (!book || items.length === 0) return null;

    const chapters = [...book.querySelectorAll('.noir-chapter')];
    const io = new IntersectionObserver(entries => {
      entries.forEach(e => {
        if (e.isIntersecting && e.intersectionRatio > 0.55) {
          const ch = e.target.dataset.ch;
          items.forEach(it => it.classList.toggle('is-active', it.dataset.ch === ch));
        }
      });
    }, { threshold: [0.56], root: book });

    chapters.forEach(ch => io.observe(ch));

    const clickHandlers = [];
    items.forEach(it => {
      const onClick = (ev) => {
        ev.preventDefault();
        const target = book.querySelector(`#noir-ch-${it.dataset.ch}`);
        if (target) target.scrollIntoView({ behavior: 'smooth', block: 'start' });
      };
      it.addEventListener('click', onClick);
      clickHandlers.push({ el: it, fn: onClick });
    });

    return {
      destroy() {
        io.disconnect();
        clickHandlers.forEach(h => h.el.removeEventListener('click', h.fn));
      },
    };
  }

  async function enterNoir() {
    if (!CONTENT) CONTENT = extractContent();

    // 1. (v2.3 2026-05-12) No detach needed — magic-toggle now lives in
    //    body root from page-load (moved in index.html). Survives noir's
    //    body-children-hide rule via existing `:not(.magic-toggle)` exception.

    // 2. Mount persistent UI chrome: grain → running header → page meta →
    //    scroll progress hairline. Each is body-level so it floats above
    //    the book and survives mode swaps independently.
    noirGrainEl = document.createElement('div');
    noirGrainEl.className = 'noir-grain';
    document.body.appendChild(noirGrainEl);

    noirRunningEl = document.createElement('div');
    noirRunningEl.className = 'noir-running';
    noirRunningEl.innerHTML =
      '<span class="lhs">DEADLINE <span class="sep">/</span> SELECTED WORK 2025-2026</span>' +
      '<span class="mid" data-mid>COVER</span>' +
      '<span class="rhs">CH <span data-ch-num>01</span> <span class="sep">/</span> 10</span>';
    document.body.appendChild(noirRunningEl);

    noirPagesEl = document.createElement('div');
    noirPagesEl.className = 'noir-pages';
    noirPagesEl.innerHTML =
      '<span class="lhs">PAGE <strong data-page-num>001</strong> / 010</span>' +
      '<span class="rhs">EDITION I · PRINT-FOR-WEB</span>';
    document.body.appendChild(noirPagesEl);

    noirProgressEl = document.createElement('div');
    noirProgressEl.className = 'noir-progress';
    document.body.appendChild(noirProgressEl);

    // 3. Mount the photobook
    const wrapper = document.createElement('div');
    wrapper.className = 'noir-book';
    wrapper.innerHTML = buildNoirHTML(CONTENT);
    document.body.appendChild(wrapper);
    noirBook = wrapper;

    // 4. Theatrical overlay fade
    overlay.style.background = '#0A0908';
    overlay.classList.add('is-visible');
    await wait(380);
    overlay.classList.remove('is-visible');
    await wait(200);
    overlay.style.background = '';

    // 5. Reveals — GSAP timelines bound to ScrollTrigger when available
    //    (precise easing + sub-frame stagger), CSS-IO fallback otherwise.
    //    First chapter is revealed immediately regardless.
    const chapters = [...wrapper.querySelectorAll('.noir-chapter')];
    if (chapters[0]) chapters[0].classList.add('is-revealed');
    noirReveals = gsapNoirReveals(wrapper);
    if (!noirReveals) {
      // GSAP not loaded — fall back to CSS class-driven IO reveals
      noirRevealIO = new IntersectionObserver(entries => {
        entries.forEach(e => {
          if (e.isIntersecting && e.intersectionRatio > 0.15) {
            e.target.classList.add('is-revealed');
          }
        });
      }, { threshold: [0.16], root: wrapper });
      chapters.forEach(ch => noirRevealIO.observe(ch));
    }

    // 5a. 3D backdrop — single body-level canvas, scene swaps based on
    //     active chapter. Async load doesn't block enter.
    mountNoirBackdrop().then(bd => {
      noirScene = bd;
      if (bd) bd.setScene(CHAPTER_SCENES['noir-ch-0']);
    });

    // 6. Active-chapter tracker: updates running-header mid label,
    //    chapter number, page number, TOC highlight, AND swaps the 3D
    //    backdrop scene to the per-chapter geometry.
    const labels = [
      'COVER', 'MANIFESTO', 'WEB · DEV', 'AUTOMATION', 'AI AGENTS',
      'CASE · VRP', 'CASE · KD', 'CASE · RA',
      'VOICES', 'WRITE',
    ];
    const midEl    = noirRunningEl.querySelector('[data-mid]');
    const chNumEl  = noirRunningEl.querySelector('[data-ch-num]');
    const pageNum  = noirPagesEl.querySelector('[data-page-num]');
    const tocItems = [...wrapper.querySelectorAll('.noir-toc-item')];
    const navIO = new IntersectionObserver(entries => {
      entries.forEach(e => {
        if (e.isIntersecting && e.intersectionRatio > 0.42) {
          const idx = Number(e.target.dataset.ch);
          if (midEl)   midEl.textContent  = labels[idx] || '';
          if (chNumEl) chNumEl.textContent = String(idx + 1).padStart(2, '0');
          if (pageNum) pageNum.textContent = String(idx + 1).padStart(3, '0');
          tocItems.forEach(t => t.classList.toggle('is-active', Number(t.dataset.ch) === idx));
          // Swap backdrop scene to match active chapter
          if (noirScene && noirScene.setScene) {
            const sceneName = CHAPTER_SCENES[e.target.id];
            if (sceneName) noirScene.setScene(sceneName);
          }
          // Tint the body-level canvas via a class for per-mode CSS opacity
          document.body.setAttribute('data-noir-ch', String(idx));
        }
      });
    }, { threshold: [0.43], root: wrapper });
    chapters.forEach(ch => navIO.observe(ch));

    const onTocClick = (ev) => {
      const item = ev.target.closest('.noir-toc-item');
      if (!item) return;
      ev.preventDefault();
      const target = wrapper.querySelector(`#noir-ch-${item.dataset.ch}`);
      if (target) target.scrollIntoView({ behavior: 'smooth', block: 'start' });
    };
    wrapper.addEventListener('click', onTocClick);

    noirScrollNav = {
      destroy() {
        navIO.disconnect();
        wrapper.removeEventListener('click', onTocClick);
      },
    };

    // 7. Scroll-progress hairline width = scroll percentage
    const onScroll = () => {
      const max = wrapper.scrollHeight - wrapper.clientHeight;
      const pct = max > 0 ? wrapper.scrollTop / max : 0;
      if (noirProgressEl) noirProgressEl.style.transform = `scaleX(${pct.toFixed(4)})`;
    };
    wrapper.addEventListener('scroll', onScroll, { passive: true });
    onScroll();
    noirProgressHandler = { el: wrapper, fn: onScroll };

    // 8. Cursor + magnetic
    if (window.matchMedia('(hover: hover) and (pointer: fine)').matches) {
      noirCursor   = createNoirCursor();
      noirCursor.start();
      noirMagnetic = installNoirMagnetic();
    }
  }

  async function exitNoir() {
    overlay.style.background = '#0A0908';
    overlay.classList.add('is-visible');
    await wait(300);

    if (noirCursor)    { noirCursor.destroy();    noirCursor    = null; }
    if (noirMagnetic)  { noirMagnetic.destroy();  noirMagnetic  = null; }
    if (noirScrollNav) { noirScrollNav.destroy(); noirScrollNav = null; }
    if (noirReveals)   { noirReveals.destroy();   noirReveals   = null; }
    if (noirRevealIO)  { noirRevealIO.disconnect(); noirRevealIO = null; }
    if (noirScene)     { noirScene.destroy();     noirScene     = null; }
    if (noirProgressHandler) {
      noirProgressHandler.el.removeEventListener('scroll', noirProgressHandler.fn);
      noirProgressHandler = null;
    }

    if (noirGrainEl)    { noirGrainEl.remove();    noirGrainEl    = null; }
    if (noirRunningEl)  { noirRunningEl.remove();  noirRunningEl  = null; }
    if (noirPagesEl)    { noirPagesEl.remove();    noirPagesEl    = null; }
    if (noirProgressEl) { noirProgressEl.remove(); noirProgressEl = null; }
    document.body.removeAttribute('data-noir-ch');
    if (noirBook)       { noirBook.remove();       noirBook       = null; }

    // (v2.3 2026-05-12) No re-attach needed — magic-toggle lives in body
    // permanently. magicBtnHome no longer used.
    magicBtnHome = null;
  }

  // ═══════════════════════════════════════════════════════════════════════
  // VAULT — Resn-inspired
  //   Signature: four corner ghost-nav labels + mono debug strip (live time)
  //   + stationary-cursor aquamarine load-up ring. No click-and-hold gate
  //   (it would block normal flow); the ring captures the "patient intent"
  //   feeling instead.
  // ═══════════════════════════════════════════════════════════════════════
  let vaultChrome = null;
  let vaultRing   = null;

  function installVaultChrome() {
    const corners = [
      { cls: 'tl', txt: '// DEADLINE / 001' },
      { cls: 'tr', txt: 'CONTACT →',         href: '#contact' },
      { cls: 'bl', txt: 'MODE · VAULT' },
      { cls: 'br', txt: 'EST · 2025' },
    ];
    const els = corners.map(c => {
      const tag = c.href ? 'a' : 'div';
      const el  = document.createElement(tag);
      el.className = `vault-corner ${c.cls}`;
      el.textContent = c.txt;
      if (c.href) el.href = c.href;
      document.body.appendChild(el);
      return el;
    });

    const debug = document.createElement('div');
    debug.className = 'vault-debug';
    function paint() {
      const t = new Date();
      const hh = String(t.getHours()).padStart(2, '0');
      const mm = String(t.getMinutes()).padStart(2, '0');
      const ss = String(t.getSeconds()).padStart(2, '0');
      const lang = document.body.classList.contains('lang-en') ? 'EN' : 'RU';
      debug.innerHTML =
        `<span>MODE: <strong>VAULT</strong></span>` +
        `<span>BUILD: deadline@342684c</span>` +
        `<span>LANG: <strong>${lang}</strong></span>` +
        `<span>${hh}:${mm}:${ss}</span>` +
        `<span class="vault-debug-tail">// patience is precision</span>`;
    }
    paint();
    const interval = setInterval(paint, 1000);
    document.body.appendChild(debug);

    return {
      destroy() {
        clearInterval(interval);
        els.forEach(e => e.remove());
        debug.remove();
      },
    };
  }

  function installVaultRing() {
    const SVG_NS = 'http://www.w3.org/2000/svg';
    const svg = document.createElementNS(SVG_NS, 'svg');
    svg.setAttribute('class', 'vault-ring');
    svg.setAttribute('viewBox', '0 0 56 56');
    const bg = document.createElementNS(SVG_NS, 'circle');
    bg.setAttribute('class', 'vault-ring-bg');
    bg.setAttribute('cx', '28');
    bg.setAttribute('cy', '28');
    bg.setAttribute('r', '27');
    const fg = document.createElementNS(SVG_NS, 'circle');
    fg.setAttribute('class', 'vault-ring-fg');
    fg.setAttribute('cx', '28');
    fg.setAttribute('cy', '28');
    fg.setAttribute('r', '27');
    svg.appendChild(bg);
    svg.appendChild(fg);
    document.body.appendChild(svg);

    let mx = window.innerWidth / 2, my = window.innerHeight / 2;
    let stillTimer = null;
    let onScreen = false;

    function position() {
      svg.style.transform = `translate3d(${mx}px, ${my}px, 0)`;
    }
    function onMove(e) {
      mx = e.clientX;
      my = e.clientY;
      position();
      if (!onScreen) {
        svg.classList.add('is-on');
        onScreen = true;
      }
      svg.classList.remove('is-filling');
      if (stillTimer) clearTimeout(stillTimer);
      stillTimer = setTimeout(() => {
        svg.classList.add('is-filling');
      }, 220);
    }
    function onLeave() {
      svg.classList.remove('is-on', 'is-filling');
      onScreen = false;
      if (stillTimer) { clearTimeout(stillTimer); stillTimer = null; }
    }
    position();
    window.addEventListener('mousemove', onMove);
    document.addEventListener('mouseleave', onLeave);

    return {
      destroy() {
        window.removeEventListener('mousemove', onMove);
        document.removeEventListener('mouseleave', onLeave);
        if (stillTimer) clearTimeout(stillTimer);
        svg.remove();
      },
    };
  }

  async function enterVault() {
    overlay.style.background = 'radial-gradient(circle at 50% 50%, rgba(0, 200, 180, 0.18) 0%, #000 70%)';
    overlay.classList.add('is-visible');
    await wait(300);
    overlay.classList.remove('is-visible');
    await wait(180);
    overlay.style.background = '';

    vaultChrome = installVaultChrome();
    if (window.matchMedia('(hover: hover) and (pointer: fine)').matches) {
      vaultRing = installVaultRing();
    }
  }

  async function exitVault() {
    overlay.style.background = '#000';
    overlay.classList.add('is-visible');
    await wait(260);
    if (vaultChrome) { vaultChrome.destroy(); vaultChrome = null; }
    if (vaultRing)   { vaultRing.destroy();   vaultRing   = null; }
  }

  // ═══════════════════════════════════════════════════════════════════════
  // KINETIC — Active Theory-inspired
  //   Signature: scroll-velocity injected into --scroll-v CSS variable
  //   (subtle nav squash), bottom-left fade-in section reveals, hero
  //   clip-path wipe-in on enter. No WebGL, no Three.js — just rAF + IO.
  // ═══════════════════════════════════════════════════════════════════════
  let kineticScroll = null;
  let kineticIO     = null;

  function installKineticScroll() {
    let prevY = window.scrollY;
    let velocity = 0;
    let raf = null;
    let running = true;

    function tick() {
      const cur   = window.scrollY;
      const delta = cur - prevY;
      prevY = cur;
      // exponential moving average so values feel springy not jittery
      velocity += (delta - velocity) * 0.22;
      // map to [-1, 1] with cap — squash is meant to be subtle
      const v = Math.max(-1, Math.min(1, velocity / 24));
      HTML.style.setProperty('--scroll-v', v.toFixed(3));
      if (running) raf = requestAnimationFrame(tick);
    }
    raf = requestAnimationFrame(tick);
    return {
      destroy() {
        running = false;
        if (raf) cancelAnimationFrame(raf);
        HTML.style.removeProperty('--scroll-v');
      },
    };
  }

  function installKineticReveal() {
    const targets = document.querySelectorAll(
      'section, .hero, .work-grid > *, .testimonial-card, .svc-card, .process-step'
    );
    targets.forEach(t => t.classList.add('kinetic-reveal'));
    const io = new IntersectionObserver((entries) => {
      entries.forEach(e => {
        if (e.isIntersecting) {
          e.target.classList.add('is-in');
          io.unobserve(e.target);
        }
      });
    }, { threshold: 0.14, rootMargin: '0px 0px -70px 0px' });
    targets.forEach(t => io.observe(t));
    return {
      destroy() {
        io.disconnect();
        targets.forEach(t => t.classList.remove('kinetic-reveal', 'is-in'));
      },
    };
  }

  async function enterKinetic() {
    overlay.style.background = 'linear-gradient(180deg, #000 0%, #002b26 100%)';
    overlay.classList.add('is-visible');
    await wait(300);
    overlay.classList.remove('is-visible');
    await wait(160);
    overlay.style.background = '';

    // Hero clip-path wipe-in (one-shot)
    HTML.classList.add('kinetic-entering');
    setTimeout(() => HTML.classList.remove('kinetic-entering'), 1300);

    kineticScroll = installKineticScroll();
    kineticIO     = installKineticReveal();
  }

  async function exitKinetic() {
    overlay.style.background = '#000';
    overlay.classList.add('is-visible');
    await wait(260);
    HTML.classList.remove('kinetic-entering');
    if (kineticScroll) { kineticScroll.destroy(); kineticScroll = null; }
    if (kineticIO)     { kineticIO.destroy();     kineticIO     = null; }
  }

  // ─────────────────────────────────────────────────────────────────────
  // Generic fallback (no longer used now that Studio has its own theatre,
  // but kept for safety)
  // ─────────────────────────────────────────────────────────────────────
  async function genericExit() {
    overlay.classList.add('is-visible');
    await wait(420);
  }
  async function genericEnter() {
    await wait(180);
    overlay.classList.remove('is-visible');
    await wait(420);
  }

  // ─────────────────────────────────────────────────────────────────────
  // Master orchestrator
  // ─────────────────────────────────────────────────────────────────────
  async function transitionTo(target) {
    if (HTML.classList.contains(TRANSITION_LOCK_CLASS)) return;
    HTML.classList.add(TRANSITION_LOCK_CLASS);

    btn.classList.remove('is-arriving', 'is-hidden');
    btn.classList.add('is-charging');
    await wait(220);
    btn.classList.remove('is-charging');
    btn.classList.add('is-hidden');

    const current = getCurrentMode();

    if (current === 'matrix')       await exitMatrix();
    else if (current === 'island')  await exitIsland();
    else if (current === 'studio')  await exitStudio();
    else if (current === 'noir')    await exitNoir();
    else if (current === 'vault')   await exitVault();
    else if (current === 'kinetic') await exitKinetic();
    else                            await genericExit();

    clearModeClasses();
    if (target) HTML.classList.add(`mode-${target}`);
    updateIndicator();
    await wait(140);

    if (target === 'matrix')        await enterMatrix();
    else if (target === 'island')   await enterIsland();
    else if (target === 'studio')   await enterStudio();
    else if (target === 'noir')     await enterNoir();
    else if (target === 'vault')    await enterVault();
    else if (target === 'kinetic')  await enterKinetic();
    else                            await genericEnter();

    btn.classList.remove('is-hidden');
    void btn.offsetWidth;
    btn.classList.add('is-arriving');
    await wait(720);
    btn.classList.remove('is-arriving');

    HTML.classList.remove(TRANSITION_LOCK_CLASS);
  }

  // ─────────────────────────────────────────────────────────────────────
  // Click wiring
  // ─────────────────────────────────────────────────────────────────────
  btn.addEventListener('click', (e) => {
    e.preventDefault();
    if (HTML.classList.contains(TRANSITION_LOCK_CLASS)) return;
    transitionTo(pickNextMode());
  });

  // ─────────────────────────────────────────────────────────────────────
  // Idle signature: rare scroll glitch in Matrix mode
  // ─────────────────────────────────────────────────────────────────────
  let glitchTimer = null;
  function scheduleGlitch() {
    clearTimeout(glitchTimer);
    const delay = 45000 + Math.random() * 65000;
    glitchTimer = setTimeout(() => {
      if (getCurrentMode() === 'matrix' && !HTML.classList.contains(TRANSITION_LOCK_CLASS)) {
        HTML.classList.add('is-glitching');
        setTimeout(() => HTML.classList.remove('is-glitching'), 260);
      }
      scheduleGlitch();
    }, delay);
  }
  scheduleGlitch();

  window.__magicToggle = {
    pickNextMode, transitionTo, getCurrentMode, MODES,
    forceMode: (m) => transitionTo(m),
  };

  // ═══════════════════════════════════════════════════════════════════════
  // MATRIX nav scroll-collapse + cascade re-entry
  // Scroll down past 80px → .is-collapsed (only Связаться CTA visible)
  // Scroll up back to top → .is-reveal (cascade animation)
  // Only active in matrix mode.
  // ═══════════════════════════════════════════════════════════════════════
  (() => {
    const navBar = document.querySelector('.nav-bar');
    if (!navBar) return;
    let collapsed = false;
    let revealTimer = null;
    const THRESHOLD = 80;

    function onScroll() {
      if (!HTML.classList.contains('mode-matrix')) {
        navBar.classList.remove('is-collapsed', 'is-reveal');
        document.body.classList.remove('mx-nav-collapsed');
        collapsed = false;
        return;
      }
      const y = window.scrollY || window.pageYOffset;
      const shouldCollapse = y > THRESHOLD;
      if (shouldCollapse && !collapsed) {
        navBar.classList.add('is-collapsed');
        navBar.classList.remove('is-reveal');
        // Delay body class so floating-CTA appears AFTER nav fade starts
        setTimeout(() => {
          if (navBar.classList.contains('is-collapsed')) {
            document.body.classList.add('mx-nav-collapsed');
          }
        }, 300);
        collapsed = true;
      } else if (!shouldCollapse && collapsed) {
        navBar.classList.remove('is-collapsed');
        navBar.classList.add('is-reveal');
        document.body.classList.remove('mx-nav-collapsed');
        collapsed = false;
        clearTimeout(revealTimer);
        revealTimer = setTimeout(() => navBar.classList.remove('is-reveal'), 1200);
      }
    }
    window.addEventListener('scroll', onScroll, { passive: true });
    // Re-check on mode change too
    new MutationObserver(onScroll).observe(HTML, { attributes: true, attributeFilter: ['class'] });
  })();

  // ═══════════════════════════════════════════════════════════════════════
  // MATRIX scroll-reveal — sections + cards fade in with matrix flicker
  // when they enter viewport. Only active in matrix mode.
  // ═══════════════════════════════════════════════════════════════════════
  (() => {
    if (!('IntersectionObserver' in window)) return;
    const SELECTORS = [
      'section', '.credo-block', '.manifesto-block', '.stats-band',
      '.sticker-card', '.svc-card', '.testimonial-card', '.process-step',
      '.credo-item', '.stats-grid > *', '.section-headline'
    ];

    const observer = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return;
        // Only animate in matrix mode
        if (HTML.classList.contains('mode-matrix')) {
          entry.target.classList.add('mx-revealed');
        }
        observer.unobserve(entry.target);
      });
    }, { threshold: 0.12, rootMargin: '0px 0px -8% 0px' });

    function scan() {
      SELECTORS.forEach(sel => {
        document.querySelectorAll(sel).forEach(el => {
          if (!el.dataset.mxObserved) {
            el.dataset.mxObserved = '1';
            observer.observe(el);
          }
        });
      });
    }
    scan();
    // Re-scan on mode change (in case new elements appear)
    new MutationObserver(scan).observe(document.body, { childList: true, subtree: true });
  })();

  // ═══════════════════════════════════════════════════════════════════════
  // MATRIX premium effects — runs only in matrix mode:
  //   1. Custom mint cross-hair cursor + soft glow halo trail
  //   2. Decode-hover on nav-chips, logo, CTA buttons (letters shuffle)
  //   3. Thin horizontal scan-beam ползёт сверху вниз
  // ═══════════════════════════════════════════════════════════════════════
  (() => {
    // ─── 1) Custom cursor ─────────────────────────────────────────────
    const cur = document.createElement('div');
    cur.className = 'mx-cursor';
    document.body.appendChild(cur);
    const halo = document.createElement('div');
    halo.className = 'mx-cursor-halo';
    document.body.appendChild(halo);
    let mx = -100, my = -100, hx = -100, hy = -100, rafId = null;
    const onMove = (e) => { mx = e.clientX; my = e.clientY; };
    function tickCursor() {
      cur.style.transform  = `translate3d(${mx}px, ${my}px, 0) translate(-50%, -50%)`;
      hx += (mx - hx) * 0.16;
      hy += (my - hy) * 0.16;
      halo.style.transform = `translate3d(${hx}px, ${hy}px, 0) translate(-50%, -50%)`;
      rafId = requestAnimationFrame(tickCursor);
    }
    // Toggle cursor on/off with mode
    function refreshCursor() {
      const active = HTML.classList.contains('mode-matrix');
      document.body.classList.toggle('mx-fx-on', active);
      if (active && !rafId) {
        document.addEventListener('mousemove', onMove, { passive: true });
        rafId = requestAnimationFrame(tickCursor);
      } else if (!active && rafId) {
        document.removeEventListener('mousemove', onMove);
        cancelAnimationFrame(rafId);
        rafId = null;
      }
    }
    refreshCursor();
    new MutationObserver(refreshCursor).observe(HTML, { attributes: true, attributeFilter: ['class'] });

    // Enlarge cursor on interactive elements
    const interactive = 'a, button, [role="button"], input, textarea, select';
    document.addEventListener('mouseover', (e) => {
      if (!HTML.classList.contains('mode-matrix')) return;
      cur.classList.toggle('is-active', !!e.target.closest(interactive));
    }, { passive: true });

    // ─── 2) Decode hover ──────────────────────────────────────────────
    const GLYPHS = '01アイウエオカキクケコサシスセソタチツテト<>{}[]/\\=*+-|ABCDEFGHJK0123456789'.split('');
    function decodeText(el, durationMs = 380) {
      const finalText = el.dataset.mxOriginal || el.textContent;
      if (!el.dataset.mxOriginal) el.dataset.mxOriginal = finalText;
      const frames = 14;
      const len = finalText.length;
      let i = 0;
      clearInterval(el._mxTimer);
      el._mxTimer = setInterval(() => {
        const settled = Math.floor((len * i) / frames);
        let out = '';
        for (let c = 0; c < len; c++) {
          if (c < settled || finalText[c] === ' ') out += finalText[c];
          else out += GLYPHS[(Math.random() * GLYPHS.length) | 0];
        }
        el.textContent = out;
        i++;
        if (i > frames) {
          clearInterval(el._mxTimer);
          el.textContent = finalText;
        }
      }, durationMs / (frames + 1));
    }

    function bindDecode() {
      // Target: visible lang-span inside nav-chips, btn-primary, btn-secondary, logo
      const targets = document.querySelectorAll(
        '.nav-chips .btn-chip span.lang-ru, .nav-chips .btn-chip span.lang-en,' +
        '.nav-bar .btn-primary span.lang-ru, .nav-bar .btn-primary span.lang-en,' +
        '.logo span.lang-ru, .logo span.lang-en'
      );
      targets.forEach(span => {
        if (span.dataset.mxBound) return;
        span.dataset.mxBound = '1';
        const parent = span.closest('a, button');
        if (!parent) return;
        parent.addEventListener('mouseenter', () => {
          if (!HTML.classList.contains('mode-matrix')) return;
          // Only decode visible span (other lang hidden via CSS)
          const cs = getComputedStyle(span);
          if (cs.display === 'none') return;
          decodeText(span, 360);
        });
      });
    }
    bindDecode();
    new MutationObserver(bindDecode).observe(document.body, { childList: true, subtree: true });

    // ─── 3) Scan-beam ─────────────────────────────────────────────────
    const beam = document.createElement('div');
    beam.className = 'mx-scan-beam';
    document.body.appendChild(beam);

    // ─── 4) Viewport corner brackets ─────────────────────────────────
    ['tl', 'tr', 'bl', 'br'].forEach(pos => {
      const c = document.createElement('div');
      c.className = 'mx-frame-corner ' + pos;
      document.body.appendChild(c);
    });

    // ─── 5) HUD: live timestamp + section index ──────────────────────
    const hud = document.createElement('div');
    hud.className = 'mx-hud';
    hud.innerHTML =
      '<span class="mx-hud-time">--:--:--</span>' +
      '<span class="mx-hud-sec">00 / —</span>';
    document.body.appendChild(hud);

    const hudTime = hud.querySelector('.mx-hud-time');
    const hudSec  = hud.querySelector('.mx-hud-sec');
    function updateClock() {
      const d = new Date();
      const pad = (n) => String(n).padStart(2, '0');
      hudTime.textContent = pad(d.getHours()) + ':' + pad(d.getMinutes()) + ':' + pad(d.getSeconds());
    }
    updateClock();
    setInterval(updateClock, 1000);

    // ─── 6) Scroll-progress rail on right edge ───────────────────────
    const rail = document.createElement('div');
    rail.className = 'mx-rail';
    const railThumb = document.createElement('div');
    railThumb.className = 'mx-rail-thumb';
    rail.appendChild(railThumb);
    document.body.appendChild(rail);

    const SECTION_NAMES = [
      { sel: '#hero',         label: 'HERO' },
      { sel: '#services',     label: 'SERVICES' },
      { sel: '.stats-band',   label: 'NUMBERS' },
      { sel: '#process',      label: 'PROCESS' },
      { sel: '.credo-block.matrix-only', label: 'MANIFESTO' },
      { sel: '#testimonials', label: 'VOICES' },
      { sel: '.manifesto-block', label: 'CREDO' },
      { sel: '#contact',      label: 'CONTACT' },
    ];
    let lastScrollSync = 0;
    function onPageScroll() {
      const now = performance.now();
      if (now - lastScrollSync < 60) return;
      lastScrollSync = now;
      const doc = document.documentElement;
      const max = Math.max(1, (doc.scrollHeight - window.innerHeight));
      const ratio = Math.min(1, Math.max(0, window.scrollY / max));
      // Thumb position
      const railH = rail.clientHeight;
      const top = Math.round(ratio * (railH - railThumb.offsetHeight));
      railThumb.style.top = top + 'px';
      // Section index — find which section is mostly in view
      const vh = window.innerHeight;
      let bestIdx = 0;
      for (let i = 0; i < SECTION_NAMES.length; i++) {
        const el = document.querySelector(SECTION_NAMES[i].sel);
        if (!el) continue;
        const r = el.getBoundingClientRect();
        if (r.top < vh * 0.6) bestIdx = i;
      }
      const cur = SECTION_NAMES[bestIdx];
      const num = String(bestIdx + 1).padStart(2, '0');
      hudSec.textContent = num + ' / ' + cur.label;
    }
    window.addEventListener('scroll', onPageScroll, { passive: true });
    setTimeout(onPageScroll, 100);

    // ─── 7) Drifting kanji/digit dust ────────────────────────────────
    const DUST_CHARS = ['ア', 'カ', 'タ', 'ナ', 'マ', '0', '1', '7', '/', '<', '>', '{', '}'];
    for (let i = 1; i <= 5; i++) {
      const d = document.createElement('div');
      d.className = 'mx-dust d' + i;
      d.textContent = DUST_CHARS[(Math.random() * DUST_CHARS.length) | 0];
      document.body.appendChild(d);
    }
    // Periodically swap char so it doesn't feel static
    setInterval(() => {
      if (!document.body.classList.contains('mx-fx-on')) return;
      document.querySelectorAll('.mx-dust').forEach(d => {
        if (Math.random() < 0.35) d.textContent = DUST_CHARS[(Math.random() * DUST_CHARS.length) | 0];
      });
    }, 3000);

    // ─── 8) Vertical edge pulse rails (left + right of viewport) ─────
    const eL = document.createElement('div');
    eL.className = 'mx-edge left';
    const eR = document.createElement('div');
    eR.className = 'mx-edge right';
    document.body.appendChild(eL);
    document.body.appendChild(eR);
  })();

  // ═══════════════════════════════════════════════════════════════════════
  // MATRIX headline decode-reveal on scroll-in
  // Each section eyebrow + headline scrambles letters briefly, then settles
  // when first entering viewport (in matrix mode only).
  // ═══════════════════════════════════════════════════════════════════════
  (() => {
    if (!('IntersectionObserver' in window)) return;
    const GLYPHS = '01アイウエオカキクケコサシスセソタチツテト<>{}[]/\\=*+-|ABCDEFGHJK'.split('');

    // (v2.7 2026-05-12) Snappier: 280ms / 10 frames. Width-locked during
    // scramble — measures the span's current rect (final text width) and
    // pins min-width inline so the layout doesn't dance. Restored on finish.
    function decodeSpan(span, finalText, durationMs = 280) {
      if (!finalText || finalText.length < 1) return;
      // Width-lock to kill mid-scramble jitter (only when span has a width)
      const rect = span.getBoundingClientRect();
      if (rect.width > 0 && !span.dataset.mxWidthLock) {
        span.dataset.mxWidthLock = '1';
        span.dataset.mxPrevMinWidth = span.style.minWidth || '';
        span.style.minWidth = Math.ceil(rect.width) + 'px';
      }
      span.classList.add('mx-decoding');
      const frames = 10;
      const len = finalText.length;
      let i = 0;
      clearInterval(span._mxDecodeT);
      span._mxDecodeT = setInterval(() => {
        const settled = Math.floor((len * i) / frames);
        let out = '';
        for (let c = 0; c < len; c++) {
          const ch = finalText[c];
          if (c < settled || ch === ' ' || ch === ' ') out += ch;
          else out += GLYPHS[(Math.random() * GLYPHS.length) | 0];
        }
        span.textContent = out;
        i++;
        if (i > frames) {
          clearInterval(span._mxDecodeT);
          span.textContent = finalText;
          span.classList.remove('mx-decoding');
          // Release width-lock (v2.7)
          if (span.dataset.mxWidthLock) {
            span.style.minWidth = span.dataset.mxPrevMinWidth || '';
            delete span.dataset.mxWidthLock;
            delete span.dataset.mxPrevMinWidth;
          }
        }
      }, durationMs / (frames + 1));
    }

    // ── v2.8 (2026-05-12): selectors AUDITED against real index.html.
    // Real structure facts:
    //   - .svc-body / .svc-bullets are the lang-carrying element (NOT a span inside)
    //   - .ps-body / .quote / .role contain inner span.lang-{ru,en}
    //   - .hero-meta .small and .hero-text .sub spans have inline <br>/<strong>
    //     INSIDE them → decode would destroy that markup → EXCLUDED
    //   - manifesto / .hero-text .line / .credo-headline — visually too large for scramble → EXCLUDED
    //
    // Width-lock in decodeSpan() prevents reflow during scramble.
    const HEADLINE_SEL = [
      // Section structure
      '.section-headline span.lang-ru', '.section-headline span.lang-en',
      '.section-eyebrow span.lang-ru', '.section-eyebrow span.lang-en',
      '.section-eyebrow',
      // Stats — .num excluded; v3.3 count-up animation handles it.
      '.stats-grid .label span.lang-ru', '.stats-grid .label span.lang-en',
      // Services (#services section — bullets + closing only; svc-body lives in #work)
      '#services .sticker-card .svc-name span.lang-ru',
      '#services .sticker-card .svc-name span.lang-en',
      '#services .sticker-card .svc-bullets.lang-ru li',
      '#services .sticker-card .svc-bullets.lang-en li',
      '#services .sticker-card .svc-closing span.lang-ru',
      '#services .sticker-card .svc-closing span.lang-en',
      // Work / Case studies (svc-body lives HERE, not in #services — corrected v2.8)
      '#work .sticker-card .svc-name span.lang-ru',
      '#work .sticker-card .svc-name span.lang-en',
      '#work .sticker-card .svc-body.lang-ru',   // class on <p> itself
      '#work .sticker-card .svc-body.lang-en',
      '#work .sticker-card .case-meta',
      '#work .sticker-card .btn-chip-copper',
      // Process
      '#process .process-step .ps-name span.lang-ru',
      '#process .process-step .ps-name span.lang-en',
      '#process .process-step .ps-body span.lang-ru',
      '#process .process-step .ps-body span.lang-en',
      // Testimonials
      '#testimonials .testimonial-card .quote span.lang-ru',
      '#testimonials .testimonial-card .quote span.lang-en',
      '#testimonials .testimonial-card .author',
      '#testimonials .testimonial-card .role span.lang-ru',
      '#testimonials .testimonial-card .role span.lang-en',
      // Hero — badge + cta label only (.small and .sub spans contain <br>/<strong>)
      '.hero-meta .btn-chip-copper',
      '.hero-cta .label span.lang-ru', '.hero-cta .label span.lang-en',
      // Contact
      '#contact .section-headline span.lang-ru',
      '#contact .section-headline span.lang-en',
      // (v3.2 2026-05-12) Large poster headlines re-included after width-lock fix.
      // Hero "Меньше слов. / Больше результата." + manifesto "Мы — DEADLINE…"
      // — scroll-decode applies, parent layout isolated via CSS `contain`.
      '.hero-text .line span.lang-ru', '.hero-text .line span.lang-en',
      '.manifesto-text span.lang-ru', '.manifesto-text span.lang-en',
      '.credo-block .credo-headline .line span.lang-ru',
      '.credo-block .credo-headline .line span.lang-en'
    ].join(',');

    // ── v2.1 (2026-05-12): decode fires EVERY time element enters viewport
    // (both scroll directions), debounced per-element to 1.2s so quick
    // back-and-forth doesn't stutter. unobserve() removed.
    const observer = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return;
        if (!HTML.classList.contains('mode-matrix')) return;
        const el = entry.target;
        // Snapshot original text on first decode (only once)
        if (!el.dataset.mxOrig) el.dataset.mxOrig = el.textContent;
        // Skip if hidden (other lang span)
        const cs = getComputedStyle(el);
        if (cs.display === 'none') return;
        // Per-element debounce: avoid retriggering during the decode itself
        // or immediately after, but allow re-decode after a real exit/return.
        const now = performance.now();
        const last = Number(el.dataset.mxLastDecode || 0);
        if (now - last < 1500) return;
        el.dataset.mxLastDecode = String(now);
        decodeSpan(el, el.dataset.mxOrig);  // v2.4: use default 380ms (faster)
      });
    }, { threshold: [0, 0.15, 0.5], rootMargin: '0px 0px -5% 0px' });

    function scanHeadlines() {
      document.querySelectorAll(HEADLINE_SEL).forEach(el => {
        if (el.dataset.mxHeadlineObserved) return;
        // Don't re-decode something already empty
        if (!el.textContent.trim()) return;
        el.dataset.mxHeadlineObserved = '1';
        observer.observe(el);
      });
    }
    scanHeadlines();
    new MutationObserver(scanHeadlines).observe(document.body, { childList: true, subtree: true });

    // ── v2.2 (2026-05-12): scroll-safety-net.
    // IntersectionObserver only fires at threshold crossings — if user
    // scrolls partway and stops, an element fully in view but not crossing
    // 0.15 won't re-trigger. This idle-after-scroll pass guarantees every
    // visible un-recent decode fires regardless of crossing direction.
    let safetyTimer = null;
    function safetyScan() {
      if (!HTML.classList.contains('mode-matrix')) return;
      const vh = window.innerHeight;
      const now = performance.now();
      document.querySelectorAll(HEADLINE_SEL).forEach(el => {
        const r = el.getBoundingClientRect();
        if (r.bottom < 0 || r.top > vh) return;        // off-screen
        const cs = getComputedStyle(el);
        if (cs.display === 'none') return;
        if (!el.dataset.mxOrig) el.dataset.mxOrig = el.textContent;
        const last = Number(el.dataset.mxLastDecode || 0);
        if (now - last < 1500) return;
        el.dataset.mxLastDecode = String(now);
        decodeSpan(el, el.dataset.mxOrig);  // v2.4: 380ms default
      });
    }
    window.addEventListener('scroll', () => {
      clearTimeout(safetyTimer);
      safetyTimer = setTimeout(safetyScan, 250);
    }, { passive: true });

    // When user switches INTO matrix mode after page has loaded,
    // re-decode visible headlines so the effect happens.
    new MutationObserver(() => {
      if (!HTML.classList.contains('mode-matrix')) return;
      // Trigger decode on currently-visible headlines
      document.querySelectorAll(HEADLINE_SEL).forEach(el => {
        const r = el.getBoundingClientRect();
        if (r.bottom < 0 || r.top > window.innerHeight) return;
        const cs = getComputedStyle(el);
        if (cs.display === 'none') return;
        const original = el.dataset.mxOrig || el.textContent;
        if (!el.dataset.mxOrig) el.dataset.mxOrig = original;
        decodeSpan(el, original);  // v2.4: 380ms default
      });
    }).observe(HTML, { attributes: true, attributeFilter: ['class'] });
  })();

  // ═══════════════════════════════════════════════════════════════════════
  // Matrix rain drip from Связаться button (nav)
  // Tiny mint character falls from below the button every ~1.6s
  // ═══════════════════════════════════════════════════════════════════════
  (() => {
    const DRIP_CHARS = ['0', '1', 'ア', 'カ', 'タ', '/', '<', '>'];
    let dripTimer = null;

    function spawnDripOn(host) {
      if (!host) return;
      if (!HTML.classList.contains('mode-matrix')) return;
      const drip = document.createElement('span');
      drip.className = 'mx-drip';
      drip.textContent = DRIP_CHARS[(Math.random() * DRIP_CHARS.length) | 0];
      const offsetPct = 5 + Math.random() * 90; // 5%-95% of host width
      drip.style.left = offsetPct + '%';
      drip.style.setProperty('--mx-drip-x', '-50%');
      // Random fall distance — different floors per drip (40-180px)
      const dist = 40 + Math.floor(Math.random() * 140);
      drip.style.setProperty('--mx-drip-dist', dist + 'px');
      // Random duration scaled to distance (longer fall = longer time)
      const dur = (1.2 + (dist / 180) * 1.6 + Math.random() * 0.4).toFixed(2);
      drip.style.animationDuration = dur + 's';
      // Randomize font-size slightly
      drip.style.fontSize = (11 + Math.random() * 4).toFixed(1) + 'px';
      host.appendChild(drip);
      setTimeout(() => drip.remove(), (parseFloat(dur) * 1000) + 200);
    }

    // Floating СВЯЗАТЬСЯ pinned at top-right of viewport, independent of nav
    function ensureFloatingCTA() {
      if (!HTML.classList.contains('mode-matrix')) return null;
      let cta = document.querySelector('.mx-floating-cta');
      if (cta) return cta;
      const navBtn = document.querySelector('.nav-bar .btn-primary');
      cta = document.createElement('a');
      cta.className = 'mx-floating-cta';
      cta.href = navBtn ? (navBtn.getAttribute('href') || 'https://t.me/deadline_corp') : 'https://t.me/deadline_corp';
      cta.target = '_blank';
      cta.rel = 'noopener';
      // Mirror visible lang text
      const ru = document.createElement('span');
      ru.className = 'lang-ru';
      ru.textContent = 'Связаться';
      const en = document.createElement('span');
      en.className = 'lang-en';
      en.textContent = 'Talk to us';
      cta.appendChild(ru);
      cta.appendChild(en);
      document.body.appendChild(cta);
      return cta;
    }
    function removeFloatingCTA() {
      const cta = document.querySelector('.mx-floating-cta');
      if (cta) cta.remove();
    }

    function spawnDrip() {
      // Drip targets — floating CTA (pinned) + DEADLINE logo if visible
      const cta = ensureFloatingCTA();
      const navBar = document.querySelector('.nav-bar');
      const navCollapsed = navBar && navBar.classList.contains('is-collapsed');
      const logo = !navCollapsed ? document.querySelector('.nav-bar .logo') : null;
      const targets = [cta, logo].filter(Boolean);
      if (!targets.length) return;
      const host = targets[(Math.random() * targets.length) | 0];
      spawnDripOn(host);
      if (Math.random() < 0.35 && targets.length > 1) {
        const other = targets.find(t => t !== host);
        if (other) spawnDripOn(other);
      }
    }

    function startDrip() {
      stopDrip();
      ensureFloatingCTA();
      // (v2 2026-05-12) Only spawn initial bursts if tab is visible
      if (!document.hidden) {
        setTimeout(spawnDrip, 300);
        setTimeout(spawnDrip, 700);
      }
      function loop() {
        // (v2) Smart auto-pause: skip spawn when tab is hidden, but keep
        // the loop scheduled so resume is instant when user returns.
        if (!document.hidden) spawnDrip();
        dripTimer = setTimeout(loop, 600 + Math.random() * 800);
      }
      dripTimer = setTimeout(loop, 1100);
    }
    function stopDrip() {
      if (dripTimer) { clearTimeout(dripTimer); dripTimer = null; }
      document.querySelectorAll('.mx-drip').forEach(d => d.remove());
    }

    function check() {
      const inMatrix = HTML.classList.contains('mode-matrix');
      if (inMatrix) {
        ensureFloatingCTA();
        startDrip();
      } else {
        stopDrip();
        removeFloatingCTA();
      }
    }
    check();
    new MutationObserver(check).observe(HTML, { attributes: true, attributeFilter: ['class'] });
    const navBarEl = document.querySelector('.nav-bar');
    if (navBarEl) {
      new MutationObserver(check).observe(navBarEl, { attributes: true, attributeFilter: ['class'] });
    }
  })();

  // ═══════════════════════════════════════════════════════════════════════
  // MATRIX v2 EFFECTS (added 2026-05-12)
  //   1  — lens vignette + chromatic aberration (CSS-only, just mount nodes)
  //   2  — cinematic letterbox around manifesto (IntersectionObserver)
  //   3  — type-on-load hero headline with blinking caret
  //   4  — cursor-reactive ambient aura (mousemove → CSS vars)
  //   6  — glyph-bleed on CTA hover (sets data-mx-bleed for ::before)
  //   9  — covered above in drip startDrip() via document.hidden check
  //   10 — colour drift (pure CSS keyframe, no JS needed)
  // To disable any one — see CHANGES_MATRIX_v2.md.
  // ═══════════════════════════════════════════════════════════════════════
  (() => {
    // ── v2-1: Mount vignette + aberration overlays (CSS-driven visibility)
    if (!document.querySelector('.mx-cinema-vignette')) {
      const v = document.createElement('div');
      v.className = 'mx-cinema-vignette';
      v.setAttribute('aria-hidden', 'true');
      document.body.appendChild(v);
    }
    if (!document.querySelector('.mx-cinema-aberration')) {
      const a = document.createElement('div');
      a.className = 'mx-cinema-aberration';
      a.setAttribute('aria-hidden', 'true');
      document.body.appendChild(a);
    }

    // ── v2-2: Cinematic letterbox — appears around manifesto block
    if (!document.querySelector('.mx-letterbox.top')) {
      ['top', 'bottom'].forEach(pos => {
        const bar = document.createElement('div');
        bar.className = 'mx-letterbox ' + pos;
        bar.setAttribute('aria-hidden', 'true');
        document.body.appendChild(bar);
      });
    }
    // (v3.0 2026-05-12) Letterbox is now GLOBAL — appears as soon as user
    // scrolls past the first viewport (hero), stays the whole way down, and
    // retracts back when they scroll up to hero. Cinematic "frame" wrapping
    // the entire site except the opening shot.
    let lbScrollPending = false;
    function lbUpdate() {
      lbScrollPending = false;
      if (!HTML.classList.contains('mode-matrix')) {
        document.body.classList.remove('mx-letterbox-on');
        return;
      }
      // Trigger after user has scrolled past 60% of hero. Going back up
      // retracts the bars before hero is fully on-screen for a clean look.
      const threshold = Math.max(180, window.innerHeight * 0.6);
      const past = (window.scrollY || window.pageYOffset || 0) > threshold;
      document.body.classList.toggle('mx-letterbox-on', past);
    }
    function lbOnScroll() {
      if (lbScrollPending) return;
      lbScrollPending = true;
      requestAnimationFrame(lbUpdate);
    }
    window.addEventListener('scroll', lbOnScroll, { passive: true });
    window.addEventListener('resize', lbOnScroll, { passive: true });
    // Re-evaluate on mode change (so leaving matrix removes bars instantly)
    new MutationObserver(lbUpdate).observe(HTML, { attributes: true, attributeFilter: ['class'] });
    // Initial check
    lbUpdate();

    // ── v2-3: Type-on-load hero headline
    // Triggers when matrix mode is entered and hero is visible. Once per
    // page-load OR per mode-entry. The 3 hero text lines type sequentially.
    let typedDone = false;
    function typeLine(el, finalText, durationMs) {
      return new Promise(resolve => {
        const total = finalText.length;
        if (total === 0) { resolve(); return; }
        // Insert caret as sibling
        const caret = document.createElement('span');
        caret.className = 'mx-caret';
        caret.setAttribute('aria-hidden', 'true');
        el.textContent = '';
        el.appendChild(caret);
        let i = 0;
        const step = Math.max(18, Math.floor(durationMs / total));
        const timer = setInterval(() => {
          i++;
          el.textContent = finalText.slice(0, i);
          el.appendChild(caret);
          if (i >= total) {
            clearInterval(timer);
            setTimeout(() => { caret.remove(); resolve(); }, 420);
          }
        }, step);
      });
    }
    async function runHeroType() {
      if (typedDone) return;
      if (!HTML.classList.contains('mode-matrix')) return;
      // Pick visible language spans inside .hero-text .line
      const lines = [...document.querySelectorAll('.hero-text .line')];
      if (!lines.length) return;
      typedDone = true; // mark before await to prevent double-trigger
      for (const lineEl of lines) {
        const span = [...lineEl.querySelectorAll('span.lang-ru, span.lang-en')]
          .find(s => getComputedStyle(s).display !== 'none');
        if (!span) continue;
        const original = span.dataset.mxTypeOrig || span.textContent;
        if (!span.dataset.mxTypeOrig) span.dataset.mxTypeOrig = original;
        await typeLine(span, original, 380);  // v2.4: faster hero typewriter
      }
    }
    // Trigger on first matrix-entry
    if (HTML.classList.contains('mode-matrix')) {
      setTimeout(runHeroType, 250);
    }
    new MutationObserver(() => {
      if (HTML.classList.contains('mode-matrix')) runHeroType();
    }).observe(HTML, { attributes: true, attributeFilter: ['class'] });

    // ── v2-4: Cursor-reactive ambient aura
    let auraEl = document.querySelector('.mx-cursor-aura');
    if (!auraEl) {
      auraEl = document.createElement('div');
      auraEl.className = 'mx-cursor-aura';
      auraEl.setAttribute('aria-hidden', 'true');
      document.body.appendChild(auraEl);
    }
    let curX = window.innerWidth / 2, curY = window.innerHeight / 2;
    let auraX = curX, auraY = curY;
    let auraRAF = null;
    function tickAura() {
      auraX += (curX - auraX) * 0.08;
      auraY += (curY - auraY) * 0.08;
      auraEl.style.transform =
        'translate3d(' + (auraX - 300) + 'px, ' + (auraY - 300) + 'px, 0)';
      auraRAF = requestAnimationFrame(tickAura);
    }
    function onAuraMove(e) { curX = e.clientX; curY = e.clientY; }
    function refreshAura() {
      const on = HTML.classList.contains('mode-matrix');
      if (on && !auraRAF) {
        document.addEventListener('mousemove', onAuraMove, { passive: true });
        auraRAF = requestAnimationFrame(tickAura);
      } else if (!on && auraRAF) {
        document.removeEventListener('mousemove', onAuraMove);
        cancelAnimationFrame(auraRAF);
        auraRAF = null;
      }
    }
    refreshAura();
    new MutationObserver(refreshAura).observe(HTML, { attributes: true, attributeFilter: ['class'] });

    // ── v2-6: Glyph-bleed on CTA hover (sets data-mx-bleed for CSS ::before)
    // Picks the currently-visible lang span text and writes it to data-mx-bleed
    // so the ::before pseudo can duplicate it with mint-shifted shadow.
    function bindCtaBleed() {
      const ctas = document.querySelectorAll('.btn-primary, .btn-secondary');
      ctas.forEach(cta => {
        if (cta.dataset.mxBleedBound) return;
        cta.dataset.mxBleedBound = '1';
        const updateBleed = () => {
          if (!HTML.classList.contains('mode-matrix')) return;
          const vis = [...cta.querySelectorAll('span.lang-ru, span.lang-en')]
            .find(s => getComputedStyle(s).display !== 'none');
          const txt = vis ? vis.textContent.trim() : cta.textContent.trim();
          cta.setAttribute('data-mx-bleed', txt);
        };
        cta.addEventListener('mouseenter', updateBleed);
        // Set once for first paint
        updateBleed();
      });
    }
    bindCtaBleed();
    new MutationObserver(bindCtaBleed).observe(document.body, { childList: true, subtree: true });
  })();

  // ═══════════════════════════════════════════════════════════════════════
  // MATRIX v2.1 EFFECTS (added 2026-05-12)
  //   A — firefly motes: 7 mint dots breathe at random positions
  //   B — card hover glow: subtle mint pulse on services / testimonials /
  //       process / stats cards (CSS-only, JS just ensures matrix scope)
  //   C — stat number ambient flicker: live mint pulse on .stats-grid .num
  // ═══════════════════════════════════════════════════════════════════════
  (() => {
    // ── v2.1-A: Spawn firefly mote nodes once. CSS animates them. ──
    if (!document.querySelector('.mx-firefly')) {
      const FIREFLY_COUNT = 7;
      for (let i = 0; i < FIREFLY_COUNT; i++) {
        const f = document.createElement('div');
        f.className = 'mx-firefly mx-firefly-' + (i + 1);
        f.setAttribute('aria-hidden', 'true');
        // Randomize position + drift per mote
        f.style.setProperty('--mx-fly-x',  (5 + Math.random() * 90).toFixed(1) + '%');
        f.style.setProperty('--mx-fly-y',  (5 + Math.random() * 90).toFixed(1) + '%');
        f.style.setProperty('--mx-fly-dx', (-30 + Math.random() * 60).toFixed(0) + 'px');
        f.style.setProperty('--mx-fly-dy', (-40 + Math.random() * 80).toFixed(0) + 'px');
        f.style.animationDuration = (6 + Math.random() * 7).toFixed(2) + 's';
        f.style.animationDelay = (-Math.random() * 5).toFixed(2) + 's';
        document.body.appendChild(f);
      }
    }
    // (B and C are pure CSS — see modes.css v2.1 block.)
  })();

  // ═══════════════════════════════════════════════════════════════════════
  // MATRIX v3.3 EFFECTS (added 2026-05-12)
  //   #2 — DevTools console easter egg
  //   #4 — Magnetic CTA hover (matrix only)
  //   #5 — Stat numbers count-up on first viewport-entry
  // ═══════════════════════════════════════════════════════════════════════
  (() => {
    // ── v3.3-#2: DevTools easter egg — printed once on page load ────────
    try {
      const accent = 'color:#7AC79A;font:13px/1.6 "JetBrains Mono",monospace;';
      const muted  = 'color:#8A7E64;font:11px/1.6 "JetBrains Mono",monospace;';
      const link   = 'color:#CFE4D6;font:12px/1.6 "JetBrains Mono",monospace;text-decoration:underline;';
      console.log(
        '%c\n  //  D E A D L I N E   ─────────────────────────────\n' +
        '%c  //  Ничего не горит.\n  //\n' +
        '%c  //  Видишь это? Значит ты технарь.\n' +
        '%c  //  Мы делаем web · automation · AI-агенты в production.\n' +
        '%c  //  Берёмся за всё. Дедлайны нас боятся.\n  //\n' +
        '%c  //  →  corpdeadline@gmail.com  ·  https://t.me/deadline_corp\n\n',
        accent, accent, muted, muted, muted, link
      );
    } catch (e) { /* ignore — IE/old browsers */ }

    // ── v3.3-#4: Magnetic CTA hover (matrix mode + pointer devices only) ─
    if (window.matchMedia('(hover: hover)').matches) {
      const CTA_SELECTOR = '.btn-primary, .btn-secondary';
      const MAX_PULL = 6;     // px the button can drift toward cursor
      const STRENGTH = 0.18;  // how much of the offset to use

      function bindMagnetic(cta) {
        if (cta.dataset.mxMagBound) return;
        cta.dataset.mxMagBound = '1';
        let rect = null;
        let raf  = null;

        function onEnter() {
          if (!HTML.classList.contains('mode-matrix')) return;
          rect = cta.getBoundingClientRect();
        }
        function onMove(e) {
          if (!rect || !HTML.classList.contains('mode-matrix')) return;
          const cx = rect.left + rect.width / 2;
          const cy = rect.top  + rect.height / 2;
          const dx = (e.clientX - cx) * STRENGTH;
          const dy = (e.clientY - cy) * STRENGTH;
          const tx = Math.max(-MAX_PULL, Math.min(MAX_PULL, dx));
          const ty = Math.max(-MAX_PULL, Math.min(MAX_PULL, dy));
          if (raf) cancelAnimationFrame(raf);
          raf = requestAnimationFrame(() => {
            cta.style.setProperty('--mx-mag-x', tx + 'px');
            cta.style.setProperty('--mx-mag-y', ty + 'px');
            cta.classList.add('mx-magnetic');
          });
        }
        function onLeave() {
          rect = null;
          cta.classList.remove('mx-magnetic');
          cta.style.setProperty('--mx-mag-x', '0px');
          cta.style.setProperty('--mx-mag-y', '0px');
        }
        cta.addEventListener('mouseenter', onEnter, { passive: true });
        cta.addEventListener('mousemove',  onMove,  { passive: true });
        cta.addEventListener('mouseleave', onLeave, { passive: true });
      }
      function scanCtas() {
        document.querySelectorAll(CTA_SELECTOR).forEach(bindMagnetic);
      }
      scanCtas();
      new MutationObserver(scanCtas).observe(document.body, { childList: true, subtree: true });
    }

    // ── v3.3-#5: Stat numbers count-up (e.g. "12+", "0", "100%", "8") ────
    if ('IntersectionObserver' in window) {
      function parseStat(text) {
        // Returns {value, suffix} — supports "12+", "100%", "0", "8"
        const m = String(text || '').trim().match(/^(\d+)([^\d]*)$/);
        if (!m) return null;
        return { value: parseInt(m[1], 10), suffix: m[2] || '' };
      }

      function countUp(el, target, suffix, durationMs = 750) {
        // Snapshot original so we restore exactly on settle
        const original = (el.dataset.mxStatOrig = el.dataset.mxStatOrig || el.textContent);
        const start = performance.now();
        clearInterval(el._mxCountT);
        function frame(now) {
          const t = Math.min(1, (now - start) / durationMs);
          // Easing: easeOutQuart for a confident finish
          const eased = 1 - Math.pow(1 - t, 4);
          const cur = Math.floor(target * eased);
          el.textContent = cur + (t >= 1 ? suffix : '');
          if (t < 1) {
            el._mxCountT = requestAnimationFrame(frame);
          } else {
            el.textContent = original;  // exact final string
          }
        }
        el._mxCountT = requestAnimationFrame(frame);
      }

      const statObserver = new IntersectionObserver((entries) => {
        entries.forEach(entry => {
          if (!entry.isIntersecting) return;
          if (!HTML.classList.contains('mode-matrix')) return;
          const el = entry.target;
          // Debounce: skip if counted in last 1.8s
          const now = performance.now();
          const last = Number(el.dataset.mxStatLast || 0);
          if (now - last < 1800) return;
          el.dataset.mxStatLast = String(now);
          const parsed = parseStat(el.dataset.mxStatOrig || el.textContent);
          if (!parsed) return;
          countUp(el, parsed.value, parsed.suffix, 750);
        });
      }, { threshold: [0, 0.3], rootMargin: '0px 0px -8% 0px' });

      function bindStats() {
        document.querySelectorAll('.stats-grid .num').forEach(el => {
          if (el.dataset.mxStatObs) return;
          el.dataset.mxStatObs = '1';
          // Cache the original text once
          if (!el.dataset.mxStatOrig) el.dataset.mxStatOrig = el.textContent.trim();
          statObserver.observe(el);
        });
      }
      bindStats();
      new MutationObserver(bindStats).observe(document.body, { childList: true, subtree: true });
    }
  })();

  // ═══════════════════════════════════════════════════════════════════════
  // DRAGGABLE MAGIC-TOGGLE (v3.6 2026-05-13)
  //   • Touch + mouse drag the button around the viewport
  //   • Position persisted in localStorage between page-loads + skin swaps
  //   • Distinguishes tap (≤8px movement) from drag — tap still cycles skin
  //   • Always reachable: clears the during-transition pointer-events block
  //   • Constrained to viewport with 8px safety margin
  // ═══════════════════════════════════════════════════════════════════════
  (() => {
    const btn = document.getElementById('magic-toggle');
    if (!btn) return;

    const LS_KEY = 'mx-toggle-pos';
    const DRAG_THRESHOLD = 8;  // px — below this counts as tap, not drag
    const EDGE_MARGIN = 8;

    let dragging = false;
    let didMove  = false;
    let startX = 0, startY = 0;
    let btnStartX = 0, btnStartY = 0;
    let suppressNextClick = false;

    function applyPosition(x, y) {
      const w = btn.offsetWidth  || 46;
      const h = btn.offsetHeight || 36;
      const maxX = window.innerWidth  - w - EDGE_MARGIN;
      const maxY = window.innerHeight - h - EDGE_MARGIN;
      x = Math.max(EDGE_MARGIN, Math.min(maxX, x));
      y = Math.max(EDGE_MARGIN, Math.min(maxY, y));
      // (v3.6.1) setProperty with 'important' priority — beats the many
      // `top/right: ... !important` rules in modes.css that lock the
      // default position. Without this, JS inline writes do nothing.
      btn.style.setProperty('left',   x + 'px', 'important');
      btn.style.setProperty('top',    y + 'px', 'important');
      btn.style.setProperty('right',  'auto',  'important');
      btn.style.setProperty('bottom', 'auto',  'important');
    }

    function loadSaved() {
      try {
        const raw = localStorage.getItem(LS_KEY);
        if (!raw) return;
        const pos = JSON.parse(raw);
        if (typeof pos.x === 'number' && typeof pos.y === 'number') {
          applyPosition(pos.x, pos.y);
        }
      } catch (e) { /* ignore */ }
    }
    function savePosition() {
      try {
        const r = btn.getBoundingClientRect();
        localStorage.setItem(LS_KEY, JSON.stringify({ x: r.left, y: r.top }));
      } catch (e) { /* ignore */ }
    }

    function eventPoint(e) {
      if (e.touches && e.touches[0]) return { x: e.touches[0].clientX, y: e.touches[0].clientY };
      if (e.changedTouches && e.changedTouches[0]) return { x: e.changedTouches[0].clientX, y: e.changedTouches[0].clientY };
      return { x: e.clientX, y: e.clientY };
    }

    function onStart(e) {
      const r = btn.getBoundingClientRect();
      btnStartX = r.left;
      btnStartY = r.top;
      const p = eventPoint(e);
      startX = p.x;
      startY = p.y;
      dragging = true;
      didMove  = false;
      btn.classList.add('is-dragging');
      // (v3.6.3) DO NOT preventDefault on touchstart — it cancels the
      // synthetic click that mobile browsers fire on tap. Without that
      // click, taps stop cycling skins. Drag-during-scroll prevention
      // is handled in touchmove instead (only after we know it's a drag).
    }
    function onMove(e) {
      if (!dragging) return;
      const p = eventPoint(e);
      const dx = p.x - startX;
      const dy = p.y - startY;
      if (!didMove && (Math.abs(dx) + Math.abs(dy) > DRAG_THRESHOLD)) didMove = true;
      if (didMove) {
        applyPosition(btnStartX + dx, btnStartY + dy);
        // Only block scroll AFTER we're sure this is a drag, not a tap
        if (e.type === 'touchmove' && e.cancelable) e.preventDefault();
      }
    }
    function onEnd(e) {
      if (!dragging) return;
      dragging = false;
      btn.classList.remove('is-dragging');
      if (didMove) {
        savePosition();
        suppressNextClick = true;
      } else if (e && e.type === 'touchend') {
        // Mobile-only fallback: if no native click fires (some browsers
        // skip it under fast-tap heuristics), trigger one manually.
        // Use a microtask delay so any organic click fires first.
        setTimeout(() => {
          if (!suppressNextClick) {
            // Check if a click happened in the last 50ms by reading flag
            if (!btn._mxClickFired) btn.click();
          }
          btn._mxClickFired = false;
        }, 60);
      }
    }
    // Track natively-fired clicks so the touchend fallback doesn't double-fire
    btn.addEventListener('click', () => { btn._mxClickFired = true; }, true);

    // Wire it: button captures start, window captures move/end so the
    // drag survives even when the finger/cursor leaves the button bounds.
    btn.addEventListener('mousedown',  onStart);
    btn.addEventListener('touchstart', onStart, { passive: false });
    window.addEventListener('mousemove',  onMove);
    window.addEventListener('touchmove',  onMove, { passive: false });
    window.addEventListener('mouseup',    onEnd);
    window.addEventListener('touchend',   onEnd);
    window.addEventListener('touchcancel', onEnd);

    // Swallow the click that follows a drag (browser fires click after mouseup/touchend)
    btn.addEventListener('click', (e) => {
      if (suppressNextClick) {
        e.preventDefault();
        e.stopImmediatePropagation();
        suppressNextClick = false;
      }
    }, true);

    // Re-clamp on viewport resize / orientation change
    window.addEventListener('resize', () => {
      const r = btn.getBoundingClientRect();
      applyPosition(r.left, r.top);
      savePosition();
    });
    // Apply saved position once on load (after CSS terminator has set defaults)
    // Slight delay so any other init finishes first.
    setTimeout(loadSaved, 50);
  })();
})();

/* ═══════════════════════════════════════════════════════════════════════
   SERVICE WORKER REGISTRATION (v3.7, 2026-05-13)
   Enables offline support + faster repeat visits.
   sw.js handles the cache strategy.
   ═══════════════════════════════════════════════════════════════════════ */
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker
      .register('sw.js', { scope: './' })
      .catch((err) => {
        // Don't break the site if SW registration fails (e.g. file:// protocol)
        console.warn('[sw] registration failed:', err.message);
      });
  });
}
