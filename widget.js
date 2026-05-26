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

  const SESSION_ID =
    "sess_" + Date.now().toString(36) + "_" + Math.random().toString(36).slice(2, 8);

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
  function addMsg(text, role) {
    const div = document.createElement("div");
    div.className = "dl-msg " + role;
    div.textContent = text;
    $msg.appendChild(div);
    $msg.scrollTop = $msg.scrollHeight;
  }

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
          addMsg("Сервис временно недоступен. Напишите в Telegram @deadline_corp", "b");
        } else {
          addMsg("Ошибка связи. Напишите в Telegram @deadline_corp", "b");
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
      addMsg("Сбой связи. Напишите в Telegram @deadline_corp", "b");
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

  // ----- Activation gate: only mount if ?admin=<token> is in URL -----
  const URL_PARAMS = new URLSearchParams(window.location.search);
  const ADMIN_TOKEN = URL_PARAMS.get("admin");
  if (!ADMIN_TOKEN) return;  // not in admin mode → don't render anything

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
      const text = await r.text().catch(() => "");
      throw new Error("HTTP " + r.status + ": " + text.slice(0, 200));
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
      renderProposal(proposal);
    } catch (e) {
      showStatus("Ошибка: " + e.message, true);
    } finally {
      $tDraftBtn.disabled = false;
      $tDraftBtn.textContent = "Получить вариант";
    }
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

  async function approveSession(blockEl) {
    if (!sessionId) return;
    try {
      const result = await postJSON("/admin/training/approve", {
        session_id: sessionId, created_by: "admin",
      });
      showStatus("✓ Сохранено! ID правила: " + result.correction_id.slice(0, 8));
      sessionId = null;
      $tDialog.value = "";
      $tNote.value = "";
    } catch (e) {
      showStatus("Ошибка сохранения: " + e.message, true);
    }
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

  // Expose for debugging
  window.DeadlineTrainer = {
    sessionId: () => sessionId,
    listRules,
  };
})();
