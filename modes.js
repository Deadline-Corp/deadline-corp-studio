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

  async function enterMatrix() {
    if (!activeRain) {
      activeRain = createMatrixRain();
      activeRain.setAlpha(0);
      activeRain.start();
    }
    await activeRain.fadeAlphaTo(1, 500);
    await wait(700);
    activeRain.setSpeed(0.4);
    await activeRain.fadeAlphaTo(0.18, 600);
    overlay.classList.remove('is-visible');
    await wait(420);
    if (activeRain) { activeRain.destroy(); activeRain = null; }
  }

  async function exitMatrix() {
    if (!activeRain) {
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
  }

  async function exitIsland() {
    const scene = createIslandScene();
    scene.classList.add('is-noon', 'is-silhouette-on');
    overlay.classList.add('is-visible');
    await wait(40);
    scene.classList.add('is-falling');
    await wait(700);
    scene.remove();
    removeIslandBirds();
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

    // Install idle signatures
    injectStudioIdle();
  }

  async function exitStudio() {
    // Tear idle FIRST so spot/parallax don't flicker through the transition
    removeStudioIdle();
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
