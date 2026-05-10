/* ═══════════════════════════════════════════════════════════════════════
   MAGIC MODE TOGGLE — orchestrator
     Phase 1: button + transition lock + class swap
     Phase 2: Matrix theatre + idle signatures
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
  const raf  = () => new Promise(r => requestAnimationFrame(r));

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
  // Theme overlay element — full-screen scrim for theatrical transitions
  // ─────────────────────────────────────────────────────────────────────
  const overlay = document.createElement('div');
  overlay.className = 'mode-overlay';
  overlay.setAttribute('aria-hidden', 'true');
  document.body.appendChild(overlay);

  function showOverlay()  { overlay.classList.add('is-visible');   return wait(420); }
  function hideOverlay()  { overlay.classList.remove('is-visible'); return wait(420); }

  // ─────────────────────────────────────────────────────────────────────
  // MatrixRain — a self-contained canvas effect with intensity control
  // ─────────────────────────────────────────────────────────────────────
  function createMatrixRain() {
    const canvas = document.createElement('canvas');
    overlay.appendChild(canvas);
    const ctx = canvas.getContext('2d', { alpha: true });

    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const charSize = window.matchMedia('(max-width: 640px)').matches ? 14 : 16;

    // Mixed glyph pool — katakana classics + digits + symbols + DEADLINE letters
    const charPool =
      '01アイウエオカキクケコサシスセソタチツテトナニヌネノハヒフヘホマミムメモヤユヨラリルレロワヲン' +
      '<>{}/\\=*+-|.?:!ABCDEFGHIJKLMNOPRSTUVXYZ0123456789ДЕАДЛАЙН'.split('').join('');
    const chars = charPool.split('');

    const easterWord = 'DEADLINE';
    let easterColumn = -1;
    let easterIndex  = 0;
    let easterCooldown = 0;

    let drops = [];
    let columns = 0;
    let width = 0, height = 0;

    let alpha   = 0;       // 0..1 — visibility multiplier
    let speedMul = 1;      // global vertical speed multiplier
    let running = false;
    let frameId = null;

    function resize() {
      width  = window.innerWidth;
      height = window.innerHeight;
      canvas.width  = Math.floor(width  * dpr);
      canvas.height = Math.floor(height * dpr);
      canvas.style.width  = width  + 'px';
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
      // Trail-fade: paint a translucent black over the whole canvas
      ctx.fillStyle = 'rgba(0, 0, 0, 0.085)';
      ctx.fillRect(0, 0, width, height);
      ctx.font = `${charSize - 2}px 'JetBrains Mono', ui-monospace, monospace`;
      ctx.textBaseline = 'top';

      for (let i = 0; i < columns; i++) {
        const drop = drops[i];
        const x = i * charSize;

        // Pick a glyph — but if this is the easter egg column, occasionally inject
        // a letter from "DEADLINE" so it reads vertically through the rain.
        let ch;
        if (i === easterColumn && easterCooldown <= 0 && Math.random() < 0.12) {
          ch = easterWord[easterIndex];
          easterIndex = (easterIndex + 1) % easterWord.length;
          if (easterIndex === 0) easterCooldown = 280; // pause between full words
        } else {
          ch = chars[(Math.random() * chars.length) | 0];
        }

        // Head — bright (white-tinted lime)
        ctx.fillStyle = `rgba(190, 255, 210, ${alpha})`;
        ctx.fillText(ch, x, drop.y);
        // Tail position — dim green
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
      destroy() {
        this.stop();
        canvas.remove();
      },
      setAlpha(a)    { alpha = Math.max(0, Math.min(1, a)); },
      getAlpha()     { return alpha; },
      setSpeed(s)    { speedMul = s; },
      // Smoothly tween alpha over duration ms
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

  // ─────────────────────────────────────────────────────────────────────
  // Matrix theatre — entry & exit
  // ─────────────────────────────────────────────────────────────────────
  let activeRain = null;

  async function enterMatrix() {
    // Overlay is already visible (black). Spin up rain on top of black bg.
    if (!activeRain) {
      activeRain = createMatrixRain();
      activeRain.setAlpha(0);
      activeRain.start();
    }
    // 1. Rain fades in from 0 → 1 over 500ms
    await activeRain.fadeAlphaTo(1, 500);
    // 2. Hold full intensity briefly so the rain reads
    await wait(700);
    // 3. Rain dims to taste (so the page underneath becomes visible)
    activeRain.setSpeed(0.4);
    await activeRain.fadeAlphaTo(0.18, 600);
    // 4. The page (now in matrix mode) is revealed by hiding the black overlay
    overlay.classList.remove('is-visible');
    await wait(420);
    // 5. Stop the rain entirely and clean up
    if (activeRain) {
      activeRain.destroy();
      activeRain = null;
    }
  }

  async function exitMatrix() {
    // Spin up rain (overlay still mostly invisible — show overlay simultaneously)
    if (!activeRain) {
      activeRain = createMatrixRain();
      activeRain.setAlpha(0);
      activeRain.start();
    }
    overlay.classList.add('is-visible');
    // 1. Rain materialises while overlay fills with black
    await activeRain.fadeAlphaTo(1, 500);
    // 2. Drops accelerate downward — drain
    activeRain.setSpeed(3.2);
    await wait(800);
    // 3. Rain washes out into solid black
    await activeRain.fadeAlphaTo(0, 500);
    activeRain.destroy();
    activeRain = null;
    // overlay stays solid black — caller may keep it for the next phase
  }

  // ─────────────────────────────────────────────────────────────────────
  // Generic transitions for modes whose own theatre isn't implemented yet
  // (Island & Studio in Phase 2 — they fall back to a clean black wash)
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

    // Charge-up burst on the button
    btn.classList.remove('is-arriving', 'is-hidden');
    btn.classList.add('is-charging');
    await wait(220);
    btn.classList.remove('is-charging');
    btn.classList.add('is-hidden');

    const current = getCurrentMode();

    // Exit current mode
    if (current === 'matrix') {
      await exitMatrix();
    } else {
      await genericExit();
    }

    // Swap class — page is hidden under solid black overlay right now
    clearModeClasses();
    if (target) HTML.classList.add(`mode-${target}`);
    // tiny breathing room so the new theme paints before we start revealing
    await wait(140);

    // Enter target mode
    if (target === 'matrix') {
      await enterMatrix();
    } else {
      await genericEnter();
    }

    // Reveal button with welcome bounce
    btn.classList.remove('is-hidden');
    void btn.offsetWidth; // force reflow so the animation re-triggers
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
    // 45–110 sec random spacing
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

  // Expose for debugging
  window.__magicToggle = {
    pickNextMode, transitionTo, getCurrentMode, MODES,
    forceMode: (m) => transitionTo(m),
  };
})();
