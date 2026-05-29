/**
 * Deadline Sales Bot — embeddable chat widget
 *
 * Embed on the website:
 *     <script src="https://your-domain/widget.js" defer></script>
 *
 * Or embed inline (single file). The widget self-injects styles and HTML
 * into the page. Toggle the header to open/close.
 *
 * Configure the API URL via window.DEADLINE_BOT_API before loading,
 * or it falls back to the constant below.
 */

(function () {
  "use strict";

  // ============================================================
  // CONFIG
  // ============================================================
  const API_URL =
    (typeof window !== "undefined" && window.DEADLINE_BOT_API) ||
    "https://deadline-sales-bot-production.up.railway.app/chat";

  // Session id is persisted in localStorage so the conversation survives page
  // reloads, language switches AND skin switches (those don't reload, but if
  // the user ever does F5 the bot's memory stays — same session_id reaches
  // the backend, which keeps the conversation thread intact).
  const SESSION_STORAGE_KEY = "dl-bot-session-id";
  const SESSION_ID = (function () {
    let stored = null;
    try { stored = window.localStorage.getItem(SESSION_STORAGE_KEY); } catch (_) {}
    if (stored && /^sess_/.test(stored)) return stored;
    const fresh = "sess_" + Date.now().toString(36) + "_" + Math.random().toString(36).slice(2, 8);
    try { window.localStorage.setItem(SESSION_STORAGE_KEY, fresh); } catch (_) {}
    return fresh;
  })();

  // Visible chat history is also persisted so on reload / new tab the user
  // sees their previous messages, not just an empty widget. Capped at the
  // last 100 messages and expired after 30 days of inactivity. Transient
  // error messages (network failures) are explicitly NOT stored — they'd
  // be confusing to see on next visit.
  const MESSAGES_STORAGE_KEY = "dl-bot-messages";
  const MAX_STORED_MESSAGES = 100;
  const STORAGE_TTL_MS = 30 * 24 * 60 * 60 * 1000;
  function loadStoredMessages() {
    try {
      const raw = window.localStorage.getItem(MESSAGES_STORAGE_KEY);
      if (!raw) return [];
      const data = JSON.parse(raw);
      if (data.sessionId !== SESSION_ID) return [];
      if (Date.now() - (data.savedAt || 0) > STORAGE_TTL_MS) return [];
      return Array.isArray(data.messages) ? data.messages : [];
    } catch (_) { return []; }
  }
  function saveMessage(text, role) {
    try {
      const prev = loadStoredMessages();
      prev.push({ text: text, role: role, ts: Date.now() });
      const trimmed = prev.slice(-MAX_STORED_MESSAGES);
      window.localStorage.setItem(MESSAGES_STORAGE_KEY, JSON.stringify({
        sessionId: SESSION_ID,
        savedAt: Date.now(),
        messages: trimmed,
      }));
    } catch (_) {}
  }

  // ============================================================
  // STYLES
  // ============================================================
  const styles = `
    #dl-bot, #dl-bot * { box-sizing: border-box; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
    #dl-bot {
      position: fixed; bottom: 20px; right: 20px;
      width: 360px; max-height: 540px;
      background: #1f1a10; color: #f4ecd8;
      border: 1px solid #3d3520; border-radius: 12px;
      box-shadow: 0 10px 40px rgba(0,0,0,0.5);
      display: flex; flex-direction: column;
      transform: translateY(calc(100% - 52px)); transition: transform 0.25s ease;
      z-index: 99999; overflow: hidden;
    }
    #dl-bot.open { transform: translateY(0); }
    #dl-bot-header {
      padding: 14px 16px; border-bottom: 1px solid #3d3520;
      font-size: 13px; letter-spacing: 0.05em;
      cursor: pointer; user-select: none;
      display: flex; justify-content: space-between; align-items: center;
      background: #161208;
    }
    #dl-bot-header:hover { background: #1c1810; }
    #dl-bot-msg {
      flex: 1; padding: 12px; overflow-y: auto;
      display: flex; flex-direction: column; gap: 10px;
      min-height: 320px; max-height: 380px;
    }
    .dl-msg { padding: 8px 12px; border-radius: 8px; max-width: 85%; font-size: 14px; line-height: 1.45; white-space: pre-wrap; word-wrap: break-word; }
    .dl-msg.u { background: #3d3520; align-self: flex-end; color: #f4ecd8; }
    .dl-msg.b { background: #252010; align-self: flex-start; color: #f4ecd8; }
    .dl-msg.sys { background: transparent; color: #8a7d5f; font-size: 11px; align-self: center; text-align: center; padding: 4px 8px; font-style: italic; }
    .dl-typing { display: flex; gap: 4px; padding: 8px 12px; align-self: flex-start; }
    .dl-typing span { width: 6px; height: 6px; border-radius: 50%; background: #8a7d5f; opacity: 0.4; animation: dl-blink 1.4s infinite both; }
    .dl-typing span:nth-child(2) { animation-delay: 0.2s; }
    .dl-typing span:nth-child(3) { animation-delay: 0.4s; }
    @keyframes dl-blink { 0%, 80%, 100% { opacity: 0.4; } 40% { opacity: 1; } }
    #dl-bot-wrap {
      padding: 10px; border-top: 1px solid #3d3520;
      display: flex; gap: 6px; background: #1f1a10;
    }
    #dl-inp {
      flex: 1; padding: 9px 10px; background: #252010; color: #f4ecd8;
      border: 1px solid #3d3520; border-radius: 6px; font-size: 14px; outline: none;
    }
    #dl-inp:focus { border-color: #f4ecd8; }
    #dl-btn {
      background: #f4ecd8; color: #161208; border: none;
      padding: 0 14px; border-radius: 6px; cursor: pointer; font-weight: 700;
      font-size: 14px; min-width: 40px;
    }
    #dl-btn:hover { background: #ffffff; }
    #dl-btn:disabled { opacity: 0.4; cursor: not-allowed; }
    @media (max-width: 480px) {
      #dl-bot { width: calc(100% - 20px); right: 10px; left: 10px; bottom: 10px; }
    }
  `;

  // ============================================================
  // INJECT STYLES + HTML
  // ============================================================
  const styleEl = document.createElement("style");
  styleEl.textContent = styles;
  document.head.appendChild(styleEl);

  const root = document.createElement("div");
  root.id = "dl-bot";
  root.innerHTML = `
    <div id="dl-bot-header">
      <span>DEADLINE · Опишите задачу одним сообщением</span>
      <span id="dl-bot-toggle">↕</span>
    </div>
    <div id="dl-bot-msg">
      <div class="dl-msg b">Привет. Опишите задачу одним сообщением — план и срок прилетят раньше, чем уберёте руки от клавиатуры.</div>
    </div>
    <div id="dl-bot-wrap">
      <input id="dl-inp" placeholder="Опишите задачу..." autocomplete="off" />
      <button id="dl-btn" aria-label="Send">→</button>
    </div>
  `;
  document.body.appendChild(root);

  const $header = document.getElementById("dl-bot-header");
  const $msg = document.getElementById("dl-bot-msg");
  const $inp = document.getElementById("dl-inp");
  const $btn = document.getElementById("dl-btn");

  // ============================================================
  // HELPERS
  // ============================================================
  function addMsg(text, role, opts) {
    opts = opts || {};
    const div = document.createElement("div");
    div.className = "dl-msg " + role;
    div.textContent = text;
    $msg.appendChild(div);
    $msg.scrollTop = $msg.scrollHeight;
    if (opts.persist !== false) saveMessage(text, role);
  }

  // Restore previous conversation on widget mount. Wipes the inline default
  // greeting so it isn't duplicated next to the restored history.
  (function restoreHistoryOnInit() {
    const stored = loadStoredMessages();
    if (!stored.length) return;
    $msg.innerHTML = "";
    for (const m of stored) {
      const div = document.createElement("div");
      div.className = "dl-msg " + m.role;
      div.textContent = m.text;
      $msg.appendChild(div);
    }
    $msg.scrollTop = $msg.scrollHeight;
  })();

  function showTyping() {
    const t = document.createElement("div");
    t.className = "dl-typing";
    t.id = "dl-typing-indicator";
    t.innerHTML = "<span></span><span></span><span></span>";
    $msg.appendChild(t);
    $msg.scrollTop = $msg.scrollHeight;
  }
  function hideTyping() {
    const t = document.getElementById("dl-typing-indicator");
    if (t) t.remove();
  }

  // ============================================================
  // SEND
  // ============================================================
  async function send() {
    const text = $inp.value.trim();
    if (!text) return;
    addMsg(text, "u");
    $inp.value = "";
    $btn.disabled = true;
    showTyping();

    try {
      const r = await fetch(API_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: SESSION_ID, message: text }),
      });
      hideTyping();

      if (!r.ok) {
        if (r.status === 503) {
          addMsg("Сервис временно недоступен. Напишите в Telegram @deadline_corp", "b", { persist: false });
        } else {
          addMsg("Ошибка связи. Напишите в Telegram @deadline_corp", "b", { persist: false });
        }
        return;
      }

      const data = await r.json();
      addMsg(data.answer, "b");
      if (data.handoff) {
        const isEng = /[a-zA-Z]/.test(text) && !/[а-яА-Я]/.test(text);
        const msg = isEng
          ? "📩 Passed to the team. We will email you within minutes."
          : "📩 Передал команде. Напишем на email в течение минут.";
        addMsg(msg, "sys");
      }
    } catch (e) {
      hideTyping();
      addMsg("Сбой связи. Напишите в Telegram @deadline_corp", "b", { persist: false });
      console.error("[dl-bot]", e);
    } finally {
      $btn.disabled = false;
      $inp.focus();
    }
  }

  // ============================================================
  // EVENTS
  // ============================================================
  $header.addEventListener("click", () => {
    root.classList.toggle("open");
    if (root.classList.contains("open")) {
      setTimeout(() => $inp.focus(), 260);
    }
  });

  $btn.addEventListener("click", send);

  $inp.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  });

  // ============================================================
  // EXPOSE for debugging (optional)
  // ============================================================
  window.DeadlineBot = {
    open: () => root.classList.add("open"),
    close: () => root.classList.remove("open"),
    sessionId: SESSION_ID,
  };
})();


/**
 * Deadline Sales Bot — TRAINER WIDGET (opt-in)
 *
 * Activates only when the URL contains ?admin=<TRAINING_AUTH_TOKEN>.
 * Renders a second widget on the left side of the page where an operator
 * can: paste a past conversation snippet, describe what was wrong, get a
 * proposed rule + sample response from the trainer LLM, iterate via
 * feedback, and approve the final version to be saved to the bot's memory
 * (the `training_corrections` table). Saved rules influence every future
 * bot reply through RAG retrieval.
 *
 * Backend endpoints used (see main.py /admin/training/*):
 *   POST /admin/training/draft   — start session, get first proposal
 *   POST /admin/training/refine  — iterate with operator feedback
 *   POST /admin/training/approve — persist the latest proposal
 *   POST /admin/training/discard — abandon without saving
 *   GET  /admin/training/list    — list current rules
 *
 * All require `Authorization: Bearer <token>` header.
 */
(function () {
  "use strict";

  // ----- Activation gate: only mount if #admin=<token> is in the URL hash -----
  // Use the URL fragment (hash), NOT the query string: the fragment is never
  // sent to the server, never leaks in the Referer header to third-party
  // scripts (Meta Pixel / Clarity / CDNs), and never lands in server access logs.
  const URL_PARAMS = new URLSearchParams(window.location.hash.slice(1));
  const ADMIN_TOKEN = URL_PARAMS.get("admin");
  if (!ADMIN_TOKEN) return;  // not in admin mode → don't render anything
  // Strip the token from the address bar immediately so it doesn't linger in
  // browser history or session-replay tools after first read.
  try { history.replaceState(null, "", window.location.pathname + window.location.search); } catch (e) {}

  // Detect API base URL — same host as the regular widget's /chat endpoint
  const BASE_URL = (
    (typeof window !== "undefined" && window.DEADLINE_BOT_API) ||
    "https://deadline-sales-bot-production.up.railway.app/chat"
  ).replace(/\/chat\/?$/, "");

  // Auth header for every training fetch
  const authHeaders = () => ({
    "Content-Type": "application/json",
    "Authorization": "Bearer " + ADMIN_TOKEN,
  });

  // Live session state — null until first draft, then UUID returned by backend
  let sessionId = null;

  // ============================================================
  // STYLES — visually distinct from the client widget (orange accent)
  // ============================================================
  const trainerStyles = `
    #dl-trainer {
      position: fixed;
      left: 14px;
      bottom: 14px;
      width: 380px;
      max-width: calc(100vw - 28px);
      max-height: calc(100vh - 28px);
      background: #1a0f08;
      color: #f5e6d3;
      border: 1px solid #c98a3a;
      border-radius: 10px;
      font: 14px/1.45 ui-monospace, SFMono-Regular, "Cascadia Code", Menlo, Consolas, monospace;
      z-index: 99998;
      box-shadow: 0 8px 24px rgba(0,0,0,0.55);
      display: flex;
      flex-direction: column;
      overflow: hidden;
    }
    #dl-trainer.collapsed > .dl-trainer-body { display: none; }
    .dl-trainer-header {
      padding: 10px 12px;
      background: #2a1810;
      cursor: pointer;
      display: flex;
      justify-content: space-between;
      align-items: center;
      border-bottom: 1px solid #c98a3a;
    }
    .dl-trainer-header strong { color: #ffb86b; }
    .dl-trainer-body { padding: 12px; overflow-y: auto; max-height: 60vh; }
    .dl-trainer-section { margin-bottom: 14px; }
    .dl-trainer-label { display: block; margin-bottom: 4px; color: #ffb86b; font-size: 12px; }
    .dl-trainer-textarea, .dl-trainer-input {
      width: 100%;
      background: #0d0805;
      color: #f5e6d3;
      border: 1px solid #4a3220;
      border-radius: 6px;
      padding: 8px;
      font: inherit;
      box-sizing: border-box;
      resize: vertical;
    }
    .dl-trainer-textarea { min-height: 80px; }
    .dl-trainer-btn {
      background: #c98a3a;
      color: #1a0f08;
      border: 0;
      padding: 8px 14px;
      border-radius: 6px;
      cursor: pointer;
      font: inherit;
      font-weight: 600;
      margin-right: 6px;
      margin-top: 4px;
    }
    .dl-trainer-btn:hover { background: #e0a050; }
    .dl-trainer-btn.secondary { background: #4a3220; color: #f5e6d3; }
    .dl-trainer-btn:disabled { opacity: 0.5; cursor: wait; }
    .dl-trainer-proposal {
      background: #0d0805;
      border-left: 3px solid #ffb86b;
      padding: 10px 12px;
      margin-bottom: 8px;
      border-radius: 4px;
      white-space: pre-wrap;
    }
    .dl-trainer-proposal h4 { margin: 0 0 4px; color: #ffb86b; font-size: 12px; text-transform: uppercase; }
    .dl-trainer-status {
      padding: 8px;
      background: #0d0805;
      border-radius: 4px;
      color: #9ec99c;
      margin-bottom: 8px;
    }
    .dl-trainer-error { color: #ff8a8a; }
  `;
  const trainerStyleEl = document.createElement("style");
  trainerStyleEl.textContent = trainerStyles;
  document.head.appendChild(trainerStyleEl);

  // ============================================================
  // DOM
  // ============================================================
  const tRoot = document.createElement("div");
  tRoot.id = "dl-trainer";
  tRoot.innerHTML = `
    <div class="dl-trainer-header" id="dl-trainer-header">
      <strong>🎓 TRAINER · обучение бота</strong>
      <span id="dl-trainer-toggle">▼</span>
    </div>
    <div class="dl-trainer-body" id="dl-trainer-body">
      <div class="dl-trainer-section">
        <label class="dl-trainer-label">Шаг 1 — Диалог где бот ответил не так</label>
        <textarea class="dl-trainer-textarea" id="dl-trainer-dialog" placeholder="user: сколько стоит сайт?
assistant: цена от $2000
user: дорого..."></textarea>
      </div>
      <div class="dl-trainer-section">
        <label class="dl-trainer-label">Шаг 2 — Что было не так / как надо</label>
        <textarea class="dl-trainer-textarea" id="dl-trainer-note" placeholder="Бот не должен называть конкретную сумму без Discovery. Надо было предложить разобраться с задачей и узнать email."></textarea>
      </div>
      <div class="dl-trainer-section">
        <button class="dl-trainer-btn" id="dl-trainer-draft-btn">Получить вариант</button>
        <button class="dl-trainer-btn secondary" id="dl-trainer-list-btn">📚 Список правил</button>
      </div>
      <div id="dl-trainer-proposals"></div>
    </div>
  `;
  document.body.appendChild(tRoot);

  const $tBody = document.getElementById("dl-trainer-body");
  const $tHeader = document.getElementById("dl-trainer-header");
  const $tToggle = document.getElementById("dl-trainer-toggle");
  const $tDialog = document.getElementById("dl-trainer-dialog");
  const $tNote = document.getElementById("dl-trainer-note");
  const $tDraftBtn = document.getElementById("dl-trainer-draft-btn");
  const $tListBtn = document.getElementById("dl-trainer-list-btn");
  const $tProposals = document.getElementById("dl-trainer-proposals");

  // ============================================================
  // HELPERS
  // ============================================================
  function showStatus(text, isError) {
    const el = document.createElement("div");
    el.className = "dl-trainer-status" + (isError ? " dl-trainer-error" : "");
    el.textContent = text;
    $tProposals.appendChild(el);
    $tBody.scrollTop = $tBody.scrollHeight;
  }

  function renderProposal(p) {
    const wrap = document.createElement("div");
    wrap.className = "dl-trainer-proposal";
    wrap.innerHTML = `
      <h4>Предложенное правило</h4>
      <div>${escapeHtml(p.proposed_rule || "(пусто)")}</div>
      <h4 style="margin-top:8px;">Пример ответа бота</h4>
      <div>${escapeHtml(p.proposed_response || "(пусто)")}</div>
      <h4 style="margin-top:8px;">Вопрос от тренера</h4>
      <div style="color:#ffb86b;">${escapeHtml(p.confirmation_question || "Подходит?")}</div>
      <div style="margin-top:10px;">
        <button class="dl-trainer-btn" data-act="approve">✓ Сохранить</button>
        <button class="dl-trainer-btn secondary" data-act="discard">✗ Отменить</button>
      </div>
      <div style="margin-top:8px;">
        <input type="text" class="dl-trainer-input" placeholder="...или подскажи как надо иначе" data-act="refine-input"/>
        <button class="dl-trainer-btn" data-act="refine" style="margin-top:4px;">↻ Новый вариант</button>
      </div>
    `;
    wrap.querySelector('[data-act="approve"]').addEventListener("click", () => approveSession(wrap));
    wrap.querySelector('[data-act="discard"]').addEventListener("click", () => discardSession(wrap));
    wrap.querySelector('[data-act="refine"]').addEventListener("click", () => {
      const inp = wrap.querySelector('[data-act="refine-input"]');
      refineSession(inp.value.trim());
    });
    $tProposals.appendChild(wrap);
    $tBody.scrollTop = $tBody.scrollHeight;
  }

  function escapeHtml(s) {
    if (!s) return "";
    return String(s).replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));
  }

  async function postJSON(path, body) {
    const r = await fetch(BASE_URL + path, {
      method: "POST",
      headers: authHeaders(),
      body: JSON.stringify(body),
    });
    if (!r.ok) {
      // Phase 11: parse JSON body so 409 conflict payloads can be inspected
      // by callers. Falls back to plain text for non-JSON errors.
      let parsed = null;
      let raw = "";
      try {
        raw = await r.text();
        parsed = JSON.parse(raw);
      } catch (_) { /* leave parsed=null */ }
      const err = new Error("HTTP " + r.status + ": " + (raw || "").slice(0, 200));
      err.status = r.status;
      err.body = parsed;
      throw err;
    }
    return r.json();
  }

  // ============================================================
  // ACTIONS
  // ============================================================
  async function startDraft() {
    const dialog = $tDialog.value.trim();
    const note = $tNote.value.trim();
    if (!dialog || !note) {
      showStatus("Нужно заполнить и диалог, и комментарий", true);
      return;
    }
    $tDraftBtn.disabled = true;
    $tDraftBtn.textContent = "Думаю...";
    try {
      const proposal = await postJSON("/admin/training/draft", {
        dialog, correction_note: note,
      });
      sessionId = proposal.session_id;
      // Phase 11 — surface nearby existing rules BEFORE the proposal so
      // the operator knows what they might be conflicting with.
      renderSimilarWarning(proposal.similar_existing_rules || []);
      renderProposal(proposal);
    } catch (e) {
      showStatus("Ошибка: " + e.message, true);
    } finally {
      $tDraftBtn.disabled = false;
      $tDraftBtn.textContent = "Получить вариант";
    }
  }

  // Phase 11 — render the list of pre-existing active rules with similar
  // trigger context. Goes ABOVE the proposal in the trainer panel.
  function renderSimilarWarning(rules) {
    // Remove old block if any
    const old = document.getElementById("trainer-similar");
    if (old) old.remove();
    if (!rules || !rules.length) return;
    const list = $("#trainer-proposals");
    if (!list) return;
    const wrap = document.createElement("div");
    wrap.id = "trainer-similar";
    wrap.style.cssText =
      "background:#3a2010;border:1px solid #c98a3a;border-radius:6px;" +
      "padding:8px 10px;margin-bottom:8px;color:#ffb86b;font-size:12px;";
    const header = document.createElement("strong");
    header.textContent = "⚠ " + rules.length +
      " похожих активных правил уже есть в базе. Посмотрите прежде чем сохранять:";
    wrap.appendChild(header);
    rules.forEach((r) => {
      const item = document.createElement("div");
      item.style.cssText = "margin-top:6px;padding-left:8px;border-left:2px solid #c98a3a;";
      const dist = (typeof r.distance === "number") ? r.distance.toFixed(2) : "?";
      const created = (r.created_at || "").slice(0, 10);
      item.innerHTML =
        "<small style='opacity:.7'>id " + escapeHtml(String(r.id).slice(0, 8)) +
        " · created " + escapeHtml(created) + " by " + escapeHtml(r.created_by || "?") +
        " · cosine " + dist + "</small><br>" +
        escapeHtml((r.guidance || "").slice(0, 220));
      wrap.appendChild(item);
    });
    list.prepend(wrap);
  }

  async function refineSession(feedback) {
    if (!sessionId) {
      showStatus("Сначала получите первый вариант (Шаг 1)", true);
      return;
    }
    if (!feedback) {
      showStatus("Опишите что изменить в варианте", true);
      return;
    }
    try {
      const proposal = await postJSON("/admin/training/refine", {
        session_id: sessionId, operator_feedback: feedback,
      });
      renderProposal(proposal);
    } catch (e) {
      showStatus("Ошибка: " + e.message, true);
    }
  }

  async function approveSession(blockEl, forceAction) {
    if (!sessionId) return;
    try {
      const payload = { session_id: sessionId, created_by: "admin" };
      if (forceAction) payload.force_action = forceAction;
      const result = await postJSON("/admin/training/approve", payload);
      const supSuffix = (result.superseded && result.superseded.length)
        ? " (деактивировано старых: " + result.superseded.length + ")"
        : "";
      showStatus("✓ Сохранено! ID правила: " +
        result.correction_id.slice(0, 8) + supSuffix);
      sessionId = null;
      $tDialog.value = "";
      $tNote.value = "";
      const sim = document.getElementById("trainer-similar");
      if (sim) sim.remove();
    } catch (e) {
      // Phase 11 — 409 means active rules conflict with this one.
      // Show the operator a choice instead of a generic error.
      if (e.status === 409 && e.body && Array.isArray(e.body.detail?.conflicts)) {
        showConflictModal(e.body.detail.conflicts, e.body.detail.message || "");
      } else {
        showStatus("Ошибка сохранения: " + e.message, true);
      }
    }
  }

  // Phase 11 — modal to resolve a 409-conflict from /approve.
  // Inline implementation (no external library) to stay zero-dep.
  function showConflictModal(conflicts, summary) {
    // Remove any prior modal
    const old = document.getElementById("trainer-conflict-modal");
    if (old) old.remove();

    const overlay = document.createElement("div");
    overlay.id = "trainer-conflict-modal";
    overlay.style.cssText =
      "position:fixed;inset:0;background:rgba(0,0,0,.6);" +
      "z-index:99999;display:flex;align-items:center;justify-content:center;";

    const card = document.createElement("div");
    card.style.cssText =
      "background:#1a0f08;border:1px solid #c98a3a;border-radius:8px;" +
      "max-width:560px;width:92%;max-height:85vh;overflow-y:auto;" +
      "padding:16px;color:#f0d9b5;font-family:system-ui,sans-serif;";

    const title = document.createElement("h3");
    title.style.cssText = "margin:0 0 8px;color:#ffb86b;font-size:15px;";
    title.textContent = "⚠ Конфликт с " + conflicts.length + " активными правилами";

    const subtitle = document.createElement("div");
    subtitle.style.cssText = "font-size:12px;opacity:.8;margin-bottom:10px;";
    subtitle.textContent = summary;

    card.appendChild(title);
    card.appendChild(subtitle);

    conflicts.forEach((c, i) => {
      const item = document.createElement("div");
      item.style.cssText =
        "background:#2a1808;border-left:3px solid #c98a3a;padding:8px 10px;" +
        "margin-bottom:8px;font-size:12px;";
      const created = (c.created_at || "").slice(0, 10);
      item.innerHTML =
        "<strong>" + (i + 1) + ". " + escapeHtml(String(c.id).slice(0, 8)) +
        "</strong>  <small style='opacity:.7'>" +
        escapeHtml(created) + " · cosine " +
        (typeof c.distance === "number" ? c.distance.toFixed(2) : "?") +
        "</small><br><em style='opacity:.85;display:block;margin-top:4px;'>" +
        escapeHtml((c.guidance || "").slice(0, 280)) + "</em>" +
        "<div style='margin-top:4px;color:#ffb86b;'>Судья: " +
        escapeHtml(c.judge_reason || "") + "</div>" +
        "<div style='font-size:11px;opacity:.7'>предложено: " +
        escapeHtml(c.suggested_action || "supersede") + "</div>";
      card.appendChild(item);
    });

    const btnRow = document.createElement("div");
    btnRow.style.cssText = "display:flex;gap:8px;margin-top:12px;flex-wrap:wrap;";

    function mkBtn(label, color, handler) {
      const b = document.createElement("button");
      b.textContent = label;
      b.style.cssText =
        "background:" + color + ";color:#1a0f08;border:none;border-radius:4px;" +
        "padding:8px 12px;font-weight:600;cursor:pointer;font-size:13px;";
      b.onclick = handler;
      return b;
    }

    btnRow.appendChild(mkBtn("Заменить старые (supersede)", "#ffb86b", () => {
      overlay.remove();
      approveSession(null, "supersede");
    }));
    btnRow.appendChild(mkBtn("Оставить оба (coexist)", "#c98a3a", () => {
      overlay.remove();
      approveSession(null, "coexist");
    }));
    btnRow.appendChild(mkBtn("Отмена", "#5a3a20", () => {
      overlay.remove();
    }));

    card.appendChild(btnRow);
    overlay.appendChild(card);
    document.body.appendChild(overlay);
  }

  async function discardSession(blockEl) {
    if (sessionId) {
      try {
        await postJSON("/admin/training/discard", { session_id: sessionId });
      } catch (_) { /* not fatal */ }
    }
    sessionId = null;
    showStatus("Отменено. Можно начать новый разбор.");
  }

  async function listRules() {
    try {
      const r = await fetch(BASE_URL + "/admin/training/list?limit=20", { headers: authHeaders() });
      if (!r.ok) throw new Error("HTTP " + r.status);
      const data = await r.json();
      showStatus("Активных правил: " + data.count);
      data.rules.slice(0, 10).forEach((rule) => {
        const el = document.createElement("div");
        el.className = "dl-trainer-proposal";
        el.innerHTML = `
          <h4>Правило · ${escapeHtml(rule.channel || "все каналы")}</h4>
          <div>${escapeHtml(rule.guidance)}</div>
          ${rule.suggested_response ? `<h4 style="margin-top:6px;">Пример</h4><div>${escapeHtml(rule.suggested_response)}</div>` : ""}
          <div style="margin-top:4px; color:#888; font-size:11px;">${escapeHtml(rule.created_at || "")}</div>
        `;
        $tProposals.appendChild(el);
      });
      $tBody.scrollTop = $tBody.scrollHeight;
    } catch (e) {
      showStatus("Ошибка загрузки правил: " + e.message, true);
    }
  }

  // ============================================================
  // EVENTS
  // ============================================================
  $tHeader.addEventListener("click", () => {
    tRoot.classList.toggle("collapsed");
    $tToggle.textContent = tRoot.classList.contains("collapsed") ? "▲" : "▼";
  });
  $tDraftBtn.addEventListener("click", startDraft);
  $tListBtn.addEventListener("click", listRules);

  // Expose for debugging ONLY on localhost — never in production, where a
  // compromised/third-party script could call listRules() with the operator's
  // bearer token via this global.
  if (location.hostname === "localhost" || location.hostname === "127.0.0.1") {
    window.DeadlineTrainer = {
      sessionId: () => sessionId,
      listRules,
    };
  }
})();
