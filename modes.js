/* ═══════════════════════════════════════════════════════════════════════
   MAGIC MODE TOGGLE — orchestrator
     Phase 1: button + transition lock + class swap
     Phase 2: Matrix theatre + idle signatures
     Phase 3: Island theatre + idle signatures
     Phase 4: Studio theatre + idle signatures (curtain, parallax, spotlight)
   ═══════════════════════════════════════════════════════════════════════ */
(() => {
  'use strict';

  const MODES = ['matrix', 'island', 'studio'];
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

  function pickNextMode() {
    const current = getCurrentMode();
    const candidates = MODES.filter(m => m !== current);
    return candidates[Math.floor(Math.random() * candidates.length)];
  }

  // ─────────────────────────────────────────────────────────────────────
  // Theme overlay element
  // ─────────────────────────────────────────────────────────────────────
  const overlay = document.createElement('div');
  overlay.className = 'mode-overlay';
  overlay.setAttribute('aria-hidden', 'true');
  document.body.appendChild(overlay);

  // ═══════════════════════════════════════════════════════════════════════
  // MATRIX
  // ═══════════════════════════════════════════════════════════════════════
  function createMatrixRain() {
    const canvas = document.createElement('canvas');
    overlay.appendChild(canvas);
    const ctx = canvas.getContext('2d', { alpha: true });

    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const charSize = window.matchMedia('(max-width: 640px)').matches ? 14 : 16;

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
        y: Math.random() * -height,
        speed: 1 + Math.random() * 2.4,
        len: 6 + Math.floor(Math.random() * 18),
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
    await activeRain.fadeAlphaTo(1, 500);
    await wait(700);
    activeRain.setSpeed(0.4);
    await activeRain.fadeAlphaTo(0.16, 600);
    overlay.classList.remove('is-visible');
    await wait(420);

    // Promote the rain to a persistent ambient layer on <body>.
    // It keeps falling, very softly, behind everything in Matrix mode —
    // the real cypherpunk atmosphere, not just a transition gimmick.
    const c = activeRain.canvas;
    document.body.appendChild(c);
    c.classList.add('matrix-rain-ambient');
    idleMatrixRain = activeRain;
    activeRain = null;

    // Inject the terminal status bar above everything else
    injectMatrixChrome();
    // Inject cyberpunk video backdrop (Pexels free) + cursor trail
    injectMatrixVideo();
    injectThemeCursor('matrix');
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

  // ───────── 3D perspective tunnel for Matrix mode (canvas, no network) ─────────
  // Renders forward-flying green wire-lines vanishing to a centre point.
  // Combined with the existing rain canvas + photo bg + scanlines, this
  // produces a video-grade cinematic backdrop with zero external assets.
  let matrixVideo = null;          // alias kept for symmetry with old API
  let matrixTunnelStop = null;
  function injectMatrixVideo() {
    removeMatrixVideo();
    const canvas = document.createElement('canvas');
    canvas.className = 'matrix-video-bg matrix-tunnel-canvas';
    canvas.setAttribute('aria-hidden', 'true');
    document.body.appendChild(canvas);
    const ctx = canvas.getContext('2d', { alpha: true });
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    let W = 0, H = 0, cx = 0, cy = 0;

    // A set of "rings" travelling outward from the vanishing point.
    // Each ring has a depth z that decreases over time; perspective
    // projects it bigger as z → 0 (closer to camera).
    const RING_COUNT = 18;
    const SPEED = 0.012;
    let rings = [];
    function reset() {
      rings = [];
      for (let i = 0; i < RING_COUNT; i++) {
        rings.push({ z: 1 - (i / RING_COUNT) });
      }
    }
    function resize() {
      W = window.innerWidth;
      H = window.innerHeight;
      canvas.width = W * dpr;
      canvas.height = H * dpr;
      canvas.style.width = W + 'px';
      canvas.style.height = H + 'px';
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.scale(dpr, dpr);
      cx = W / 2;
      cy = H * 0.55;          // slight depression — cinematic horizon
    }
    function project(z) {
      // z in (0..1]; near = small z, far = 1. Scale grows as z → 0.
      const k = 1 / Math.max(z, 0.001);
      return k;
    }
    function frame() {
      if (!running) return;
      ctx.clearRect(0, 0, W, H);

      // Radial fog gradient sits behind the wires for depth
      const fog = ctx.createRadialGradient(cx, cy, 20, cx, cy, Math.max(W, H));
      fog.addColorStop(0,    'rgba(0, 70, 30, 0.18)');
      fog.addColorStop(0.4,  'rgba(0, 40, 18, 0.12)');
      fog.addColorStop(1,    'rgba(0, 0, 0, 0)');
      ctx.fillStyle = fog;
      ctx.fillRect(0, 0, W, H);

      // 8 radial spokes vanishing to centre
      ctx.strokeStyle = 'rgba(0, 255, 65, 0.18)';
      ctx.lineWidth = 1;
      const SPOKES = 16;
      for (let s = 0; s < SPOKES; s++) {
        const a = (s / SPOKES) * Math.PI * 2;
        ctx.beginPath();
        ctx.moveTo(cx, cy);
        ctx.lineTo(cx + Math.cos(a) * W, cy + Math.sin(a) * H);
        ctx.stroke();
      }

      // Travelling rings (rectangular hoops)
      for (const r of rings) {
        r.z -= SPEED;
        if (r.z <= 0.0) r.z = 1.0;

        const scale = project(r.z);
        const halfW = (W * 0.4) * scale;
        const halfH = (H * 0.32) * scale;
        const left   = cx - halfW;
        const right  = cx + halfW;
        const top    = cy - halfH;
        const bottom = cy + halfH;

        // Distance fade: near is brighter
        const fade = Math.min(1, (1 - r.z) * 1.6);
        ctx.strokeStyle = `rgba(0, 255, 65, ${0.55 * fade})`;
        ctx.lineWidth = 1 + fade * 1.2;
        ctx.shadowBlur = 14 * fade;
        ctx.shadowColor = 'rgba(0, 255, 65, 0.45)';
        ctx.strokeRect(left, top, right - left, bottom - top);

        // 4 corner sparks for tech feel
        ctx.fillStyle = `rgba(190, 255, 210, ${0.7 * fade})`;
        const sp = 2 + fade * 2;
        ctx.fillRect(left - sp / 2,  top - sp / 2,    sp, sp);
        ctx.fillRect(right - sp / 2, top - sp / 2,    sp, sp);
        ctx.fillRect(left - sp / 2,  bottom - sp / 2, sp, sp);
        ctx.fillRect(right - sp / 2, bottom - sp / 2, sp, sp);
      }

      ctx.shadowBlur = 0;
      raf = requestAnimationFrame(frame);
    }

    let running = true;
    let raf = null;
    reset();
    resize();
    window.addEventListener('resize', resize);
    raf = requestAnimationFrame(frame);

    matrixVideo = canvas;
    matrixTunnelStop = () => {
      running = false;
      if (raf) cancelAnimationFrame(raf);
      window.removeEventListener('resize', resize);
    };
  }
  function removeMatrixVideo() {
    if (matrixTunnelStop) { matrixTunnelStop(); matrixTunnelStop = null; }
    if (matrixVideo) { matrixVideo.remove(); matrixVideo = null; }
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

  async function enterIsland() {
    const scene = createIslandScene();
    await wait(40);
    scene.classList.add('is-silhouette-on');
    await wait(550);
    scene.classList.add('is-dawning');
    await wait(1100);
    scene.classList.add('is-flashing');
    await wait(280);
    overlay.classList.remove('is-visible');
    await wait(450);
    scene.remove();
    injectIslandBirds();
    injectIslandChrome();
    injectThemeCursor('island');
  }

  async function exitIsland() {
    removeThemeCursor();
    const scene = createIslandScene();
    scene.classList.add('is-noon', 'is-silhouette-on');
    overlay.classList.add('is-visible');
    await wait(40);
    scene.classList.add('is-falling');
    await wait(700);
    scene.remove();
    removeIslandBirds();
    removeIslandChrome();
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

    if (current === 'matrix')      await exitMatrix();
    else if (current === 'island') await exitIsland();
    else if (current === 'studio') await exitStudio();
    else                            await genericExit();

    clearModeClasses();
    if (target) HTML.classList.add(`mode-${target}`);
    await wait(140);

    if (target === 'matrix')      await enterMatrix();
    else if (target === 'island') await enterIsland();
    else if (target === 'studio') await enterStudio();
    else                           await genericEnter();

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
})();
