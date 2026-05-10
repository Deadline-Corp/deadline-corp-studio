/* ═══════════════════════════════════════════════════════════════════════
   MAGIC MODE TOGGLE — orchestrator
   Phase 1: button click stub. Theme rendering arrives in later phases.
   ═══════════════════════════════════════════════════════════════════════ */
(() => {
  'use strict';

  const MODES = ['matrix', 'island', 'studio'];
  const TRANSITION_LOCK_CLASS = 'is-transitioning';
  const HTML = document.documentElement;

  const btn = document.getElementById('magic-toggle');
  if (!btn) return;

  /** Pick a random mode that isn't the currently-active one. */
  function pickNextMode() {
    const current = getCurrentMode();
    const candidates = MODES.filter(m => m !== current);
    return candidates[Math.floor(Math.random() * candidates.length)];
  }

  function getCurrentMode() {
    for (const m of MODES) if (HTML.classList.contains(`mode-${m}`)) return m;
    return null; // default site
  }

  /** Stub: phases 2-4 will replace this with real theatre. */
  async function transitionTo(mode) {
    if (HTML.classList.contains(TRANSITION_LOCK_CLASS)) return;
    HTML.classList.add(TRANSITION_LOCK_CLASS);

    // 1. Charge-up burst on the button
    btn.classList.remove('is-arriving', 'is-hidden');
    btn.classList.add('is-charging');
    await wait(220);

    // 2. Hide button during theatre
    btn.classList.remove('is-charging');
    btn.classList.add('is-hidden');

    // 3. (placeholder) — theatrical transition out + in goes here
    //    For now, just swap the mode class with a short fade.
    await wait(400);
    MODES.forEach(m => HTML.classList.remove(`mode-${m}`));
    if (mode) HTML.classList.add(`mode-${mode}`);
    await wait(200);

    // 4. Bring the button back with a welcome bounce
    btn.classList.remove('is-hidden');
    // Force reflow so the next animation re-triggers
    void btn.offsetWidth;
    btn.classList.add('is-arriving');

    // 5. Settle: drop the lock once the bounce finishes
    await wait(720);
    btn.classList.remove('is-arriving');
    HTML.classList.remove(TRANSITION_LOCK_CLASS);
  }

  function wait(ms) {
    return new Promise(r => setTimeout(r, ms));
  }

  btn.addEventListener('click', (e) => {
    e.preventDefault();
    if (HTML.classList.contains(TRANSITION_LOCK_CLASS)) return;
    const next = pickNextMode();
    transitionTo(next);
  });

  // Expose for debugging in DevTools
  window.__magicToggle = { pickNextMode, transitionTo, getCurrentMode, MODES };
})();
