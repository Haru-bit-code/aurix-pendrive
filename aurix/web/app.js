const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const thread = $("#thread"), input = $("#input"), sendBtn = $("#send");
const state = { views: new Map(), current: null, agents: {}, agentList: [], models: [], settings: null,
                images: [], lastPending: 0, taskView: null,
                filePath: ".", viewerPath: null };

const TOOL_VERB = {
  calculator: "calculator", get_time: "get_time", list_files: "list_files", read_file: "read_file",
  write_file: "write_file", web_search: "web_search", fetch_url: "fetch_url", run_python: "run_python",
  run_shell: "run_shell", open_application: "open_application",
};
const GATE_TEXT = {
  sensitive: "This changes something on your computer.",
  dangerous: "This runs code or commands with your user's full access. Read it before running.",
};

/* ---------- helpers ---------- */
function esc(s) { return String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }
function el(tag, cls, html) { const e = document.createElement(tag); if (cls) e.className = cls; if (html != null) e.innerHTML = html; return e; }
async function api(path, opts = {}) {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  if (!res.ok) { let d = res.statusText; try { d = (await res.json()).detail || d; } catch {} throw new Error(d); }
  return res.json();
}
function fmtSize(n) { return n == null ? "" : n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1048576).toFixed(1)} MB`; }
function shortModel(m) { return (m || "").replace(/\.gguf$/i, ""); }

/* ---------- syntax highlighting (small, offline) ---------- */
const KW = "def|class|return|if|elif|else|for|while|in|import|from|as|with|try|except|finally|raise|lambda|yield|async|await|pass|break|continue|and|or|not|is|None|True|False|self|const|let|var|function|new|this|export|default|extends|interface|type|public|private|protected|static|void|null|undefined|true|false|switch|case|of|typeof|package|func|fn|struct|impl|pub|use|mut|echo|then|fi|do|done|SELECT|FROM|WHERE|JOIN|INSERT|UPDATE|DELETE|CREATE|TABLE|INTO|VALUES|GROUP|ORDER|BY";
const HASH_LANGS = new Set(["", "python", "py", "bash", "sh", "shell", "zsh", "yaml", "yml", "toml", "r", "ruby", "dockerfile", "makefile"]);
function highlight(code, lang) {
  const hash = HASH_LANGS.has(lang.toLowerCase());
  const re = new RegExp(`(${hash ? "#[^\\n]*|" : ""}\\/\\/[^\\n]*|\\/\\*[\\s\\S]*?\\*\\/|--[^\\n]*)|("""[\\s\\S]*?"""|'''[\\s\\S]*?'''|"(?:\\\\.|[^"\\\\\\n])*"|'(?:\\\\.|[^'\\\\\\n])*'|\`(?:\\\\.|[^\`\\\\])*\`)|(\\b\\d+(?:\\.\\d+)?\\b)|\\b(${KW})\\b`, "g");
  let out = "", last = 0, m;
  const sqlish = /^(sql|lua|haskell)$/i.test(lang);
  while ((m = re.exec(code))) {
    if (m[1] && m[1].startsWith("--") && !sqlish) continue;
    out += esc(code.slice(last, m.index));
    const cls = m[1] ? "tok-c" : m[2] ? "tok-s" : m[3] ? "tok-n" : "tok-k";
    out += `<span class="${cls}">${esc(m[0])}</span>`;
    last = m.index + m[0].length;
  }
  return out + esc(code.slice(last));
}

/* ---------- Markdown ---------- */
function inline(s) {
  return esc(s)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>")
    .replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
}
function markdown(src) {
  const out = [], lines = src.split("\n");
  let i = 0, list = null, para = [];
  const flushPara = () => { if (para.length) { out.push(`<p>${inline(para.join("\n")).replace(/\n/g, "<br>")}</p>`); para = []; } };
  const flushList = () => { if (list) { out.push(`<${list.tag}>${list.items.map((x) => `<li>${inline(x)}</li>`).join("")}</${list.tag}>`); list = null; } };
  while (i < lines.length) {
    const line = lines[i];
    const fence = line.match(/^\s*```\s*([\w+#.-]*)/);
    if (fence) {
      flushPara(); flushList();
      const lang = fence[1] || "", code = []; i++;
      while (i < lines.length && !/^\s*```/.test(lines[i])) code.push(lines[i++]);
      out.push(`<div class="code"><div class="code-head"><span>${esc(lang || "text")}</span><button type="button" class="copy">Copy</button></div><pre><code>${highlight(code.join("\n"), lang)}</code></pre></div>`);
      i++; continue;
    }
    const h = line.match(/^(#{1,3})\s+(.*)/), q = line.match(/^>\s?(.*)/);
    const ul = line.match(/^\s*[-*]\s+(.*)/), ol = line.match(/^\s*\d+[.)]\s+(.*)/);
    if (h) { flushPara(); flushList(); out.push(`<h${h[1].length}>${inline(h[2])}</h${h[1].length}>`); }
    else if (q) { flushPara(); flushList(); out.push(`<blockquote>${inline(q[1])}</blockquote>`); }
    else if (ul || ol) {
      flushPara();
      const tag = ul ? "ul" : "ol";
      if (list && list.tag !== tag) flushList();
      if (!list) list = { tag, items: [] };
      list.items.push((ul || ol)[1]);
    } else if (!line.trim()) { flushPara(); flushList(); }
    else { flushList(); para.push(line); }
    i++;
  }
  flushPara(); flushList();
  return out.join("");
}
thread.addEventListener("click", async (e) => {
  const b = e.target.closest(".copy"); if (!b) return;
  await navigator.clipboard.writeText(b.closest(".code").querySelector("code").innerText);
  b.textContent = "Copied"; setTimeout(() => (b.textContent = "Copy"), 1200);
});

/* ---------- panels ---------- */
function showPanel(name) {
  $$(".rail [data-panel]").forEach((b) => b.classList.toggle("active", b.dataset.panel === name));
  $$(".panel [data-view]").forEach((s) => (s.hidden = s.dataset.view !== name));
  $("#panel").classList.add("open");
  if (name === "files") loadFiles(state.filePath);
  if (name === "models") { renderModels(); loadDoctor(); }
  if (name === "tasks") loadTasks();
  if (name === "knowledge") loadKnowledge();
  if (name === "settings") renderSettings();
}
$$(".rail [data-panel]").forEach((b) => (b.onclick = () => {
  const already = b.classList.contains("active") && !$("#panel").hidden;
  if (already && window.innerWidth > 820) { $("#panel").hidden = true; b.classList.remove("active"); return; }
  $("#panel").hidden = false; showPanel(b.dataset.panel);
}));
$("#togglePanel").onclick = () => $("#panel").classList.toggle("open");
function toggleInspector(force) {
  const insp = $("#inspector"); insp.hidden = force != null ? !force : !insp.hidden;
  $("#toggleInspector").setAttribute("aria-pressed", String(!insp.hidden));
}
$("#toggleInspector").onclick = () => toggleInspector();
$("#clearInspector").onclick = () => { if (state.current) state.current.events.innerHTML = ""; };

/* ---------- views: one per conversation, so runs continue in the background ---------- */
function makeView(cid) {
  const box = el("div", "view"); box.hidden = true; thread.append(box);
  const events = el("div", "run-events", '<p class="hint">Events from the next run in this chat appear here: routing, model loading, tool calls, permissions and timings.</p>');
  events.hidden = true; $("#events").append(events);
  const v = { cid, el: box, events, running: false, controller: null, waiting: 0, title: null, runStart: 0, loaded: false };
  if (cid) state.views.set(cid, v);
  return v;
}
function isEmptyDraft(v) { return v && !v.cid && !v.running && !v.el.childElementCount; }
function showView(v) {
  const prev = state.current;
  if (prev && prev !== v) {
    prev.el.hidden = true; prev.events.hidden = true;
    if (isEmptyDraft(prev)) { prev.el.remove(); prev.events.remove(); }
  }
  state.current = v; v.el.hidden = false; v.events.hidden = false;
  $("#empty").hidden = v.el.childElementCount > 0;
  if (!$("#empty").hidden && typeof Orb !== "undefined") Orb.wake();
  $("#sessionTitle").textContent = v.title || (v.cid ? "Session" : "New session");
  syncComposer(); markConversations();
  if ($("#empty").hidden) scroll(v); else thread.scrollTop = 0;
}
function syncComposer() {
  document.body.classList.toggle("busy", [...state.views.values(), state.current].some((x) => x?.running));
  const busy = !!state.current?.running;
  sendBtn.textContent = busy ? "Stop" : "Send"; sendBtn.classList.toggle("stop", busy);
}

/* ---------- inspector ---------- */
function logEvent(v, ev) {
  if (ev.type === "token" || ev.type === "reasoning") return;
  const t = ((performance.now() - v.runStart) / 1000).toFixed(2) + "s";
  const summary = ev.type === "meta" ? `${ev.agent} on ${shortModel(ev.model)}`
    : ev.type === "status" ? ev.state
    : ev.type === "tool_call" ? `${ev.name} (${ev.level})`
    : ev.type === "tool_result" ? `${ev.name} ${ev.ok ? "ok" : "failed"}`
    : ev.type === "permission" ? `${ev.name} waiting for you`
    : ev.type === "permission_result" ? (ev.allowed ? "allowed" : "denied")
    : ev.type === "stats" ? `${ev.completion_tokens} tok, ${ev.tokens_per_second ?? "?"} tok/s`
    : ev.type === "plan" ? `${ev.steps.length} steps`
    : ev.type === "step" ? `#${ev.index + 1} ${ev.agent} ${ev.state}`
    : ev.type === "review" ? (ev.pass ? "passed" : `failed: ${(ev.issues || []).join("; ")}`)
    : ev.text || "";
  const d = el("details", "ev");
  d.innerHTML = `<summary><span class="t">${t}</span><span class="k ${ev.type}">${ev.type}</span><span class="s">${esc(summary)}</span></summary><pre>${esc(JSON.stringify(ev, null, 2))}</pre>`;
  v.events.append(d);
  if (v === state.current) $("#inspector").scrollTop = $("#inspector").scrollHeight;
}

/* ---------- rendering messages ---------- */
function scroll(v) { if (!v || v === state.current) thread.scrollTop = thread.scrollHeight; }
function addUser(v, text, images = []) {
  if (v === state.current) $("#empty").hidden = true;
  const m = el("div", "msg user"); const b = el("div", "bubble");
  for (const src of images) { const img = document.createElement("img"); img.src = src; img.alt = "Attached image"; b.append(img); }
  b.append(document.createTextNode(text));
  m.append(b); v.el.append(m); scroll(v);
}
function addAssistant(v) {
  const m = el("div", "msg assistant");
  m.view = v; m.route = el("div", "route"); m.trace = null; m.body = el("div", "body");
  m.append(m.route, m.body); v.el.append(m); return m;
}
function setRoute(m, agent, model, title) {
  m.route.innerHTML = `<b>${esc(agent)}</b> on ${esc(shortModel(model))}`;
  if (title) m.route.title = title;
}
function ensureTrace(m) { if (!m.trace) { m.trace = el("div", "trace"); m.insertBefore(m.trace, m.body); } return m.trace; }
function argSummary(args) {
  const v = Object.values(args || {})[0]; if (v == null) return "";
  const s = String(v).split("\n")[0]; return s.length > 56 ? s.slice(0, 56) + "…" : s;
}
function addStep(m, id, name, args) {
  const d = el("details", "step"); d.dataset.id = id;
  d.innerHTML = `<summary>${esc(TOOL_VERB[name] || name)}(<b>${esc(argSummary(args))}</b>)<span class="ms"></span></summary><pre>${esc(JSON.stringify(args, null, 2))}</pre>`;
  if (name === "write_file" && typeof args.path === "string" && isPreviewable(args.path)) d.dataset.previewPath = args.path;
  ensureTrace(m).append(d); scroll(m.view); return d;
}
function stepEl(m, id) { return m.querySelector(`.step[data-id="${CSS.escape(id)}"]`); }
function finishStep(m, id, ok, output) {
  const d = stepEl(m, id); if (!d) return;
  d.classList.add(ok ? "ok" : "fail"); d.append(el("pre", null, esc(output)));
  if (ok && d.dataset.previewPath) {
    // Sibling of the <details>, not a child of it: a <details> only renders its
    // content while open, but the Preview button should stay visible either way.
    const btn = el("button", "preview-btn", '<svg viewBox="0 0 24 24"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7z"/><circle cx="12" cy="12" r="3"/></svg> Preview');
    btn.type = "button"; btn.onclick = () => openPreview(d.dataset.previewPath);
    d.after(btn);
  }
}
function addStats(m, s) {
  if (!s) return;
  const parts = [];
  if (s.completion_tokens) parts.push(`${s.completion_tokens} tokens`);
  if (s.tokens_per_second) parts.push(`${s.tokens_per_second} tok/s`);
  if (s.seconds != null) parts.push(`${s.seconds} s`);
  if (s.steps > 1) parts.push(`${s.steps} model calls`);
  if (parts.length) m.append(el("div", "stats", parts.map((p) => `<span>${esc(p)}</span>`).join("")));
  if (s.tokens_per_second) $("#sbSpeed").textContent = `${s.tokens_per_second} tok/s`;
}
function addGate(m, ev) {
  const v = m.view;
  v.waiting++; markConversations();
  const g = el("div", `gate ${ev.level}`);
  g.innerHTML = `<h3>Allow ${esc(ev.name)}?</h3><p>${GATE_TEXT[ev.level] || ""}</p>` +
    `<pre>${esc(ev.args.code ?? ev.args.command ?? JSON.stringify(ev.args, null, 2))}</pre><div class="actions">` +
    `<button class="allow">${ev.level === "dangerous" ? "Run it" : "Allow once"}</button>` +
    (ev.level === "sensitive" ? `<button class="always">Allow for this chat</button>` : "") +
    `<button class="deny">Deny</button></div>`;
  const decide = async (allow, remember) => {
    g.querySelector(".actions").replaceWith(el("div", "decided", allow ? (remember ? "Allowed for this chat." : "Allowed.") : "Denied."));
    v.waiting = Math.max(0, v.waiting - 1); markConversations();
    await api(`/api/permission/${ev.id}`, { method: "POST",
      body: JSON.stringify({ allow, remember, conversation_id: ev.conversation_id, tool: ev.name }) }).catch(() => {});
  };
  $(".allow", g).onclick = () => decide(true, false);
  $(".deny", g).onclick = () => decide(false, false);
  const always = $(".always", g); if (always) always.onclick = () => decide(true, true);
  ensureTrace(m).append(g); scroll(v);
  if (v === state.current) $(".allow", g).focus();
}

/* waiting indicator with a live timer */
function startWaiting(m, stateName, model) {
  stopWaiting(m);
  const w = el("div", "waiting" + (stateName === "loading" ? "" : " gen"));
  const label = { loading: `Loading ${shortModel(model)} from the pendrive`, planning: "Planning the steps",
                  reviewing: "Reviewing the result" }[stateName] || "Generating";
  const t0 = performance.now();
  const tick = () => (w.textContent = `${label}… ${Math.round((performance.now() - t0) / 1000)} s`);
  tick(); m.waitTimer = setInterval(tick, 1000); m.waitEl = w;
  m.insertBefore(w, m.body); scroll(m.view);
}
function stopWaiting(m) { if (m.waitTimer) clearInterval(m.waitTimer); m.waitEl?.remove(); m.waitTimer = m.waitEl = null; }

/* ---------- chat ---------- */
async function send(text) {
  const v = state.current;
  const images = state.images.map((i) => i.data);
  if (!v || v.running || (!text.trim() && !images.length)) return;
  Voice.stopSpeaking();
  addUser(v, text, images); input.value = ""; autosize(); clearAttachments();
  v.running = true; v.controller = new AbortController(); syncComposer(); markConversations();
  v.events.innerHTML = ""; v.runStart = performance.now();
  const m = addAssistant(v);
  let answer = "", reasoning = null;
  try {
    const res = await fetch("/api/chat", {
      method: "POST", headers: { "Content-Type": "application/json" }, signal: v.controller.signal,
      body: JSON.stringify({ message: text, conversation_id: v.cid, agent: $("#agent").value,
                             model: $("#model").value, web: $("#web").checked, images }),
    });
    if (!res.ok) throw new Error((await res.json()).detail || res.statusText);
    const reader = res.body.getReader(), dec = new TextDecoder();
    let buf = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let idx;
      while ((idx = buf.indexOf("\n\n")) >= 0) {
        const chunk = buf.slice(0, idx); buf = buf.slice(idx + 2);
        if (!chunk.startsWith("data:")) continue;
        const ev = JSON.parse(chunk.slice(5));
        logEvent(v, ev);
        switch (ev.type) {
          case "meta":
            if (!v.cid) {
              v.cid = ev.conversation_id; state.views.set(v.cid, v);
              v.title = text.trim().split("\n")[0];
              if (v === state.current) $("#sessionTitle").textContent = v.title;
              loadConversations();
            }
            setRoute(m, ev.agent_label, ev.model, `Agent: ${ev.reason}\nModel: ${ev.model_reason}`);
            if (v === state.current) $("#sbAgent").textContent = `agent: ${ev.agent}`;
            break;
          case "status": startWaiting(m, ev.state, ev.model); break;
          case "reasoning":
            stopWaiting(m);
            if (!reasoning) { reasoning = el("details", "thinking", "<summary>Thinking</summary><div></div>"); m.insertBefore(reasoning, m.body); }
            reasoning.querySelector("div").textContent += ev.text; break;
          case "token":
            stopWaiting(m);
            if (m.inStep) break;                       // team steps: their text shows in the plan box
            m.body.classList.add("cursor");
            answer += ev.text; m.body.innerHTML = markdown(answer); scroll(v); break;
          case "tool_call": stopWaiting(m); addStep(m, ev.id, ev.name, ev.args); break;
          case "permission": addGate(m, ev); break;
          case "tool_time": { const s = stepEl(m, ev.id); if (s) s.querySelector(".ms").textContent = `${ev.ms} ms`; break; }
          case "tool_result": finishStep(m, ev.id, ev.ok, ev.output); answer = ""; m.body.innerHTML = ""; break;
          case "stats": addStats(m, ev); if (state.settings?.voice_reply) Voice.speak(m.body.innerText); break;
          case "plan": renderPlan(m, ev.steps); break;
          case "step":
            updateStep(m, ev); m.inStep = ev.state !== "done";
            answer = ""; m.body.innerHTML = ""; break;
          case "review": addReview(m, ev); break;
          case "error": stopWaiting(m); m.append(el("div", "error", esc(ev.text))); break;
        }
      }
    }
  } catch (err) {
    m.append(el("div", "error", err.name === "AbortError" ? "Stopped." : esc(err.message)));
  } finally {
    stopWaiting(m); m.body.classList.remove("cursor");
    v.running = false; v.controller = null; v.waiting = 0; v.loaded = true;
    syncComposer(); loadConversations(); refreshStatus();
  }
}

/* ---------- conversations ---------- */
async function loadConversations() {
  const list = await api("/api/conversations").catch(() => []);
  const box = $("#conversations"); box.innerHTML = "";
  if (!list.length) box.append(el("p", "hint", "No chats yet."));
  for (const c of list) {
    const row = el("div", "conv"); row.dataset.id = c.id;
    const a = el("a"); a.href = "#"; a.textContent = c.title || "Untitled";
    a.onclick = (e) => { e.preventDefault(); openConversation(c.id, c.title); $("#panel").classList.remove("open"); };
    const mark = el("span", "mark");
    const del = el("button", "del", "✕"); del.setAttribute("aria-label", `Delete ${c.title}`);
    del.onclick = async () => {
      const v = state.views.get(c.id);
      if (v) { v.controller?.abort(); state.views.delete(c.id); v.el.remove(); v.events.remove(); }
      await api(`/api/conversations/${c.id}`, { method: "DELETE" });
      if (state.current === v) newChat();
      loadConversations();
    };
    row.append(a, mark, del); box.append(row);
    const v = state.views.get(c.id); if (v) v.title = v.title || c.title;
  }
  markConversations();
}
function markConversations() {
  for (const row of $$("#conversations .conv")) {
    const v = state.views.get(row.dataset.id);
    row.classList.toggle("active", !!v && v === state.current);
    const mark = $(".mark", row); if (!mark) continue;
    if (v?.waiting) { mark.textContent = "needs approval"; mark.className = "mark waiting"; mark.title = "A tool is waiting for your approval"; }
    else if (v?.running) { mark.textContent = "running"; mark.className = "mark running"; mark.title = "Still generating"; }
    else { mark.textContent = ""; mark.className = "mark"; mark.title = ""; }
  }
}
async function openConversation(cid, title) {
  let v = state.views.get(cid);
  if (v && (v.running || v.loaded)) { v.title = v.title || title; showView(v); return; }
  v = v || makeView(cid);
  v.title = title || "Session";
  const msgs = await api(`/api/conversations/${cid}`).catch(() => []);
  v.el.innerHTML = "";
  let m = null;
  for (const msg of msgs) {
    if (msg.role === "user") { addUser(v, msg.content); m = null; }
    else if (msg.role === "assistant") {
      if (!m) m = addAssistant(v);
      if (msg.meta) setRoute(m, state.agents[msg.meta.agent] || msg.meta.agent, msg.meta.model);
      for (const tc of msg.tool_calls || []) {
        let args = {}; try { args = JSON.parse(tc.function.arguments || "{}"); } catch {}
        addStep(m, tc.id, tc.function.name, args);
      }
      if (msg.meta?.plan) renderPlan(m, msg.meta.plan.map((i) => ({ agent: "step", instruction: i })), true);
      if (msg.content && !msg.tool_calls) { m.body.innerHTML = markdown(msg.content); addStats(m, msg.meta?.stats); }
    } else if (msg.role === "tool" && m) {
      finishStep(m, msg.tool_call_id, !/^Error|denied permission/.test(msg.content), msg.content);
    }
  }
  v.loaded = true;
  showView(v);
}
function newChat() {
  if (isEmptyDraft(state.current)) { showView(state.current); input.focus(); return; }
  showView(makeView(null));
  $("#web").checked = !!state.settings?.web_default;
  input.focus();
}

/* ---------- files ---------- */
async function loadFiles(path) {
  let data;
  try { data = await api(`/api/files?path=${encodeURIComponent(path)}`); }
  catch (e) { $("#fileList").innerHTML = `<p class="hint">${esc(e.message)}</p>`; return; }
  state.filePath = data.path;
  const parts = data.path === "." ? [] : data.path.split(/[\\/]/);
  const crumbs = $("#crumbs"); crumbs.innerHTML = "";
  const home = el("button", null, "workspace"); home.onclick = () => loadFiles("."); crumbs.append(home);
  parts.forEach((p, i) => {
    crumbs.append(" / ");
    const b = el("button", null, esc(p)); b.onclick = () => loadFiles(parts.slice(0, i + 1).join("/")); crumbs.append(b);
  });
  const list = $("#fileList"); list.innerHTML = "";
  if (!data.items.length) list.append(el("p", "hint", "Empty folder. Files the agents write appear here."));
  for (const it of data.items) {
    const b = el("button", "file", `<span class="ico">${it.dir ? "▸" : "·"}</span><span class="name">${esc(it.name)}${it.dir ? "/" : ""}</span><span class="size">${fmtSize(it.size)}</span>`);
    b.onclick = () => (it.dir ? loadFiles(it.path) : openFile(it.path));
    list.append(b);
  }
}
function workspaceUrl(path) { return `/workspace/${path.split("/").map(encodeURIComponent).join("/")}`; }
function isPreviewable(path) { return /\.(html?|svg)$/i.test(path); }

function setViewerTab(tab) {
  const preview = tab === "preview";
  $$("#viewerTabs .tab").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
  $("#viewerFrame").hidden = !preview;
  $("#viewerBody").hidden = preview;
  // Note: reading .src back after setting it to "" returns the current page URL, not "",
  // so we can't check "is it empty" — just (re)assign it whenever showing the preview tab.
  if (preview) $("#viewerFrame").src = workspaceUrl(state.viewerPath);
}
$$("#viewerTabs .tab").forEach((b) => (b.onclick = () => setViewerTab(b.dataset.tab)));

async function openFile(path) {
  const f = await api(`/api/file?path=${encodeURIComponent(path)}`);
  state.viewerPath = path;
  $("#viewerTitle").textContent = path;
  $("#viewerMeta").textContent = fmtSize(f.size);
  const ext = (path.split(".").pop() || "").toLowerCase();
  $("#viewerBody").innerHTML = f.binary ? "Binary file, preview not available." : highlight(f.content, ext === "py" ? "python" : ext);
  const canPreview = isPreviewable(path) && !f.binary;
  $("#viewerTabs").hidden = !canPreview;
  const openLink = $("#viewerOpen");
  openLink.hidden = !isPreviewable(path);
  if (!openLink.hidden) openLink.href = workspaceUrl(path);
  setViewerTab(canPreview ? "preview" : "source");
  $("#viewer").showModal();
}
function openPreview(path) { openFile(path); }
$("#viewerClose").onclick = () => $("#viewer").close();
$("#viewer").addEventListener("close", () => { $("#viewerFrame").src = ""; });
$("#viewerAsk").onclick = () => {
  $("#viewer").close(); input.value = `Read \`${state.viewerPath}\` and `; autosize(); input.focus();
};
$("#refreshFiles").onclick = () => loadFiles(state.filePath);

/* ---------- models ---------- */
async function refreshStatus() {
  let s;
  try { s = await api("/api/status"); }
  catch { $("#sbServer").innerHTML = '<i class="dot off"></i>AURIX core offline'; return; }
  state.models = s.models;
  $("#sbServer").innerHTML = `<i class="dot ${s.llama_online ? "on" : "off"}"></i>${s.llama_online ? "model server" : "model server offline"}`;
  const loaded = s.models.find((m) => m.status === "loaded"), loading = s.models.find((m) => m.status === "loading");
  const sbm = $("#sbModel");
  sbm.textContent = loaded ? shortModel(loaded.id) : loading ? `loading ${shortModel(loading.id)}` : "no model loaded";
  sbm.classList.toggle("loaded", !!loaded);
  $("#sbWorkspace").textContent = s.workspace; $("#sbWorkspace").title = "Workspace folder";
  $("#workspacePath").textContent = s.workspace;
  $("#sbVersion").textContent = `AURIX ${s.version}`;
  if ($("#agent").options.length === 1) for (const a of s.agents) { state.agents[a.name] = a.label; $("#agent").append(new Option(a.label, a.name)); }
  state.agentList = s.agents;
  const badge = $("#taskBadge"), n = s.pending_permissions || 0;
  badge.hidden = !n && !s.tasks_active; badge.textContent = n ? String(n) : "•";
  badge.title = n ? `${n} action(s) waiting for your approval` : "A task is running";
  if (n !== state.lastPending) { state.lastPending = n; if (!$("[data-view=tasks]").hidden) loadTasks(); }
  fillModelSelect($("#model"), "Per agent", $("#model").value || state.settings?.model_override || "");
  if (!$("[data-view=models]").hidden) renderModels();
}
function fillModelSelect(sel, emptyLabel, value) {
  const ids = state.models.map((m) => m.id);
  const current = [...sel.options].map((o) => o.value).join("|");
  if (current !== ["", ...ids].join("|")) {
    sel.innerHTML = ""; sel.append(new Option(emptyLabel, ""));
    ids.forEach((id) => sel.append(new Option(shortModel(id), id)));
  }
  sel.value = ids.includes(value) ? value : "";
}
function renderModels() {
  const box = $("#modelList"); box.innerHTML = "";
  if (!state.models.length) { box.append(el("p", "hint", "No models found. Put .gguf files in models/ and restart the launcher.")); return; }
  for (const m of state.models) {
    const st = m.failed && m.status !== "loaded" ? "failed" : m.status;
    const label = { loaded: "loaded", loading: "loading", unloaded: "not loaded", failed: "failed to load" }[st] || st;
    const card = el("div", "model", `<div class="name">${esc(shortModel(m.id))}</div><div class="row"><span class="chip ${st}">${label}</span></div>`);
    const btn = el("button", "icon-btn", m.status === "loaded" ? "Unload" : "Load");
    btn.disabled = m.status === "loading";
    btn.onclick = async () => {
      btn.disabled = true; btn.textContent = m.status === "loaded" ? "Unloading…" : "Loading…";
      await api(`/api/models/${m.status === "loaded" ? "unload" : "load"}`, { method: "POST", body: JSON.stringify({ model: m.id }) }).catch(() => {});
      pollModels(20);
    };
    $(".row", card).append(btn); box.append(card);
  }
}
async function pollModels(times) { for (let i = 0; i < times; i++) { await refreshStatus(); if (!state.models.some((m) => m.status === "loading") && i > 0) break; await new Promise((r) => setTimeout(r, 1500)); } }
$("#refreshModels").onclick = () => refreshStatus();

/* ---------- settings ---------- */
async function loadSettings() { state.settings = await api("/api/settings").catch(() => null); }
function renderSettings() {
  const s = state.settings; if (!s) return;
  const f = $("#settingsForm");
  fillModelSelect(f.elements["models.fast"], "Automatic (config.json)", s.models.fast);
  fillModelSelect(f.elements["models.strong"], "Automatic (config.json)", s.models.strong);
  fillModelSelect(f.elements["models.code"], "Automatic (config.json)", s.models.code);
  fillModelSelect(f.elements["models.vision"], "Automatic (config.json)", s.models.vision);
  fillModelSelect(f.elements["model_override"], "Per agent", s.model_override);
  for (const k of ["max_concurrent_tasks", "review_rounds", "voice_lang", "n8n_url", "n8n_api_key", "autonomy_default"]) f.elements[k].value = s[k] ?? "";
  for (const k of ["memory_in_prompt", "browser_headless", "voice_reply"]) f.elements[k].checked = !!s[k];
  for (const k of ["temperature", "top_p", "max_tokens", "max_tool_steps", "history_messages", "custom_instructions"]) f.elements[k].value = s[k];
  f.elements.thinking.checked = s.thinking; f.elements.web_default.checked = s.web_default;
  $$("output[data-for]", f).forEach((o) => (o.textContent = f.elements[o.dataset.for].value));
  const perm = $("#permList"); perm.innerHTML = "";
  for (const [tool, level] of Object.entries(s.permissions)) {
    const sel = el("select", `lv-${level}`); sel.name = `perm.${tool}`;
    ["safe", "sensitive", "dangerous"].forEach((l) => sel.append(new Option(l, l)));
    sel.value = level; sel.onchange = () => (sel.className = `lv-${sel.value}`);
    perm.append(el("span", null, esc(tool)), sel);
  }
}
$("#settingsForm").addEventListener("input", (e) => {
  if (e.target.type === "range") $(`output[data-for="${e.target.name}"]`).textContent = e.target.value;
});
$("#settingsForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target, body = { models: {}, permissions: {} };
  body.models.fast = f.elements["models.fast"].value; body.models.strong = f.elements["models.strong"].value;
  body.models.code = f.elements["models.code"].value; body.models.vision = f.elements["models.vision"].value;
  for (const k of ["max_concurrent_tasks", "review_rounds"]) body[k] = parseInt(f.elements[k].value || "1", 10);
  for (const k of ["voice_lang", "n8n_url", "n8n_api_key", "autonomy_default"]) body[k] = f.elements[k].value;
  for (const k of ["memory_in_prompt", "browser_headless", "voice_reply"]) body[k] = f.elements[k].checked;
  body.model_override = f.elements.model_override.value;
  for (const k of ["temperature", "top_p"]) body[k] = parseFloat(f.elements[k].value);
  for (const k of ["max_tokens", "max_tool_steps", "history_messages"]) body[k] = parseInt(f.elements[k].value || "0", 10);
  body.custom_instructions = f.elements.custom_instructions.value;
  body.thinking = f.elements.thinking.checked; body.web_default = f.elements.web_default.checked;
  $$("#permList select").forEach((s) => (body.permissions[s.name.slice(5)] = s.value));
  try {
    state.settings = await api("/api/settings", { method: "PUT", body: JSON.stringify(body) });
    renderSettings(); $("#model").value = state.settings.model_override;
    flash("Saved.");
  } catch (err) { flash(err.message, true); }
});
$("#resetSettings").onclick = async () => { state.settings = await api("/api/settings", { method: "DELETE" }); renderSettings(); flash("Defaults restored."); };
function flash(text, bad) { const m = $("#settingsMsg"); m.textContent = text; m.style.color = bad ? "var(--danger)" : ""; setTimeout(() => (m.textContent = ""), 2500); }

/* ---------- input + shortcuts ---------- */
function autosize() { input.style.height = "auto"; input.style.height = Math.min(input.scrollHeight, 240) + "px"; }
input.addEventListener("input", autosize);
input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); send(input.value); }
});
$("#composer").addEventListener("submit", (e) => {
  e.preventDefault();
  if (state.current?.running) { state.current.controller?.abort(); return; }
  send(input.value);
});
$("#newChat").onclick = newChat;
$$(".starters button").forEach((b) => (b.onclick = () => send(b.dataset.prompt)));
document.addEventListener("keydown", (e) => {
  const mod = e.ctrlKey || e.metaKey; if (!mod) return;
  const k = e.key.toLowerCase();
  if (k === "k") { e.preventDefault(); newChat(); }
  else if (k === "i") { e.preventDefault(); toggleInspector(); }
  else if (k === ",") { e.preventDefault(); $("#panel").hidden = false; showPanel("settings"); }
  else if (e.shiftKey && k === "e") { e.preventDefault(); $("#panel").hidden = false; showPanel("files"); }
  else if (e.shiftKey && k === "m") { e.preventDefault(); $("#panel").hidden = false; showPanel("models"); }
  else if (e.shiftKey && k === "c") { e.preventDefault(); $("#panel").hidden = false; showPanel("chats"); }
  else if (e.shiftKey && k === "t") { e.preventDefault(); $("#panel").hidden = false; showPanel("tasks"); }
  else if (e.shiftKey && k === "k") { e.preventDefault(); $("#panel").hidden = false; showPanel("knowledge"); }
});

/* ---------- boot ---------- */
(async () => {
  await loadSettings();
  $("#web").checked = !!state.settings?.web_default;
  showView(makeView(null));
  await refreshStatus(); loadConversations(); input.focus();
  setInterval(refreshStatus, 5000);
  Orb.wake();
})();

/* ---------- plasma orb: AURIX's core (canvas, 30 fps max, only while visible) ---------- */
const Orb = (() => {
  const cv = $("#orb"); if (!cv) return { wake() {}, boost() {} };
  const ctx = cv.getContext("2d");
  const still = matchMedia("(prefers-reduced-motion: reduce)");
  const N = 22, SEG = 180, CHUNK = 12;
  let seed = 11;
  const rnd = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
  const norm = (a) => { const l = Math.hypot(...a); return a.map((x) => x / l); };
  const fil = [];
  for (let i = 0; i < N; i++) {
    const u = norm([rnd() * 2 - 1, rnd() * 2 - 1, rnd() * 2 - 1]);
    let w = [rnd() * 2 - 1, rnd() * 2 - 1, rnd() * 2 - 1];
    const d = u[0] * w[0] + u[1] * w[1] + u[2] * w[2];
    const v = norm([w[0] - d * u[0], w[1] - d * u[1], w[2] - d * u[2]]);
    fil.push({ u, v, ph: rnd() * 100, f1: 3 + Math.floor(rnd() * 4), f2: 6 + Math.floor(rnd() * 6), sp: 0.3 + rnd() * 0.5 });
  }
  let energy = 0.2, target = 0.2, raf = 0, last = 0, W = 0, H = 0, dpr = 1, lastBoost = -1e9;
  const ACTIVE_MS = 3500;   // animate only briefly after you type, then freeze

  function size() {
    const r = cv.getBoundingClientRect(); dpr = Math.min(devicePixelRatio || 1, 1.5);
    W = cv.width = Math.max(1, Math.round(r.width * dpr)); H = cv.height = Math.max(1, Math.round(r.height * dpr));
  }
  function draw(t) {
    energy += (target - energy) * 0.06; target += (0.2 - target) * 0.01;   // boosts decay back to idle
    const cx = W / 2, cy = H / 2, R = Math.min(W, H) * 0.36;
    ctx.globalCompositeOperation = "source-over"; ctx.clearRect(0, 0, W, H);
    const halo = ctx.createRadialGradient(cx, cy, R * 0.15, cx, cy, R * 1.45);
    halo.addColorStop(0, `rgba(120,70,255,${0.20 + energy * 0.15})`);
    halo.addColorStop(0.55, "rgba(90,40,220,0.10)"); halo.addColorStop(1, "rgba(0,0,0,0)");
    ctx.fillStyle = halo; ctx.fillRect(0, 0, W, H);
    const core = ctx.createRadialGradient(cx, cy + R * 0.35, 0, cx, cy + R * 0.2, R * 1.05);
    core.addColorStop(0, `rgba(225,90,255,${0.10 + energy * 0.08})`); core.addColorStop(1, "rgba(0,0,0,0)");
    ctx.fillStyle = core; ctx.fillRect(0, 0, W, H);
    ctx.globalCompositeOperation = "lighter"; ctx.lineCap = "round"; ctx.lineJoin = "round";
    const rot = t * (0.10 + energy * 0.35), cr = Math.cos(rot), sr = Math.sin(rot);
    const tilt = 0.4, ct = Math.cos(tilt), st = Math.sin(tilt);
    const amp = 0.085 + energy * 0.10, spd = 1 + energy * 2.5;
    for (const f of fil) {
      const pts = [];
      for (let s = 0; s <= SEG; s++) {
        const th = (s / SEG) * Math.PI * 2;
        let x = f.u[0] * Math.cos(th) + f.v[0] * Math.sin(th);
        let y = f.u[1] * Math.cos(th) + f.v[1] * Math.sin(th);
        let z = f.u[2] * Math.cos(th) + f.v[2] * Math.sin(th);
        const n = Math.sin(f.f1 * th + t * f.sp * spd + f.ph) * 0.50
                + Math.sin(f.f2 * th - t * f.sp * 1.7 * spd + f.ph * 2) * 0.32
                + Math.sin(19 * th + t * 1.3 * spd + f.ph * 3) * 0.24          // crackle
                + Math.sin(33 * th - t * 2.1 * spd + f.ph * 5) * 0.13
                + Math.sin(47 * th + t * 3.3 * spd + f.ph * 7) * (0.05 + 0.10 * energy);
        const r = 1 + amp * n;
        [x, z] = [x * cr + z * sr, -x * sr + z * cr];          // spin around the vertical axis
        [y, z] = [y * ct - z * st, y * st + z * ct];           // fixed tilt towards the viewer
        pts.push([cx + x * R * r, cy + y * R * r, z, y]);
      }
      for (let s = 0; s < SEG; s += CHUNK) {
        const a = pts[s], b = pts[Math.min(s + CHUNK, SEG)];
        const depth = (a[2] + b[2]) / 2, vert = (a[3] + b[3]) / 2;   // -1 (top) .. 1 (bottom)
        const k = Math.min(Math.max((vert + 1) / 2, 0), 1);
        const col = [150 + 105 * k, 110 - 20 * k, 255 - 30 * k];     // violet -> magenta
        const alpha = 0.28 + 0.72 * ((depth + 1) / 2);                 // back filaments fade
        ctx.beginPath(); ctx.moveTo(a[0], a[1]);
        for (let j = s + 1; j <= Math.min(s + CHUNK, SEG); j++) ctx.lineTo(pts[j][0], pts[j][1]);
        ctx.strokeStyle = `rgba(${col[0] | 0},${col[1] | 0},${col[2] | 0},${alpha * 0.13})`; ctx.lineWidth = 6 * dpr; ctx.stroke();
        ctx.strokeStyle = `rgba(${Math.min(255, col[0] + 40) | 0},${Math.min(255, col[1] + 60) | 0},255,${alpha * 0.85})`; ctx.lineWidth = 1.3 * dpr; ctx.stroke();
      }
    }
  }
  function visible() { return !document.hidden && cv.offsetParent !== null; }
  function frame(ts) {
    if (!visible()) { raf = 0; return; }
    const settled = performance.now() - lastBoost > ACTIVE_MS && Math.abs(target - energy) < 0.03;
    if (settled) { draw(ts / 1000); raf = 0; return; }        // freeze on a still frame: zero work at idle
    raf = requestAnimationFrame(frame);
    if (ts - last < 42) return;                               // ~24 fps while active
    last = ts; draw(ts / 1000);
  }
  function wake() {
    requestAnimationFrame((ts) => { if (!visible()) return; size(); draw(ts / 1000); });
  }
  addEventListener("resize", () => { if (visible()) { size(); draw(performance.now() / 1000); } });
  document.addEventListener("visibilitychange", () => { if (visible()) wake(); });
  return {
    wake,
    boost(v = 0.55) {
      if (still.matches || !visible()) return;
      target = Math.max(target, v); lastBoost = performance.now();
      if (!raf) raf = requestAnimationFrame(frame);
    },
  };
})();
input.addEventListener("input", () => Orb.boost(0.6));

/* ---------- Phase 4: team progress inside a reply ---------- */
function renderPlan(m, steps, historic = false) {
  const box = el("div", "plan");
  box.innerHTML = `<h4>${historic ? "Team plan" : "Plan"}</h4><ol>${steps.map((s, i) =>
    `<li data-i="${i}"><b>${esc(state.agents[s.agent] || s.agent)}</b>${esc(s.instruction)}<span class="res"></span></li>`).join("")}</ol>`;
  if (historic) box.querySelectorAll("li").forEach((li) => { li.classList.add("done"); li.querySelector("b").remove(); });
  m.plan = box; m.insertBefore(box, m.trace || m.body); scroll(m.view);
}
function updateStep(m, ev) {
  if (!m.plan) return;
  let li = m.plan.querySelector(`li[data-i="${ev.index}"]`);
  if (!li && ev.state === "running") {                   // a fix step added after review
    li = el("li", null, `<b>${esc(ev.label || ev.agent)}</b>${esc(ev.instruction || "")}<span class="res"></span>`);
    li.dataset.i = ev.index; m.plan.querySelector("ol").append(li);
  }
  if (!li) return;
  li.classList.remove("running", "done"); li.classList.add(ev.state === "done" ? "done" : "running");
  if (ev.result) li.querySelector(".res").textContent = ev.result;
}
function addReview(m, ev) {
  if (!m.plan) return;
  const r = el("div", "review" + (ev.pass ? "" : " fail"),
    ev.pass ? `Review ${ev.round}: passed` : `Review ${ev.round}: ${esc((ev.issues || []).join("; ") || "needs a fix")}`);
  m.plan.append(r);
}

/* ---------- Phase 7: image attachments (resized in the browser to keep prompts small) ---------- */
function clearAttachments() { state.images = []; renderAttachments(); }
function renderAttachments() {
  // Image chips are rebuilt from state; document chips are appended directly as they
  // upload (see uploadDocsFromComposer), so we only clear+redraw the image half here.
  $$(".thumb-chip", $("#attachments")).forEach((c) => c.remove());
  const box = $("#attachments");
  state.images.forEach((img, i) => {
    const chip = el("div", "thumb-chip", `<img src="${img.data}" alt="${esc(img.name)}"><button type="button" aria-label="Remove ${esc(img.name)}">✕</button>`);
    $("button", chip).onclick = () => { state.images.splice(i, 1); renderAttachments(); };
    box.prepend(chip);
  });
}

/* ---------- + button: upload a document straight into the knowledge base from chat ---------- */
const DOC_ICON = '<svg viewBox="0 0 24 24"><path d="M6 2h9l5 5v15H6z"/><path d="M15 2v5h5"/></svg>';
const SPINNER_ICON = '<svg viewBox="0 0 24 24" class="spin"><path d="M12 2a10 10 0 0 1 10 10"/></svg>';
async function uploadDocsFromComposer(files) {
  for (const file of files) {
    const chip = el("div", "doc-chip", `<span class="ico">${SPINNER_ICON}</span><span class="name">${esc(file.name)}</span>`);
    $("#attachments").append(chip);
    try {
      const fd = new FormData(); fd.append("file", file);
      const res = await fetch("/api/docs", { method: "POST", body: fd });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Upload failed");
      chip.querySelector(".ico").innerHTML = DOC_ICON;
      chip.append(el("span", "note", `${data.chunks} passages`));
      const del = el("button", null, "✕"); del.type = "button"; del.setAttribute("aria-label", `Remove ${file.name}`);
      del.onclick = async () => { chip.remove(); await api(`/api/docs/${data.id}`, { method: "DELETE" }).catch(() => {}); };
      chip.append(del);
      // Note in the thread so it's clear the file is now searchable, not just "sent".
      const v = state.current;
      if (v) {
        v.el.append(el("div", "route", `<b>Added to knowledge base</b> ${esc(file.name)} — ${data.chunks} passages, searchable in this and future chats`));
        scroll(v);
      }
    } catch (err) {
      chip.classList.add("error");
      chip.querySelector(".ico").innerHTML = DOC_ICON;
      chip.append(el("span", "note", err.message));
      setTimeout(() => chip.remove(), 5000);
    }
  }
}
$("#docInput").addEventListener("change", (e) => {
  uploadDocsFromComposer([...e.target.files]);
  e.target.value = ""; input.focus();
});
function readImage(file) {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => {
      const img = new Image();
      img.onload = () => {
        const max = 1024, k = Math.min(1, max / Math.max(img.width, img.height));
        const c = document.createElement("canvas"); c.width = Math.round(img.width * k); c.height = Math.round(img.height * k);
        c.getContext("2d").drawImage(img, 0, 0, c.width, c.height);
        resolve({ name: file.name, data: c.toDataURL("image/jpeg", 0.85) });
      };
      img.onerror = reject; img.src = r.result;
    };
    r.onerror = reject; r.readAsDataURL(file);
  });
}
$("#imageInput").addEventListener("change", async (e) => {
  for (const f of [...e.target.files].slice(0, 4 - state.images.length)) state.images.push(await readImage(f));
  e.target.value = ""; renderAttachments(); input.focus();
});
input.addEventListener("paste", async (e) => {
  const files = [...(e.clipboardData?.files || [])].filter((f) => f.type.startsWith("image/"));
  if (!files.length) return;
  e.preventDefault();
  for (const f of files.slice(0, 4 - state.images.length)) state.images.push(await readImage(f));
  renderAttachments();
});

/* ---------- Phase 8: voice (browser speech; no extra model, no extra heat) ---------- */
const Voice = (() => {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  const btn = $("#micBtn");
  let rec = null, base = "";
  if (!SR) { btn.title = "Voice input isn't supported in this browser"; btn.disabled = true; btn.style.opacity = .35; }
  function stop() { rec?.stop(); }
  btn.onclick = () => {
    if (!SR) return;
    if (rec) { stop(); return; }
    rec = new SR(); rec.lang = state.settings?.voice_lang || "en-US"; rec.interimResults = true; rec.continuous = true;
    base = input.value ? input.value.trimEnd() + " " : "";
    rec.onresult = (e) => {
      let text = ""; for (const r of e.results) text += r[0].transcript;
      input.value = base + text; autosize();
    };
    rec.onend = () => { rec = null; btn.setAttribute("aria-pressed", "false"); input.focus(); };
    rec.onerror = (e) => { flashError(`Voice input: ${e.error}`); };
    rec.start(); btn.setAttribute("aria-pressed", "true");
  };
  function speak(text) {
    if (!("speechSynthesis" in window) || !text) return;
    const clean = text.replace(/Copy\n/g, "").replace(/```[\s\S]*?```/g, " (code) ").slice(0, 1500);
    const u = new SpeechSynthesisUtterance(clean); u.lang = state.settings?.voice_lang || "en-US";
    speechSynthesis.cancel(); speechSynthesis.speak(u);
  }
  function stopSpeaking() { if ("speechSynthesis" in window) speechSynthesis.cancel(); }
  return { speak, stopSpeaking, stop };
})();
function flashError(text) { const v = state.current; if (v) { v.el.append(el("div", "error", esc(text))); scroll(v); } }

/* ---------- Phase 10: tasks + approvals ---------- */
const TASK_STATUS = { queued: "queued", scheduled: "scheduled", running: "running", waiting: "needs approval",
                      done: "done", failed: "failed", cancelled: "cancelled", paused: "paused" };
function fillTaskAgents() {
  const sel = $("#taskForm").elements.agent; if (sel.options.length) return;
  sel.append(new Option("Team: plan, build, review", "team"));
  for (const a of state.agentList) if (a.name !== "team") sel.append(new Option(`${a.label} only`, a.name));
}
$("#taskForm").elements.when.addEventListener("change", (e) =>
  $$("[data-when]", $("#taskForm")).forEach((l) => (l.hidden = l.dataset.when !== e.target.value)));
$("#taskForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target, when = f.elements.when.value;
  let schedule = null;
  if (when === "once") {
    if (!f.elements.at.value) return taskMsg("Pick a time.", true);
    schedule = { type: "once", at: new Date(f.elements.at.value).getTime() / 1000 };
  } else if (when === "interval") schedule = { type: "interval", minutes: parseInt(f.elements.minutes.value || "60", 10) };
  else if (when === "daily") schedule = { type: "daily", time: f.elements.time.value || "09:00" };
  try {
    await api("/api/tasks", { method: "POST", body: JSON.stringify({ goal: f.elements.goal.value,
      agent: f.elements.agent.value, autonomy: f.elements.autonomy.value, web: f.elements.web.checked, schedule }) });
    f.elements.goal.value = ""; taskMsg(when === "now" ? "Started." : "Scheduled."); loadTasks();
  } catch (err) { taskMsg(err.message, true); }
});
function taskMsg(t, bad) { const m = $("#taskMsg"); m.textContent = t; m.style.color = bad ? "var(--danger)" : "var(--safe)"; setTimeout(() => (m.textContent = ""), 2500); }

async function loadApprovals() {
  const list = await api("/api/permissions").catch(() => []);
  const box = $("#approvals"); box.innerHTML = "";
  const mine = list.filter((p) => p.origin === "task");
  if (mine.length) box.append(el("p", "hint", "Waiting for your approval"));
  for (const p of mine) {
    const card = el("div", `approval ${p.level}`,
      `<div class="a-title"><b>${esc(p.tool)}</b> for task: ${esc(p.title || "")}</div>` +
      `<pre>${esc(p.args.code ?? p.args.command ?? p.args.script ?? JSON.stringify(p.args, null, 2))}</pre>` +
      `<div class="row"><button class="allow">${p.level === "dangerous" ? "Run it" : "Allow"}</button><button class="deny">Deny</button></div>`);
    const decide = async (allow) => { await api(`/api/permission/${p.id}`, { method: "POST", body: JSON.stringify({ allow }) }).catch(() => {}); loadTasks(); refreshStatus(); };
    $(".allow", card).onclick = () => decide(true); $(".deny", card).onclick = () => decide(false);
    box.append(card);
  }
}
async function loadTasks() {
  fillTaskAgents(); loadApprovals();
  const list = await api("/api/tasks").catch(() => []);
  const box = $("#taskList"); box.innerHTML = "";
  if (!list.length) box.append(el("p", "hint", "No tasks yet. Give AURIX a goal above and it works on it in the background."));
  for (const t of list) {
    const progress = t.steps ? ` ${t.steps_done}/${t.steps} steps` : "";
    const card = el("div", "task", `<div class="t-title" title="${esc(t.goal)}">${esc(t.title)}</div>
      <div class="t-meta"><span class="chip ${t.status}">${TASK_STATUS[t.status] || t.status}</span>${esc(progress)}, ${esc(t.schedule_text)}${t.agent !== "team" ? ", " + esc(state.agents[t.agent] || t.agent) : ""}</div>
      ${t.error ? `<div class="t-meta" style="color:var(--danger)">${esc(t.error.slice(0, 140))}</div>` : ""}
      <div class="t-actions"></div>`);
    const acts = $(".t-actions", card);
    const add = (label, fn) => { const b = el("button", "icon-btn", label); b.onclick = fn; acts.append(b); };
    add("Open", () => openTask(t.id));
    if (["running", "waiting", "queued", "scheduled"].includes(t.status)) add("Cancel", () => taskAction(t.id, "cancel"));
    if (["failed", "paused", "cancelled"].includes(t.status)) add("Resume", () => taskAction(t.id, "resume"));
    if (["scheduled", "done"].includes(t.status)) add("Run now", () => taskAction(t.id, "run"));
    add("Delete", async () => { await api(`/api/tasks/${t.id}`, { method: "DELETE" }); loadTasks(); });
    box.append(card);
  }
}
async function taskAction(id, action) { await api(`/api/tasks/${id}/${action}`, { method: "POST" }).catch(() => {}); setTimeout(loadTasks, 300); }
$("#refreshTasks").onclick = loadTasks;

async function openTask(id) {
  state.taskView = { id, after: 0 };
  $("#tvEvents").innerHTML = ""; $("#tvResult").innerHTML = "";
  $("#taskViewer").showModal();
  await pollTask();
}
async function pollTask() {
  const tv = state.taskView; if (!tv || !$("#taskViewer").open) return;
  const t = await api(`/api/tasks/${tv.id}?after=${tv.after}`).catch(() => null);
  if (!t) return;
  $("#tvTitle").textContent = t.title;
  $("#tvMeta").innerHTML = `<span class="chip ${t.status}">${TASK_STATUS[t.status] || t.status}</span> ${esc(t.schedule_text)}, autonomy: ${esc(t.autonomy)}`;
  for (const e of t.events) {
    tv.after = Math.max(tv.after, e.id);
    const d = e.type === "tool_call" ? `${e.name}(${argSummary(e.args)})` : e.type === "tool_result" ? `${e.name}: ${e.ok ? "ok" : "failed"}`
      : e.type === "plan" ? e.steps.map((s, i) => `${i + 1}. [${s.agent}] ${s.instruction}`).join("\n")
      : e.type === "step" ? `#${e.index + 1} ${e.agent} ${e.state}${e.result ? "\n" + e.result.slice(0, 300) : ""}`
      : e.type === "review" ? (e.pass ? "passed" : "issues: " + (e.issues || []).join("; "))
      : e.type === "permission" ? `waiting for approval: ${e.name}` : e.type === "permission_result" ? (e.allowed ? "allowed" : "denied")
      : e.type === "status" ? (e.state + (e.note ? ` (${e.note})` : "")) : e.type === "done" ? "finished" : e.text || e.schedule || "";
    const row = el("div", `e ${e.type}`, `<span class="k">${esc(e.type)}</span><span class="d">${esc(d)}</span>`);
    $("#tvEvents").append(row);
  }
  $("#tvEvents").scrollTop = $("#tvEvents").scrollHeight;
  $("#tvResult").innerHTML = t.result ? markdown(t.result) : `<p class="hint flush">${t.status === "waiting" ? "Waiting for your approval in the Tasks panel." : "The result appears here when the task finishes."}</p>`;
  const acts = $("#tvActions"); acts.innerHTML = "";
  if (["running", "waiting", "queued", "scheduled"].includes(t.status)) { const b = el("button", null, "Cancel task"); b.onclick = () => taskAction(t.id, "cancel"); acts.append(b); }
  if (["failed", "paused", "cancelled"].includes(t.status)) { const b = el("button", null, "Resume from checkpoint"); b.onclick = () => taskAction(t.id, "resume"); acts.append(b); }
  if (["running", "waiting", "queued"].includes(t.status)) setTimeout(pollTask, 2000);
  else setTimeout(pollTask, 6000);
}
$("#tvClose").onclick = () => { $("#taskViewer").close(); state.taskView = null; };

/* ---------- Phase 5: knowledge (documents + long-term memory) ---------- */
async function loadKnowledge() {
  const [docs, facts] = await Promise.all([api("/api/docs").catch(() => []), api("/api/memory").catch(() => [])]);
  const dl = $("#docList"); dl.innerHTML = docs.length ? "" : '<p class="hint flush">No documents yet.</p>';
  for (const d of docs) {
    const row = el("div", "krow", `<div class="k-text">${esc(d.name)}<div class="k-meta">${d.chunks} passages, ${fmtSize(d.chars)} of text</div></div><button aria-label="Remove ${esc(d.name)}">✕</button>`);
    $("button", row).onclick = async () => { await api(`/api/docs/${d.id}`, { method: "DELETE" }); loadKnowledge(); };
    dl.append(row);
  }
  const fl = $("#factList"); fl.innerHTML = facts.length ? "" : '<p class="hint flush">Nothing remembered yet. Tell AURIX "remember that…" in a chat, or add one here.</p>';
  for (const f of facts) {
    const row = el("div", "krow", `<div class="k-text">${esc(f.text)}<div class="k-meta">#${f.id}, from ${esc(f.source)}</div></div><button aria-label="Forget this">✕</button>`);
    $("button", row).onclick = async () => { await api(`/api/memory/${f.id}`, { method: "DELETE" }); loadKnowledge(); };
    fl.append(row);
  }
}
$("#docUpload").addEventListener("change", async (e) => {
  for (const file of e.target.files) {
    const fd = new FormData(); fd.append("file", file);
    const res = await fetch("/api/docs", { method: "POST", body: fd });
    if (!res.ok) { const d = await res.json().catch(() => ({})); alert(d.detail || `Could not add ${file.name}`); }
  }
  e.target.value = ""; loadKnowledge();
});
let searchTimer;
$("#docSearch").addEventListener("input", (e) => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(async () => {
    const q = e.target.value.trim(), box = $("#docHits"); box.innerHTML = "";
    if (!q) return;
    const hits = await api(`/api/docs/search?q=${encodeURIComponent(q)}`).catch(() => []);
    if (!hits.length) box.append(el("p", "hint flush", "No matching passages."));
    for (const h of hits) box.append(el("div", "krow", `<div class="k-text">${esc(h.text.slice(0, 220))}${h.text.length > 220 ? "…" : ""}<div class="k-meta">${esc(h.name)}, score ${h.score}</div></div>`));
  }, 300);
});
$("#factForm").addEventListener("submit", async (e) => {
  e.preventDefault(); const t = e.target.elements.text.value.trim(); if (!t) return;
  await api("/api/memory", { method: "POST", body: JSON.stringify({ text: t }) }); e.target.reset(); loadKnowledge();
});

/* ---------- Phase 9: this computer ---------- */
async function loadDoctor() {
  const d = await api("/api/doctor").catch(() => null); if (!d) return;
  $("#doctor").innerHTML = `<dl><dt>System</dt><dd>${esc(d.os)} (${esc(d.arch)})</dd><dt>CPU</dt><dd>${d.cpu_cores} cores</dd>
    <dt>Memory</dt><dd>${d.ram_gb ?? "?"} GB</dd><dt>GPU</dt><dd>${esc(d.gpu)}</dd>
    <dt>Pendrive</dt><dd>${d.drive_free_gb} GB free of ${d.drive_total_gb} GB</dd>
    <dt>Tools</dt><dd>git ${d.git ? "yes" : "no"}, browser automation ${d.playwright ? "yes" : "no"}</dd></dl>
    ${d.tips.length ? `<ul>${d.tips.map((t) => `<li>${esc(t)}</li>`).join("")}</ul>` : ""}`;
}
setInterval(() => { if (!$("[data-view=tasks]").hidden) loadTasks(); }, 5000);
