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
