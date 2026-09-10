// recsbot-ui chat app — full redesign (Tier 1 + 2 + 3).

const state = {
  conversationId: null,
  tab: "live",
  inFlight: null,
  pendingAssistantEl: null,
  pendingAssistantBuf: "",
  thinkingEl: null,
  stickyBottom: true,
  lastUserMessage: null,
  // Accumulates sources from any web_search tool calls in the current assistant
  // turn. Cleared on every new user send + after each message_stop. Rendered as
  // an expandable Sources panel on the assistant bubble.
  pendingSources: [],
};

// ---------- DOM refs ----------
const $ = (id) => document.getElementById(id);
const els = {
  sidebar: $("sidebar"),
  sidebarOverlay: $("sidebarOverlay"),
  mobileMenuBtn: $("mobileMenuBtn"),
  convList: $("convList"),
  messages: $("messages"),
  emptyState: $("emptyState"),
  composer: $("composer"),
  composerInput: $("composerInput"),
  sendBtn: $("sendBtn"),
  stopBtn: $("stopBtn"),
  jumpToBottom: $("jumpToBottom"),
  newChatBtn: $("newChatBtn"),
  chatTitle: $("chatTitle"),
  chatTitleInput: $("chatTitleInput"),
  renameBtn: $("renameBtn"),
  deleteBtn: $("deleteBtn"),
  userBadge: $("userBadge"),
  logoutBtn: $("logoutBtn"),
  tabs: document.querySelectorAll(".tab"),
  settingsBtn: $("settingsBtn"),
  settingsModal: $("settingsModal"),
  closeSettingsBtn: $("closeSettingsBtn"),
  saveSettingsBtn: $("saveSettingsBtn"),
  saveResult: $("saveResult"),
  steamKeyInput: $("steamKeyInput"),
  steamIdInput: $("steamIdInput"),
  testSteamBtn: $("testSteamBtn"),
  steamResult: $("steamResult"),
  jellyUrlInput: $("jellyUrlInput"),
  jellyKeyInput: $("jellyKeyInput"),
  jellyUserInput: $("jellyUserInput"),
  testJellyBtn: $("testJellyBtn"),
  jellyResult: $("jellyResult"),
  refreshTasteBtn: $("refreshTasteBtn"),
  tasteResult: $("tasteResult"),
  themeChips: document.querySelectorAll(".theme-chip"),
  confirmModal: $("confirmModal"),
  confirmMsg: $("confirmMsg"),
  confirmCancelBtn: $("confirmCancelBtn"),
  confirmOkBtn: $("confirmOkBtn"),
  shortcutsModal: $("shortcutsModal"),
  closeShortcutsBtn: $("closeShortcutsBtn"),
  toastRegion: $("toastRegion"),
  liveRegion: $("liveRegion"),
};

// Screen-reader announcer. Replace prior text so AT speaks the *latest*
// summary rather than queueing per-frame updates.
function announce(text) {
  if (!els.liveRegion) return;
  els.liveRegion.textContent = "";
  // Defer so the live-region change is detected even when the same string
  // is announced twice in a row.
  setTimeout(() => { els.liveRegion.textContent = text; }, 30);
}

// ---------- init ----------
document.addEventListener("DOMContentLoaded", init);

async function init() {
  applyStoredTheme();
  els.composerInput.addEventListener("input", () => autosizeTextarea(els.composerInput));
  els.composerInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      els.composer.requestSubmit();
    }
  });
  els.composer.addEventListener("submit", onSend);
  els.stopBtn.addEventListener("click", stopGeneration);
  els.newChatBtn.addEventListener("click", startNewChat);
  els.renameBtn.addEventListener("click", () => beginRename());
  els.chatTitle.addEventListener("click", () => beginRename());
  els.chatTitle.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); beginRename(); } });
  els.chatTitleInput.addEventListener("blur", commitRename);
  els.chatTitleInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); commitRename(); }
    else if (e.key === "Escape") { e.preventDefault(); cancelRename(); }
  });
  els.deleteBtn.addEventListener("click", deleteCurrent);
  els.tabs.forEach(t => t.addEventListener("click", () => switchTab(t.dataset.tab)));
  els.settingsBtn.addEventListener("click", openSettings);
  els.closeSettingsBtn.addEventListener("click", closeSettings);
  els.saveSettingsBtn.addEventListener("click", saveSettings);
  els.testSteamBtn.addEventListener("click", () => runTest("steam"));
  els.testJellyBtn.addEventListener("click", () => runTest("jellyfin"));
  els.refreshTasteBtn.addEventListener("click", refreshTaste);
  els.themeChips.forEach(c => c.addEventListener("click", () => setTheme(c.dataset.theme)));
  els.confirmCancelBtn.addEventListener("click", () => closeModal(els.confirmModal));
  els.closeShortcutsBtn.addEventListener("click", () => closeModal(els.shortcutsModal));
  els.logoutBtn.addEventListener("click", doLogout);
  els.mobileMenuBtn.addEventListener("click", toggleSidebar);
  els.sidebarOverlay.addEventListener("click", toggleSidebar);
  els.jumpToBottom.addEventListener("click", () => { state.stickyBottom = true; scrollToBottom(); els.jumpToBottom.hidden = true; });

  // close-modal behaviors (backdrop click + Esc)
  [els.settingsModal, els.confirmModal, els.shortcutsModal].forEach(modal => {
    modal.addEventListener("click", (e) => { if (e.target === modal) closeModal(modal); });
  });

  document.addEventListener("keydown", onGlobalKey);

  // sticky-scroll observer. We distinguish *user* scrolls (the user explicitly
  // scrolled to read history) from *programmatic* ones (we scrolled to bottom on
  // a new token). Only user scrolls update stickyBottom — otherwise auto-scrolling
  // toggles itself back on every frame and the user can't read history mid-stream.
  let suppressScrollListener = false;
  state._scrollSuppress = (fn) => {
    suppressScrollListener = true;
    fn();
    // Allow the resulting scroll event to fire, then clear the flag on the next tick.
    requestAnimationFrame(() => requestAnimationFrame(() => { suppressScrollListener = false; }));
  };
  els.messages.addEventListener("scroll", () => {
    if (suppressScrollListener) return;
    const distFromBottom = els.messages.scrollHeight - els.messages.scrollTop - els.messages.clientHeight;
    state.stickyBottom = distFromBottom < 60;
    if (state.stickyBottom) els.jumpToBottom.hidden = true;
  });

  renderEmpty();

  const me = await fetch("/api/me").then(r => r.json()).catch(() => null);
  if (me && me.username) els.userBadge.textContent = me.username;

  await refreshConversations();
}

function onGlobalKey(e) {
  // Esc closes any open modal (also commits rename)
  if (e.key === "Escape") {
    if (!els.chatTitleInput.hidden) { cancelRename(); return; }
    [els.shortcutsModal, els.confirmModal, els.settingsModal].forEach(m => { if (!m.hidden) closeModal(m); });
    if (els.sidebar.classList.contains("open")) toggleSidebar();
    return;
  }
  // "/" alone focuses the composer (Slack/Discord style) — only when nothing else is focused
  if (e.key === "/" && !e.metaKey && !e.ctrlKey && !e.altKey) {
    const tag = (e.target && e.target.tagName) || "";
    if (tag !== "INPUT" && tag !== "TEXTAREA") {
      e.preventDefault();
      els.composerInput.focus();
      return;
    }
  }
  // ⌘/Ctrl+Shift+K = focus composer; ⌘/Ctrl+Shift+N = new chat;
  // ⌘/Ctrl+/ = shortcut sheet. We use Shift to avoid colliding with the browser's
  // native ⌘N (new window) and ⌘K (address bar / find).
  const isMeta = e.metaKey || e.ctrlKey;
  if (!isMeta) return;
  if (e.shiftKey && (e.key === "K" || e.key === "k")) { e.preventDefault(); els.composerInput.focus(); }
  else if (e.shiftKey && (e.key === "N" || e.key === "n")) { e.preventDefault(); startNewChat(); }
  else if (e.key === "/") { e.preventDefault(); openModal(els.shortcutsModal); }
}

// ---------- conversations sidebar ----------

async function refreshConversations() {
  const trash = state.tab === "trash" ? "true" : "false";
  const r = await fetch(`/api/conversations?trash=${trash}&limit=100`);
  if (!r.ok) return;
  const d = await r.json();
  renderConvList(d.items || []);
}

function relativeTime(iso) {
  if (!iso) return "";
  const then = new Date(iso);
  const diff = (Date.now() - then.getTime()) / 1000;
  if (diff < 60) return "ora";
  if (diff < 3600) return `${Math.floor(diff/60)}m fa`;
  if (diff < 86400) return `${Math.floor(diff/3600)}h fa`;
  if (diff < 86400 * 7) return `${Math.floor(diff/86400)}g fa`;
  return then.toLocaleDateString("it-IT", { day: "numeric", month: "short" });
}

function dayBucket(iso) {
  if (!iso) return "Più vecchie";
  const then = new Date(iso);
  const now = new Date();
  const startToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  const t = then.getTime();
  if (t >= startToday) return "Oggi";
  if (t >= startToday - 86400000) return "Ieri";
  if (t >= startToday - 7 * 86400000) return "Questa settimana";
  if (t >= startToday - 30 * 86400000) return "Questo mese";
  return "Più vecchie";
}

function renderConvList(items) {
  if (!items.length) {
    els.convList.innerHTML = '<div class="conv-empty">Nessuna conversazione.</div>';
    return;
  }
  // Group by bucket; preserve list order (already sorted desc on server).
  const buckets = new Map();
  for (const it of items) {
    const key = dayBucket(it.updated_at);
    if (!buckets.has(key)) buckets.set(key, []);
    buckets.get(key).push(it);
  }
  let html = "";
  for (const [bucket, rows] of buckets) {
    html += `<div class="conv-group-label">${escapeHTML(bucket)}</div>`;
    for (const it of rows) {
      const active = state.conversationId === it.id ? " active" : "";
      const meta = `${relativeTime(it.updated_at)} · ${it.message_count || 0} msg`;
      html += `<div class="conv-item${active}" data-cid="${it.id}" tabindex="0" role="button" aria-label="${escapeHTML(it.title || "Senza titolo")}">
        <div class="conv-item-title">${escapeHTML(it.title || "(senza titolo)")}</div>
        <div class="conv-item-meta">${escapeHTML(meta)}</div>
      </div>`;
    }
  }
  els.convList.innerHTML = html;
  els.convList.querySelectorAll(".conv-item").forEach(n => {
    n.addEventListener("click", () => loadConversation(n.dataset.cid));
    n.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); loadConversation(n.dataset.cid); } });
  });
}

function switchTab(tab) {
  state.tab = tab;
  els.tabs.forEach(t => {
    const isActive = t.dataset.tab === tab;
    t.classList.toggle("active", isActive);
    t.setAttribute("aria-selected", String(isActive));
  });
  refreshConversations();
}

async function loadConversation(cid) {
  state.conversationId = cid;
  document.querySelectorAll(".conv-item").forEach(n => n.classList.toggle("active", n.dataset.cid === cid));
  const r = await fetch(`/api/conversations/${cid}`);
  if (!r.ok) { renderEmpty(); return; }
  const d = await r.json();
  els.chatTitle.textContent = d.title || "(senza titolo)";
  els.messages.innerHTML = "";
  state.lastUserMessage = null;
  for (const m of d.messages || []) renderStoredMessage(m);
  state.stickyBottom = true;
  scrollToBottom();
  if (window.innerWidth <= 760 && els.sidebar.classList.contains("open")) toggleSidebar();
}

// ---------- empty state ----------

function renderEmpty() {
  state.lastUserMessage = null;
  const quick = [
    { emoji: "🎮", label: "Cosa giochiamo stasera?", hint: "Dalla tua libreria Steam", text: "Cosa potrei giocare stasera per ~2h?" },
    { emoji: "🎵", label: "Cosa ascolto?", hint: "Dalla tua libreria Jellyfin", text: "Suggeriscimi qualcosa da ascoltare adesso." },
    { emoji: "🆕", label: "Novità del mese", hint: "Cerca sul web", text: "Cosa è uscito di interessante questo mese?" },
    { emoji: "🤔", label: "Sorprendimi", hint: "Mood discovery", text: "Sorprendimi: consigliami qualcosa che potrebbe piacermi." },
  ];
  els.messages.innerHTML = `
    <div class="empty-state" id="emptyState">
      <div class="empty-eyebrow">${escapeHTML(emptyEyebrow())}</div>
      <h2 class="empty-title display">Cosa ti <em>va</em> stasera?</h2>
      <p class="empty-hint">Scegli un punto di partenza qui sotto, oppure scrivi qualsiasi cosa nella casella in basso.</p>
      <div class="quick-actions">
        ${quick.map((q, i) => `
          <button class="quick-action" data-i="${i}">
            <span class="quick-action-emoji">${q.emoji}</span>
            <span class="quick-action-label">${q.label}</span>
            <span class="quick-action-hint">${q.hint}</span>
          </button>`).join("")}
      </div>
    </div>`;
  els.messages.querySelectorAll(".quick-action").forEach(btn => {
    btn.addEventListener("click", (e) => {
      const q = quick[+btn.dataset.i];
      els.composerInput.value = q.text;
      autosizeTextarea(els.composerInput);
      els.composerInput.focus();
      // Shift-click (or alt-click) just fills the input for editing — no auto-send.
      // Plain click sends immediately, matching the default expectation.
      if (e.shiftKey || e.altKey) {
        // place caret at end so the user can append
        const v = els.composerInput.value;
        els.composerInput.setSelectionRange(v.length, v.length);
        return;
      }
      els.composer.requestSubmit();
    });
  });
}

// ---------- rendering messages ----------

function renderStoredMessage(m) {
  if (m.role === "user") {
    const text = (m.content || []).map(b => b.text || "").join("");
    appendUserMessage(text);
    state.lastUserMessage = text;
  } else if (m.role === "assistant") {
    // Sources are persisted as a {"sources":[...]} block on the assistant
    // message. Pull them out up-front and prime state.pendingSources so
    // appendAssistantBubble attaches the panel just like a live SSE turn.
    const storedSources = (m.content || [])
      .filter(b => b && Array.isArray(b.sources))
      .flatMap(b => b.sources);
    if (storedSources.length) state.pendingSources = storedSources.slice();
    // Same shape for follow-ups, written by the chat handler when
    // suggest_followups fires. Newer messages have this block; older ones
    // still embed the tag in the text and extractFollowups handles them.
    const storedFollowups = (m.content || [])
      .filter(b => b && Array.isArray(b.followups))
      .flatMap(b => b.followups);
    if (storedFollowups.length) state._bufferedFollowups = storedFollowups.slice();

    let buf = "";
    for (const b of m.content || []) {
      if (b.text) {
        buf += b.text;
      } else if (b.toolUse) {
        if (buf) {
          const { clean, items } = extractFollowups(buf);
          const c = appendAssistantBubble(clean);
          if (items.length) renderFollowups(c.closest(".msg"), items);
          buf = "";
        }
        appendToolPill(b.toolUse.name, b.toolUse.input, "ok");
      }
    }
    if (buf) {
      const { clean, items } = extractFollowups(buf);
      const c = appendAssistantBubble(clean);
      const bubbleMsg = c.closest(".msg");
      if (items.length) renderFollowups(bubbleMsg, items);
      // appendAssistantBubble already attached sources from pendingSources;
      // now rewrite [N] markers in the prose.
      const liCount = bubbleMsg?.querySelectorAll(".src-list li").length || 0;
      if (liCount) linkifyCitationsIn(bubbleMsg, liCount);
    }
    state.pendingSources = [];
    // Restore prior feedback if persisted on the message.
    if (m.feedback === "up" || m.feedback === "down") {
      const lastBubble = els.messages.querySelector(".msg.assistant:last-of-type");
      if (lastBubble) {
        const target = lastBubble.querySelector(m.feedback === "up" ? ".fb-up" : ".fb-down");
        if (target) target.setAttribute("aria-pressed", "true");
      }
    }
  }
}

function emptyEyebrow() {
  // Time-of-day flavour for the eyebrow above the empty-state headline.
  // Keeps things alive without rotating the headline itself, which the user
  // is supposed to anchor on.
  const h = new Date().getHours();
  if (h < 6)  return "È tardi 🌙";
  if (h < 12) return "Buongiorno ☀️";
  if (h < 18) return "Pomeriggio ✨";
  if (h < 22) return "Sera 🌆";
  return "Notte 🌙";
}

function userInitial() {
  // Derive the user-avatar glyph from the logged-in username so it doesn't
  // collide with the assistant 'A'. Special-case 'admin' (the default
  // single-tenant account) → 'M' for Matteo, the human behind the box,
  // since 'A' would clash with the assistant avatar.
  const u = (els.userBadge?.textContent || "").trim();
  if (!u) return "M";
  if (u.toLowerCase() === "admin") return "M";
  return u[0].toUpperCase();
}

function appendUserMessage(text) {
  const el = document.createElement("div");
  el.className = "msg user";
  el.innerHTML = `
    <div class="msg-bubble">
      <div class="msg-content"></div>
    </div>
    <div class="avatar avatar-user" aria-hidden="true">${escapeHTML(userInitial())}</div>`;
  el.querySelector(".msg-content").textContent = text;
  els.messages.appendChild(el);
  if (state.stickyBottom) scrollToBottom();
  return el;
}

function markUserMessageFailed(userEl, text) {
  if (!userEl || userEl.querySelector(".msg-retry")) return;
  userEl.classList.add("failed");
  const retry = document.createElement("button");
  retry.type = "button";
  retry.className = "msg-retry";
  retry.innerHTML = '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 0 1 15-6.7L21 8"/><path d="M21 3v5h-5"/></svg> Riprova';
  retry.title = "Invia di nuovo questo messaggio";
  retry.addEventListener("click", () => {
    userEl.remove();
    els.composerInput.value = text;
    autosizeTextarea(els.composerInput);
    els.composer.requestSubmit();
  });
  (userEl.querySelector(".msg-bubble") || userEl).appendChild(retry);
}

function appendAssistantBubble(rawText) {
  const wrap = document.createElement("div");
  wrap.className = "msg assistant";
  wrap.setAttribute("lang", document.documentElement.lang || "it");
  wrap.innerHTML = `
    <div class="avatar avatar-assistant" aria-hidden="true">
      <span class="avatar-glyph">A</span>
    </div>
    <div class="msg-bubble">
      <div class="msg-role">recsbot</div>
      <div class="msg-content markdown-body"></div>
      <div class="msg-sources" hidden></div>
      <div class="msg-followups" hidden></div>
      <div class="msg-actions">
        <button class="msg-action-btn feedback-btn fb-up" title="Risposta utile" aria-pressed="false">
          <svg class="msg-action-icon" viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 9V5a3 3 0 0 0-3-3l-4 9v11h11.28a2 2 0 0 0 2-1.7l1.38-9a2 2 0 0 0-2-2.3zM7 22H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3"/></svg>
        </button>
        <button class="msg-action-btn feedback-btn fb-down" title="Risposta non utile" aria-pressed="false">
          <svg class="msg-action-icon" viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10 15v4a3 3 0 0 0 3 3l4-9V2H5.72a2 2 0 0 0-2 1.7l-1.38 9a2 2 0 0 0 2 2.3zM17 2h3a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2h-3"/></svg>
        </button>
        <span class="msg-action-divider" aria-hidden="true"></span>
        <button class="msg-action-btn copy-btn" title="Copia">
          <svg class="msg-action-icon" viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
          <span class="msg-action-label">Copia</span>
        </button>
        <button class="msg-action-btn regen-btn" title="Rigenera la risposta">
          <svg class="msg-action-icon" viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 0 1 15-6.7L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-15 6.7L3 16"/><path d="M3 21v-5h5"/></svg>
          <span class="msg-action-label">Rigenera</span>
        </button>
      </div>
    </div>`;
  const content = wrap.querySelector(".msg-content");
  content.dataset.raw = rawText;
  content.innerHTML = renderMarkdown(rawText);
  wrap.querySelector(".copy-btn").addEventListener("click", (e) => copyText(content.dataset.raw, e.currentTarget));
  wrap.querySelector(".regen-btn").addEventListener("click", () => regenerateLast());
  wrap.querySelector(".fb-up").addEventListener("click", (e) => sendFeedback(wrap, "up", e.currentTarget));
  wrap.querySelector(".fb-down").addEventListener("click", (e) => sendFeedback(wrap, "down", e.currentTarget));
  els.messages.appendChild(wrap);
  // If sources arrived from web_search calls before the assistant bubble,
  // attach them now.
  if (state.pendingSources.length) {
    renderSources(wrap, state.pendingSources);
  }
  // Same for followups: the suggest_followups tool may fire before any
  // text_delta lands (rare but legal — tool-only turns).
  if (state._bufferedFollowups && state._bufferedFollowups.length) {
    renderFollowups(wrap, state._bufferedFollowups);
    state._bufferedFollowups = null;
  }
  if (state.stickyBottom) scrollToBottom();
  return content;
}

async function sendFeedback(bubbleWrap, value, clickedBtn) {
  if (!state.conversationId) return;
  // Toggle: clicking the already-pressed button clears the vote.
  const pressed = clickedBtn.getAttribute("aria-pressed") === "true";
  const sendValue = pressed ? "" : value;
  // Optimistic UI update — if the request fails we revert.
  const upBtn = bubbleWrap.querySelector(".fb-up");
  const downBtn = bubbleWrap.querySelector(".fb-down");
  const prevUp = upBtn.getAttribute("aria-pressed");
  const prevDown = downBtn.getAttribute("aria-pressed");
  upBtn.setAttribute("aria-pressed", String(sendValue === "up"));
  downBtn.setAttribute("aria-pressed", String(sendValue === "down"));
  try {
    const r = await fetch(`/api/conversations/${state.conversationId}/feedback`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ value: sendValue }),
    });
    if (!r.ok) throw new Error("HTTP " + r.status);
  } catch (e) {
    upBtn.setAttribute("aria-pressed", prevUp);
    downBtn.setAttribute("aria-pressed", prevDown);
    toast("Feedback non salvato.", "error");
  }
}

// Pluck `<followups>a|b|c</followups>` out of the response text. Returns
// {clean, items}. The tag is always at the very end per system prompt; if a
// model fluffs that we still locate it. Tag is stripped so users never see it.
const FOLLOWUPS_RE = /\s*<followups>([^<]*)<\/followups>\s*$/i;
function extractFollowups(raw) {
  const m = raw.match(FOLLOWUPS_RE);
  if (!m) return { clean: raw, items: [] };
  const items = m[1].split("|").map(s => s.trim()).filter(Boolean).slice(0, 4);
  return { clean: raw.replace(FOLLOWUPS_RE, ""), items };
}

function renderFollowups(bubbleEl, items) {
  const slot = bubbleEl.querySelector(".msg-followups");
  if (!slot) return;
  if (!items.length) { slot.hidden = true; return; }
  slot.innerHTML = items.map(t =>
    `<button type="button" class="followup-chip">${escapeHTML(t)}</button>`
  ).join("");
  slot.hidden = false;
  slot.querySelectorAll(".followup-chip").forEach(b => {
    b.addEventListener("click", () => {
      els.composerInput.value = b.textContent;
      autosizeTextarea(els.composerInput);
      els.composer.requestSubmit();
    });
  });
}

// Monotonic id for source-list anchors; lets [N] markers in the prose link
// to the right <li> even when several assistant bubbles coexist on the page.
let _sourceListSeq = 0;

function renderSources(bubbleEl, sources) {
  const slot = bubbleEl.querySelector(".msg-sources");
  if (!slot) return;
  const seen = new Set();
  const unique = sources.filter(s => {
    if (!s || !s.url || seen.has(s.url)) return false;
    seen.add(s.url);
    return true;
  });
  if (!unique.length) { slot.hidden = true; return; }
  const listKey = `srcs-${++_sourceListSeq}`;
  bubbleEl.dataset.srcKey = listKey;
  const items = unique.map((s, i) => {
    const host = (() => { try { return new URL(s.url).host.replace(/^www\./, ""); } catch { return ""; } })();
    return `<li id="${listKey}-${i + 1}">
      <a class="src-link" href="${escapeHTML(s.url)}" target="_blank" rel="noopener noreferrer">
        <span class="src-num">${i + 1}</span>
        <span class="src-title">${escapeHTML(s.title || s.url)}</span>
        <span class="src-host">${escapeHTML(host)}</span>
      </a>
    </li>`;
  }).join("");
  slot.innerHTML = `
    <button class="src-toggle" type="button" aria-expanded="false">
      <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>
      Fonti (${unique.length})
      <svg class="src-chevron" viewBox="0 0 24 24" width="11" height="11" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><polyline points="6 9 12 15 18 9"/></svg>
    </button>
    <ul class="src-list" hidden>${items}</ul>`;
  slot.hidden = false;
  const toggle = slot.querySelector(".src-toggle");
  const list = slot.querySelector(".src-list");
  toggle.addEventListener("click", () => {
    const expanded = toggle.getAttribute("aria-expanded") === "true";
    toggle.setAttribute("aria-expanded", String(!expanded));
    list.hidden = expanded;
  });
  // Re-link any pre-rendered citation markers in this bubble — needed when
  // sources arrive AFTER the assistant text was already painted (live SSE
  // path: tool_use_end can fire after some text_delta frames).
  linkifyCitationsIn(bubbleEl, unique.length);
}

// Transform [1], [2], … markers in already-rendered prose into superscript
// links that scroll to the matching <li> in this bubble's Sources panel.
// Skipped if the bubble has no sources (the marker would be a dead anchor)
// or if the number is out of range.
function linkifyCitationsIn(bubbleEl, sourceCount) {
  if (!bubbleEl || !sourceCount) return;
  const listKey = bubbleEl.dataset.srcKey;
  if (!listKey) return;
  const content = bubbleEl.querySelector(".msg-content");
  if (!content) return;
  // Walk text nodes only — avoids molesting attributes / existing anchors.
  const walker = document.createTreeWalker(content, NodeFilter.SHOW_TEXT);
  const targets = [];
  let n;
  while ((n = walker.nextNode())) {
    if (/\[\d{1,2}\]/.test(n.nodeValue)) targets.push(n);
  }
  for (const node of targets) {
    const frag = document.createDocumentFragment();
    let last = 0;
    const re = /\[(\d{1,2})\]/g;
    let m;
    const text = node.nodeValue;
    while ((m = re.exec(text)) !== null) {
      const num = parseInt(m[1], 10);
      if (num < 1 || num > sourceCount) continue;   // out-of-range → leave as-is
      if (m.index > last) frag.appendChild(document.createTextNode(text.slice(last, m.index)));
      const a = document.createElement("a");
      a.href = `#${listKey}-${num}`;
      a.className = "citation";
      a.textContent = String(num);
      a.setAttribute("aria-label", `Fonte ${num}`);
      a.addEventListener("click", (e) => {
        e.preventDefault();
        const target = document.getElementById(`${listKey}-${num}`);
        if (!target) return;
        // Auto-expand the panel so the click actually lands on something.
        const slot = bubbleEl.querySelector(".msg-sources");
        const toggle = slot?.querySelector(".src-toggle");
        const list = slot?.querySelector(".src-list");
        if (toggle?.getAttribute("aria-expanded") !== "true") {
          toggle?.setAttribute("aria-expanded", "true");
          if (list) list.hidden = false;
        }
        target.scrollIntoView({ behavior: "smooth", block: "nearest" });
        target.classList.add("src-flash");
        setTimeout(() => target.classList.remove("src-flash"), 1400);
      });
      const sup = document.createElement("sup");
      sup.appendChild(a);
      frag.appendChild(sup);
      last = m.index + m[0].length;
    }
    if (last < text.length) frag.appendChild(document.createTextNode(text.slice(last)));
    if (frag.childNodes.length) node.parentNode.replaceChild(frag, node);
  }
}

function appendThinking() {
  // Defer by 250ms — if the first token arrives before then (Bedrock often
  // first-bytes within 100-200ms), we never show the indicator and the user
  // doesn't see it flash on/off uselessly.
  removeThinking();
  state._thinkingTimer = setTimeout(() => {
    state._thinkingTimer = null;
    const el = document.createElement("div");
    el.className = "msg assistant";
    el.innerHTML = `
      <div class="avatar avatar-assistant" aria-hidden="true"><span class="avatar-glyph">A</span></div>
      <div class="msg-bubble">
        <div class="msg-role">recsbot</div>
        <div class="thinking">Sto pensando<span class="thinking-dots"><span></span><span></span><span></span></span></div>
      </div>`;
    els.messages.appendChild(el);
    state.thinkingEl = el;
    if (state.stickyBottom) scrollToBottom();
  }, 250);
}

function removeThinking() {
  if (state._thinkingTimer) {
    clearTimeout(state._thinkingTimer);
    state._thinkingTimer = null;
  }
  if (state.thinkingEl) { state.thinkingEl.remove(); state.thinkingEl = null; }
}

function appendToolPill(name, input, status) {
  removeThinking();
  const el = document.createElement("div");
  el.className = "msg assistant msg-tool";
  const label = humanizeTool(name, input);
  const pillCls = status === "error" ? "tool-event error" : "tool-event running";
  const rawJson = JSON.stringify(input || {}, null, 2);
  el.innerHTML = `
    <div class="avatar avatar-tool" aria-hidden="true">
      <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/></svg>
    </div>
    <div class="msg-bubble">
      <button class="${pillCls}" type="button" aria-expanded="false">
        <span class="tool-event-label">${label}</span>
        <svg class="tool-chevron" viewBox="0 0 24 24" width="11" height="11" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><polyline points="6 9 12 15 18 9"/></svg>
      </button>
      <pre class="tool-event-raw" hidden>${escapeHTML(`${name}(${rawJson})`)}</pre>
    </div>`;
  const btn = el.querySelector(".tool-event");
  const raw = el.querySelector(".tool-event-raw");
  btn.addEventListener("click", () => {
    const exp = btn.getAttribute("aria-expanded") === "true";
    btn.setAttribute("aria-expanded", String(!exp));
    raw.hidden = exp;
  });
  els.messages.appendChild(el);
  if (state.stickyBottom) scrollToBottom();
}

function humanizeTool(name, input) {
  const i = input || {};
  switch (name) {
    case "search_owned_library": {
      const lib = i.library === "steam" ? "Steam" : i.library === "jellyfin" ? "Jellyfin" : "le tue librerie";
      const q = i.query || "qualcosa";
      return `🔍 Cerco "${escapeHTML(q)}" in ${lib}`;
    }
    case "web_search":
      return `🌐 Cerco sul web: "${escapeHTML(i.query || "")}"`;
    case "get_user_preferences":
      return `🧠 Controllo le tue preferenze`;
    case "record_user_preference": {
      const map = { add_blocklist: "🚫 Aggiungo alla blocklist", add_favorite_genre: "❤️ Salvo come genere preferito", set_recent_mood: "🎭 Aggiorno il tuo mood" };
      return `${map[i.action] || "💾 Salvo preferenza"}: ${escapeHTML(i.value || "")}`;
    }
    default:
      return `🔧 ${escapeHTML(name)}`;
  }
}

// rAF-batched markdown render: tokens arrive 50-100/s during streaming; we don't
// need to re-parse on every one. requestAnimationFrame caps it at ~60 paints/s and
// preserves text selection (innerHTML rewrites would otherwise wipe it).
let _flushScheduled = false;
function scheduleMarkdownFlush() {
  if (_flushScheduled) return;
  _flushScheduled = true;
  requestAnimationFrame(() => {
    _flushScheduled = false;
    if (!state.pendingAssistantEl) return;
    // Strip an in-progress <followups>... tag so the user never sees the raw
    // markup mid-stream. We also strip a half-typed '<followups>' prefix.
    const buf = state.pendingAssistantBuf;
    const idx = buf.lastIndexOf("<followups>");
    const visible = idx >= 0 ? buf.slice(0, idx).replace(/\s+$/, "") : buf;
    state.pendingAssistantEl.innerHTML = renderMarkdown(visible);
  });
}

function scrollToBottom() {
  // Wrap the scroll mutation so the scroll listener doesn't interpret it as user input
  if (state._scrollSuppress) {
    state._scrollSuppress(() => { els.messages.scrollTop = els.messages.scrollHeight; });
  } else {
    els.messages.scrollTop = els.messages.scrollHeight;
  }
}

function maybeShowJumpPill() {
  const distFromBottom = els.messages.scrollHeight - els.messages.scrollTop - els.messages.clientHeight;
  els.jumpToBottom.hidden = distFromBottom < 60;
}

// ---------- send / stop / regenerate ----------

function startNewChat() {
  if (state.inFlight) state.inFlight.abort();
  state.conversationId = null;
  state.pendingAssistantEl = null;
  state.pendingAssistantBuf = "";
  state.lastUserMessage = null;
  els.chatTitle.textContent = "Nuova conversazione";
  renderEmpty();
  document.querySelectorAll(".conv-item").forEach(n => n.classList.remove("active"));
  els.composerInput.focus();
  if (window.innerWidth <= 760 && els.sidebar.classList.contains("open")) toggleSidebar();
}

async function onSend(e) {
  e.preventDefault();
  if (state.inFlight) return;
  const text = els.composerInput.value.trim();
  if (!text) return;
  els.composerInput.value = "";
  autosizeTextarea(els.composerInput);
  setSending(true);
  // clear empty-state if visible
  const empty = els.messages.querySelector(".empty-state");
  if (empty) empty.remove();
  const userEl = appendUserMessage(text);
  state.lastUserMessage = text;
  state.pendingAssistantEl = null;
  state.pendingAssistantBuf = "";
  state.pendingSources = [];
  appendThinking();

  const ok = await streamChat({ message: text, conversation_id: state.conversationId });
  // If the send failed entirely (no assistant bubble was rendered), mark the user
  // bubble as failed so the user can retry.
  if (!ok && userEl && !state.pendingAssistantEl && !els.messages.querySelector(".msg.assistant")) {
    markUserMessageFailed(userEl, text);
  }
}

async function streamChat(payload) {
  const ctrl = new AbortController();
  state.inFlight = ctrl;
  let success = true;
  try {
    const resp = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      signal: ctrl.signal,
    });
    if (!resp.ok) throw new Error("HTTP " + resp.status);
    await consumeSSE(resp.body.getReader());
  } catch (err) {
    success = false;
    removeThinking();
    if (err.name !== "AbortError") {
      toast("Errore: " + err.message, "error");
    }
  } finally {
    state.inFlight = null;
    setSending(false);
    refreshConversations();
  }
  return success;
}

function setSending(on) {
  els.sendBtn.hidden = on;
  els.stopBtn.hidden = !on;
  els.composerInput.disabled = false;
}

function stopGeneration() {
  if (state.inFlight) state.inFlight.abort();
  removeThinking();
  toast("Generazione interrotta.", "info");
}

async function regenerateLast() {
  if (state.inFlight) return;
  if (!state.conversationId) { toast("Nessuna conversazione attiva.", "error"); return; }
  // Visually peel off the last assistant turn (and any tool pills attached to it),
  // back to the previous user message. Backend prunes Dynamo on its own; this just
  // keeps the DOM in sync so the new reply doesn't appear stacked under the old one.
  const kids = Array.from(els.messages.children);
  for (let i = kids.length - 1; i >= 0; i--) {
    const m = kids[i];
    if (m.classList && m.classList.contains("user")) break;
    m.remove();
  }
  setSending(true);
  state.pendingAssistantEl = null;
  state.pendingAssistantBuf = "";
  state.pendingSources = [];
  appendThinking();
  await streamChat({ message: "", conversation_id: state.conversationId, regenerate: true });
}

async function consumeSSE(reader) {
  const dec = new TextDecoder();
  let buf = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let idx;
    while ((idx = buf.indexOf("\n\n")) >= 0) {
      const frame = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      const line = frame.split("\n").find(l => l.startsWith("data: "));
      if (!line) continue;
      let evt;
      try { evt = JSON.parse(line.slice(6)); } catch { continue; }
      handleSSE(evt);
    }
  }
}

function handleSSE(evt) {
  switch (evt.type) {
    case "conversation_created":
      state.conversationId = evt.conversation_id;
      els.chatTitle.textContent = evt.title || "Nuova conversazione";
      break;
    case "text_delta":
      if (!state.pendingAssistantEl) {
        removeThinking();
        state.pendingAssistantEl = appendAssistantBubble("");
        state.pendingAssistantBuf = "";
      }
      state.pendingAssistantBuf += evt.text;
      state.pendingAssistantEl.dataset.raw = state.pendingAssistantBuf;
      // Rate-limit the markdown re-render to one per animation frame. For long
      // responses this drops parse cost from O(n²) to O(n) and prevents the
      // visible stutter where the bubble briefly empties before re-painting.
      scheduleMarkdownFlush();
      if (state.stickyBottom) scrollToBottom();
      else maybeShowJumpPill();
      break;
    case "tool_use_start":
      // a tool starts — drop any thinking indicator + flush pending bubble
      state.pendingAssistantEl = null;
      state.pendingAssistantBuf = "";
      appendToolPill(evt.name, evt.input, "ok");
      announce(`Strumento: ${humanizeTool(evt.name, evt.input).replace(/<[^>]+>/g, "")}`);
      break;
    case "followups":
      // suggest_followups tool fired — render chips on the current assistant
      // bubble. May arrive before or after message_stop in practice (the
      // model can call the tool, then end_turn). Buffer if no bubble yet.
      {
        const items = Array.isArray(evt.prompts) ? evt.prompts : [];
        if (!items.length) break;
        const bubbleEl = state.pendingAssistantEl?.closest(".msg")
          ?? els.messages.querySelector(".msg.assistant:last-of-type");
        if (bubbleEl) renderFollowups(bubbleEl, items);
        else state._bufferedFollowups = items;
      }
      break;
    case "tool_use_end":
      // Drop the running shimmer + flip to error style if needed. Find the LAST
      // running pill (most-recent one) — if multiple tools ran in parallel we
      // drop them in arrival order.
      {
        const running = els.messages.querySelectorAll(".tool-event.running");
        const target = running[running.length - 1];
        if (target) {
          target.classList.remove("running");
          if (evt.status !== "ok") {
            target.classList.add("error");
            target.title = evt.summary || "";
          } else if (evt.summary) {
            target.title = evt.summary;
          }
        }
      }
      // Collect any sources for the upcoming assistant turn. If the bubble
      // already exists (model emitted text before the tool finished — rare
      // but possible), attach immediately.
      if (Array.isArray(evt.sources) && evt.sources.length) {
        state.pendingSources.push(...evt.sources);
        if (state.pendingAssistantEl) {
          renderSources(state.pendingAssistantEl.closest(".msg"), state.pendingSources);
        }
      }
      break;
    case "message_stop": {
      // Final pass: strip <followups>...</followups>, render any chips,
      // canonicalize the rendered text + dataset.raw so copy/regen use the
      // clean text. The model is instructed to emit the tag at the end.
      if (state.pendingAssistantEl) {
        const { clean, items } = extractFollowups(state.pendingAssistantBuf);
        state.pendingAssistantEl.dataset.raw = clean;
        state.pendingAssistantEl.innerHTML = renderMarkdown(clean);
        const bubbleMsg = state.pendingAssistantEl.closest(".msg");
        if (bubbleMsg) {
          renderFollowups(bubbleMsg, items);
          // Source panel may have been rendered before all the citation
          // markers arrived in the streamed text. Re-walk the prose now
          // that everything is settled.
          const slot = bubbleMsg.querySelector(".msg-sources");
          const liCount = slot?.querySelectorAll(".src-list li").length || 0;
          if (liCount) linkifyCitationsIn(bubbleMsg, liCount);
        }
      }
      state.pendingAssistantEl = null;
      state.pendingAssistantBuf = "";
      state.pendingSources = [];
      removeThinking();
      announce("Risposta completata.");
      break;
    }
    case "error":
      removeThinking();
      toast("Errore: " + (evt.message || evt.code || "?"), "error");
      announce("Errore nella risposta.");
      break;
  }
}

// ---------- rename / delete ----------

function beginRename() {
  if (!state.conversationId) return;
  els.chatTitleInput.value = els.chatTitle.textContent;
  els.chatTitle.hidden = true;
  els.chatTitleInput.hidden = false;
  els.chatTitleInput.focus();
  els.chatTitleInput.select();
}

function cancelRename() {
  els.chatTitleInput.hidden = true;
  els.chatTitle.hidden = false;
}

async function commitRename() {
  if (els.chatTitleInput.hidden) return;
  const newTitle = els.chatTitleInput.value.trim();
  els.chatTitleInput.hidden = true;
  els.chatTitle.hidden = false;
  if (!newTitle || newTitle === els.chatTitle.textContent) return;
  els.chatTitle.textContent = newTitle;
  await fetch(`/api/conversations/${state.conversationId}`, {
    method: "PATCH", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title: newTitle }),
  });
  refreshConversations();
}

async function deleteCurrent() {
  if (!state.conversationId) return;
  const ok = await confirmDialog({
    title: "Sposta nel cestino?",
    message: "Potrai ripristinare la conversazione dal Cestino. Vuoi continuare?",
    okLabel: "Sposta",
  });
  if (!ok) return;
  await fetch(`/api/conversations/${state.conversationId}`, {
    method: "PATCH", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ deleted: true }),
  });
  toast("Conversazione spostata nel cestino.", "success");
  startNewChat();
  refreshConversations();
}

// ---------- confirm modal ----------

function confirmDialog({ title = "Conferma", message = "Sei sicuro?", okLabel = "Conferma" }) {
  return new Promise((resolve) => {
    document.querySelector("#confirmTitle").textContent = title;
    els.confirmMsg.textContent = message;
    els.confirmOkBtn.textContent = okLabel;
    openModal(els.confirmModal);
    const cleanup = (val) => {
      els.confirmOkBtn.removeEventListener("click", onOk);
      els.confirmCancelBtn.removeEventListener("click", onCancel);
      closeModal(els.confirmModal);
      resolve(val);
    };
    const onOk = () => cleanup(true);
    const onCancel = () => cleanup(false);
    els.confirmOkBtn.addEventListener("click", onOk);
    els.confirmCancelBtn.addEventListener("click", onCancel);
  });
}

// ---------- modal helpers + focus trap ----------

let lastFocused = null;
function openModal(modal) {
  lastFocused = document.activeElement;
  modal.hidden = false;
  const focusables = modal.querySelectorAll("button, [href], input, select, textarea, [tabindex]:not([tabindex='-1'])");
  if (focusables.length) focusables[0].focus();
  modal._trapHandler = (e) => {
    if (e.key !== "Tab") return;
    const list = Array.from(modal.querySelectorAll("button, [href], input, select, textarea, [tabindex]:not([tabindex='-1'])")).filter(el => !el.disabled && el.offsetParent !== null);
    if (!list.length) return;
    const first = list[0]; const last = list[list.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  };
  modal.addEventListener("keydown", modal._trapHandler);
}
function closeModal(modal) {
  modal.hidden = true;
  if (modal._trapHandler) { modal.removeEventListener("keydown", modal._trapHandler); modal._trapHandler = null; }
  if (lastFocused && document.contains(lastFocused)) lastFocused.focus();
}

// ---------- copy ----------

function copyText(text, btn) {
  navigator.clipboard?.writeText(text).then(() => {
    if (!btn) return;
    btn.classList.add("copied");
    // Replace only the label span so the SVG stays put — prevents width-shift.
    const label = btn.querySelector(".msg-action-label");
    const orig = label ? label.textContent : null;
    if (label) label.textContent = "Copiato";
    setTimeout(() => {
      btn.classList.remove("copied");
      if (label && orig != null) label.textContent = orig;
    }, 1400);
  });
}

// ---------- toast ----------

function toast(msg, kind = "info") {
  const t = document.createElement("div");
  t.className = `toast ${kind}`;
  const text = document.createElement("span");
  text.className = "toast-text";
  text.textContent = msg;
  const close = document.createElement("button");
  close.className = "toast-close";
  close.type = "button";
  close.setAttribute("aria-label", "Chiudi notifica");
  close.textContent = "×";
  let dismissed = false;
  const dismiss = () => {
    if (dismissed) return;
    dismissed = true;
    t.style.transition = "opacity .25s, transform .25s";
    t.style.opacity = "0";
    t.style.transform = "translateX(20px)";
    setTimeout(() => t.remove(), 280);
  };
  close.addEventListener("click", dismiss);
  t.append(text, close);
  els.toastRegion.appendChild(t);
  // Errors stay until dismissed; info/success auto-dismiss after 3.5s.
  if (kind !== "error") setTimeout(dismiss, 3500);
}

// ---------- theme ----------

function applyStoredTheme() {
  const stored = localStorage.getItem("recsbot-theme") || "dark";
  setTheme(stored, /* persist */ false);
}

function setTheme(theme, persist = true) {
  if (!["dark", "light", "auto"].includes(theme)) theme = "dark";
  document.documentElement.dataset.theme = theme;
  els.themeChips.forEach(c => c.classList.toggle("active", c.dataset.theme === theme));
  if (persist) localStorage.setItem("recsbot-theme", theme);
}

// ---------- mobile sidebar ----------

function toggleSidebar() {
  const open = !els.sidebar.classList.contains("open");
  els.sidebar.classList.toggle("open", open);
  els.sidebarOverlay.hidden = !open;
}

// ---------- logout ----------

async function doLogout() {
  await fetch("/api/logout", { method: "POST" });
  window.location.href = "/login";
}

// ---------- settings ----------

async function openSettings() {
  openModal(els.settingsModal);
  els.saveResult.textContent = "Caricamento...";
  els.saveResult.className = "settings-result";
  els.steamResult.textContent = "";
  els.jellyResult.textContent = "";
  els.tasteResult.textContent = "";
  try {
    const r = await fetch("/api/settings");
    if (!r.ok) throw new Error("HTTP " + r.status);
    const d = await r.json();
    els.steamKeyInput.value = "";
    els.steamKeyInput.placeholder = d.STEAM_API_KEY?.set ? `(impostata · ${d.STEAM_API_KEY.length} caratteri — lascia vuoto per non cambiare)` : "(non impostata)";
    els.steamIdInput.value = d.STEAM_ID?.value || "";
    els.jellyUrlInput.value = d.JELLYFIN_BASE_URL?.value || "http://127.0.0.1:8096";
    els.jellyKeyInput.value = "";
    els.jellyKeyInput.placeholder = d.JELLYFIN_API_KEY?.set ? `(impostata · ${d.JELLYFIN_API_KEY.length} caratteri — lascia vuoto per non cambiare)` : "(non impostata)";
    els.jellyUserInput.value = d.JELLYFIN_USER_ID?.value || "";
    els.saveResult.textContent = "";
    // sync theme chip selection
    const theme = localStorage.getItem("recsbot-theme") || "dark";
    els.themeChips.forEach(c => c.classList.toggle("active", c.dataset.theme === theme));
  } catch (e) {
    els.saveResult.textContent = "Errore: " + e.message;
    els.saveResult.className = "settings-result err";
  }
}

function closeSettings() { closeModal(els.settingsModal); }

async function saveSettings() {
  els.saveSettingsBtn.disabled = true;
  els.saveResult.textContent = "Salvataggio...";
  els.saveResult.className = "settings-result";
  const body = {
    STEAM_API_KEY: els.steamKeyInput.value,
    STEAM_ID: els.steamIdInput.value.trim(),
    JELLYFIN_BASE_URL: els.jellyUrlInput.value.trim(),
    JELLYFIN_API_KEY: els.jellyKeyInput.value,
    JELLYFIN_USER_ID: els.jellyUserInput.value.trim(),
  };
  try {
    const r = await fetch("/api/settings", { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const d = await r.json();
    if (!r.ok || d.error) throw new Error(d.error || "HTTP " + r.status);
    els.saveResult.textContent = d.persisted ? "✓ Salvato (sync entro 1 min)" : "✓ Salvato (persist su disco fallito)";
    els.saveResult.className = "settings-result " + (d.persisted ? "ok" : "err");
    setTimeout(openSettings, 600);
  } catch (e) {
    els.saveResult.textContent = "Errore: " + e.message;
    els.saveResult.className = "settings-result err";
  } finally {
    els.saveSettingsBtn.disabled = false;
  }
}

async function runTest(provider) {
  const resEl = provider === "steam" ? els.steamResult : els.jellyResult;
  const btn = provider === "steam" ? els.testSteamBtn : els.testJellyBtn;
  btn.disabled = true;
  resEl.textContent = "Provo...";
  resEl.className = "settings-result";
  try {
    const r = await fetch(`/api/settings/test/${provider}`, { method: "POST" });
    const d = await r.json();
    resEl.textContent = (d.ok ? "✓ " : "✗ ") + (d.message || "");
    resEl.className = "settings-result " + (d.ok ? "ok" : "err");
  } catch (e) {
    resEl.textContent = "Errore: " + e.message;
    resEl.className = "settings-result err";
  } finally {
    btn.disabled = false;
  }
}

async function refreshTaste() {
  els.refreshTasteBtn.disabled = true;
  els.tasteResult.textContent = "Ricalcolo...";
  els.tasteResult.className = "settings-result";
  try {
    const r = await fetch("/api/taste-profile?refresh=1");
    const d = await r.json();
    const steamN = (d.steam?.owned_summary?.total) ?? 0;
    const jelN = (d.jellyfin?.recent_watched?.length) ?? 0;
    const arts = (d.jellyfin?.top_artists?.length) ?? 0;
    els.tasteResult.textContent = `✓ Steam: ${steamN} games · Jellyfin: ${jelN} recent · ${arts} top artists`;
    els.tasteResult.className = "settings-result ok";
  } catch (e) {
    els.tasteResult.textContent = "Errore: " + e.message;
    els.tasteResult.className = "settings-result err";
  } finally {
    els.refreshTasteBtn.disabled = false;
  }
}
