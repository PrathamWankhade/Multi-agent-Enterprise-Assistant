/* ==========================================================================
   Enterprise Assistant — front-end application
   ========================================================================== */

const API = '/api/v1';
const $ = (id) => document.getElementById(id);
const STORE_KEY = 'eaa.sessions.v1';
const THEME_KEY = 'eaa.theme';
const KEY_STORAGE = 'eaa.apikey';

function getApiKey() {
  try { return localStorage.getItem(KEY_STORAGE) || ''; } catch (_) { return ''; }
}
function setApiKey(value) {
  try {
    if (value) localStorage.setItem(KEY_STORAGE, value);
    else localStorage.removeItem(KEY_STORAGE);
  } catch (_) { /* private mode — ignore */ }
}

/** All API calls go through here so RBAC works from the UI too. */
function apiFetch(path, options) {
  const opts = Object.assign({}, options || {});
  const headers = Object.assign({}, opts.headers || {});
  const key = getApiKey();
  if (key) headers['X-API-Key'] = key;
  opts.headers = headers;
  return fetch(API + path, opts).then((res) => {
    if (res.status === 401 || res.status === 403) {
      toast('Authentication required — set an API key', true);
    }
    return res;
  });
}

const state = {
  sessions: [],
  activeId: null,
  running: false,
  controller: null,
  health: null,
  lastChunks: [],
};

/* ---------------------------------------------------------------- icons */

const ICONS = {
  bot: '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3l7 4v10l-7 4-7-4V7z"/><path d="M12 12l7-4M12 12v9M12 12L5 8"/></svg>',
  copy: '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15V5a2 2 0 012-2h10"/></svg>',
  check: '<svg viewBox="0 0 24 24" width="11" height="11" fill="none" stroke="currentColor" stroke-width="3.2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6L9 17l-5-5"/></svg>',
  trash: '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6"/></svg>',
  doc: '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><path d="M14 2v6h6"/></svg>',
  sun: '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>',
  moon: '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.8A9 9 0 1111.2 3a7 7 0 009.8 9.8z"/></svg>',
  search: '<svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/></svg>',
  chart: '<svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 3v18h18"/><path d="M7 15l4-5 3 3 5-7"/></svg>',
  bolt: '<svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M13 2L4 14h7l-1 8 9-12h-7z"/></svg>',
  chat: '<svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 01-2 2H8l-5 4V5a2 2 0 012-2h14a2 2 0 012 2z"/></svg>',
  retry: '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12a9 9 0 11-3-6.7L21 8"/><path d="M21 3v5h-5"/></svg>',
  shield: '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3l7 4v5c0 4.5-3 7.7-7 9-4-1.3-7-4.5-7-9V7z"/><path d="M9 12l2 2 4-4"/></svg>',
};

const AGENT_INFO = {
  orchestrator: { label: 'Orchestrator', desc: 'Reads your request, classifies intent and decides which specialists to involve.' },
  research: { label: 'Research', desc: 'Searches the knowledge base with vector similarity and writes a grounded, cited answer.' },
  analyst: { label: 'Analyst', desc: 'Turns retrieved findings into comparisons, insights and recommendations.' },
  execution: { label: 'Execution', desc: 'Calls allow-listed APIs and enterprise tools. Write operations pause for human approval.' },
  synthesis: { label: 'Compose', desc: 'Combines every agent result into one clear response.' },
  review: { label: 'Review', desc: 'Checks the draft answer against retrieved evidence and flags unsupported claims.' },
};

/* ---------------------------------------------------------------- utils */

function escapeHtml(text) {
  const amp = '&' + 'amp;', lt = '&' + 'lt;', gt = '&' + 'gt;', quot = '&' + 'quot;', apos = '&' + '#39;';
  const map = { '&': amp, '<': lt, '>': gt, '"': quot, "'": apos };
  return String(text == null ? '' : text).replace(/[&<>"']/g, (c) => map[c]);
}

function uid() {
  return 'sess-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 8);
}

function timeLabel(ts) {
  try {
    return new Date(ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  } catch (_) { return ''; }
}

function toast(message, isError) {
  const el = document.createElement('div');
  el.className = 'toast' + (isError ? ' err' : '');
  el.textContent = message;
  $('toasts').appendChild(el);
  setTimeout(() => el.remove(), 2600);
}

/* ---------------------------------------------------------------- markdown */

function renderMarkdown(raw) {
  let text = escapeHtml(raw);

  // fenced code blocks first, protected from inline rules
  const blocks = [];
  text = text.replace(/```([\w+-]*)\n?([\s\S]*?)```/g, (_, lang, code) => {
    blocks.push('<pre><code>' + code.replace(/\n$/, '') + '</code></pre>');
    return '\u0000BLOCK' + (blocks.length - 1) + '\u0000';
  });

  text = text.replace(/`([^`\n]+)`/g, '<code>$1</code>');
  text = text.replace(/\*\*\*([^*]+)\*\*\*/g, '<strong><em>$1</em></strong>');
  text = text.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  text = text.replace(/(^|[^*\w])\*([^*\n]+)\*/g, '$1<em>$2</em>');
  text = text.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');

  // citations: [source: file.md] -> clickable chip
  text = text.replace(/\[source:\s*([^\]]+)\]/gi, (_, name) => {
    const clean = name.trim();
    return '<button class="cite" type="button" data-source="' + clean + '">' + ICONS.doc + clean + '</button>';
  });

  const lines = text.split('\n');
  const out = [];
  let listType = null;
  let inTable = false;

  const closeList = () => { if (listType) { out.push(listType === 'ul' ? '</ul>' : '</ol>'); listType = null; } };
  const closeTable = () => { if (inTable) { out.push('</tbody></table>'); inTable = false; } };

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const trimmed = line.trim();

    if (/^\u0000BLOCK\d+\u0000$/.test(trimmed)) { closeList(); closeTable(); out.push(trimmed); continue; }

    // tables
    if (/^\|.*\|$/.test(trimmed)) {
      const cells = trimmed.slice(1, -1).split('|').map((c) => c.trim());
      const isDivider = cells.every((c) => /^:?-{2,}:?$/.test(c));
      if (isDivider) continue;
      closeList();
      if (!inTable) {
        const next = (lines[i + 1] || '').trim();
        const headerIsNext = /^\|[\s:|-]+\|$/.test(next);
        out.push('<table>');
        if (headerIsNext) {
          out.push('<thead><tr>' + cells.map((c) => '<th>' + c + '</th>').join('') + '</tr></thead><tbody>');
          inTable = true;
          continue;
        }
        out.push('<tbody>');
        inTable = true;
      }
      out.push('<tr>' + cells.map((c) => '<td>' + c + '</td>').join('') + '</tr>');
      continue;
    }
    closeTable();

    const heading = trimmed.match(/^(#{1,4})\s+(.*)$/);
    if (heading) {
      closeList();
      const level = Math.min(heading[1].length + 1, 4);
      out.push('<h' + level + '>' + heading[2] + '</h' + level + '>');
      continue;
    }
    if (/^(-{3,}|\*{3,}|_{3,})$/.test(trimmed)) { closeList(); out.push('<hr>'); continue; }
    if (/^>\s?/.test(trimmed)) { closeList(); out.push('<blockquote>' + trimmed.replace(/^>\s?/, '') + '</blockquote>'); continue; }

    const bullet = trimmed.match(/^[*\-+]\s+(.*)$/);
    const numbered = trimmed.match(/^\d+[.)]\s+(.*)$/);
    if (bullet || numbered) {
      const want = bullet ? 'ul' : 'ol';
      if (listType !== want) { closeList(); out.push(want === 'ul' ? '<ul>' : '<ol>'); listType = want; }
      out.push('<li>' + (bullet ? bullet[1] : numbered[1]) + '</li>');
      continue;
    }

    closeList();
    if (trimmed === '') continue;
    out.push('<p>' + line + '</p>');
  }
  closeList(); closeTable();

  let html = out.join('');
  html = html.replace(/\u0000BLOCK(\d+)\u0000/g, (_, n) => blocks[Number(n)] || '');
  return html;
}

/* ---------------------------------------------------------------- storage */

function loadSessions() {
  try {
    const raw = localStorage.getItem(STORE_KEY);
    state.sessions = raw ? JSON.parse(raw) : [];
    if (!Array.isArray(state.sessions)) state.sessions = [];
  } catch (_) { state.sessions = []; }
}

function saveSessions() {
  try {
    const trimmed = state.sessions.slice(0, 40).map((s) => ({
      ...s, messages: (s.messages || []).slice(-60),
    }));
    localStorage.setItem(STORE_KEY, JSON.stringify(trimmed));
  } catch (_) { /* quota — ignore */ }
}

function activeSession() {
  return state.sessions.find((s) => s.id === state.activeId) || null;
}

function newSession(activate) {
  const s = { id: uid(), title: 'New conversation', createdAt: Date.now(), messages: [] };
  state.sessions.unshift(s);
  if (activate !== false) { state.activeId = s.id; renderAll(); }
  saveSessions();
  return s;
}

function switchSession(id) {
  if (state.running) { toast('Wait for the current answer to finish'); return; }
  state.activeId = id;
  renderAll();
  closeSidebar();
}

function deleteSession(id) {
  state.sessions = state.sessions.filter((s) => s.id !== id);
apiFetch('/sessions/' + encodeURIComponent(id), { method: 'DELETE' }).catch(() => {});
  if (state.activeId === id) {
    state.activeId = state.sessions.length ? state.sessions[0].id : null;
    if (!state.activeId) newSession(false);
    state.activeId = state.activeId || state.sessions[0].id;
  }
  saveSessions();
  renderAll();
}

/* ---------------------------------------------------------------- rendering */

function renderSessionList() {
  const list = $('conversation-list');
  list.innerHTML = '';
  if (!state.sessions.length) {
    const li = document.createElement('li');
    li.className = 'empty-note';
    li.textContent = 'No conversations yet.';
    list.appendChild(li);
    return;
  }
  state.sessions.slice(0, 30).forEach((s) => {
    const li = document.createElement('li');
    li.className = 'conversation-item' + (s.id === state.activeId ? ' active' : '');
    li.tabIndex = 0;
    li.setAttribute('role', 'button');

    const title = document.createElement('span');
    title.className = 'ci-title';
    title.textContent = s.title || 'New conversation';
    li.appendChild(title);

    const del = document.createElement('button');
    del.className = 'ci-del';
    del.type = 'button';
    del.title = 'Delete conversation';
    del.innerHTML = ICONS.trash;
    del.addEventListener('click', (e) => { e.stopPropagation(); deleteSession(s.id); });
    li.appendChild(del);

    li.addEventListener('click', () => switchSession(s.id));
    li.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); switchSession(s.id); }
    });
    list.appendChild(li);
  });
}

function heroNode() {
  const wrap = document.createElement('div');
  wrap.className = 'hero';
  wrap.innerHTML =
    '<div class="hero-mark">' + ICONS.bot + '</div>' +
    '<h2>How can I help today?</h2>' +
    '<p>I search your knowledge base, analyse what I find, and cite every source. Pick a starting point or ask your own question.</p>';

  const grid = document.createElement('div');
  grid.className = 'suggestions';
  const items = [
    { icon: ICONS.search, title: 'Company leave policy', sub: 'Entitlements, notice period and carry-forward rules', q: 'What is the company leave policy?' },
    { icon: ICONS.chart, title: 'Compare our products', sub: 'Recommend the best fit for a healthcare customer', q: 'Analyze the products and recommend one for healthcare' },
    { icon: ICONS.doc, title: 'Expense reimbursement', sub: 'Limits, approvals and travel allowances', q: 'What are the expense reimbursement rules?' },
    { icon: ICONS.bolt, title: 'Remote work rules', sub: 'How many days, and what approvals are needed', q: 'What is the remote work policy?' },
  ];
  items.forEach((it) => {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'suggestion';
    b.innerHTML = '<span class="s-icon">' + it.icon + '</span><strong>' + escapeHtml(it.title) + '</strong><span>' + escapeHtml(it.sub) + '</span>';
    b.addEventListener('click', () => sendMessage(it.q));
    grid.appendChild(b);
  });
  wrap.appendChild(grid);
  return wrap;
}

function approvalCard(m) {
  const card = document.createElement('div');
  card.className = 'approval-card';

  const p = m.pending || {};
  const isTool = p.kind === 'tool';
  const title = isTool
    ? `Approve tool: ${p.tool || 'action'}`
    : `Approve ${p.method || 'HTTP'} ${p.url || ''}`;
  const argsText = isTool
    ? JSON.stringify(p.args || {}, null, 1)
    : JSON.stringify(p.json_body || p.params || {}, null, 1);

  const head = document.createElement('div');
  head.className = 'approval-head';
  head.innerHTML = ICONS.shield + '<strong>' + escapeHtml(title) + '</strong>' +
    '<span class="chip warn-chip">' + escapeHtml(p.risk || 'write') + '</span>';
  card.appendChild(head);

  const pre = document.createElement('pre');
  pre.className = 'approval-args';
  pre.textContent = argsText.length > 400 ? argsText.slice(0, 400) + '…' : argsText;
  card.appendChild(pre);

  const note = document.createElement('p');
  note.className = 'approval-note';
  note.textContent = 'This action will only run after you approve it.';
  card.appendChild(note);

  const row = document.createElement('div');
  row.className = 'approval-actions';

  const approve = document.createElement('button');
  approve.type = 'button';
  approve.className = 'btn btn-primary btn-sm';
  approve.textContent = 'Approve & run';
  approve.addEventListener('click', async () => {
    approve.disabled = true;
    reject.disabled = true;
    approve.textContent = 'Running…';
    try {
      const r = await apiFetch('/tasks/' + encodeURIComponent(m.taskId) + '/approve', { method: 'POST' });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || 'HTTP ' + r.status);
      m.pendingResolved = 'approved';
      const outcome = d.result || {};
      const summary = outcome.message || outcome.note ||
        (outcome.status === 'ok' ? 'Tool executed successfully.' : 'Action completed.');
      const resultMsg = { role: 'assistant', content: '✅ **Approved & executed**\n\n' + summary, ts: Date.now() };
      const s = activeSession();
      if (s) { s.messages.push(resultMsg); saveSessions(); }
      renderMessages();
      toast('Action approved');
    } catch (e) {
      approve.disabled = false;
      reject.disabled = false;
      approve.textContent = 'Approve & run';
      toast('Approval failed: ' + e.message, true);
    }
  });

  const reject = document.createElement('button');
  reject.type = 'button';
  reject.className = 'btn btn-danger btn-sm';
  reject.textContent = 'Reject';
  reject.addEventListener('click', async () => {
    approve.disabled = true;
    reject.disabled = true;
    try {
      await apiFetch('/tasks/' + encodeURIComponent(m.taskId) + '/reject', { method: 'POST' });
      m.pendingResolved = 'rejected';
      renderMessages();
      toast('Action rejected');
    } catch (e) {
      approve.disabled = false;
      reject.disabled = false;
      toast('Could not reject: ' + e.message, true);
    }
  });

  row.appendChild(approve);
  row.appendChild(reject);
  card.appendChild(row);
  return card;
}

function messageNode(m) {
  const wrap = document.createElement('div');
  wrap.className = 'msg ' + (m.role === 'user' ? 'user' : 'bot') + (m.error ? ' error' : '');

  const av = document.createElement('div');
  av.className = 'avatar ' + (m.role === 'user' ? 'me' : 'bot');
  av.innerHTML = m.role === 'user' ? 'You' : ICONS.bot;
  wrap.appendChild(av);

  const col = document.createElement('div');
  col.className = 'msg-col';

  const head = document.createElement('div');
  head.className = 'msg-head';
  head.innerHTML = '<span class="who">' + (m.role === 'user' ? 'You' : 'Assistant') + '</span><span>' + timeLabel(m.ts) + '</span>';
  col.appendChild(head);

  const bubble = document.createElement('div');
  bubble.className = 'bubble';
  if (m.role === 'user') {
    bubble.textContent = m.content;
  } else {
    const md = document.createElement('div');
    md.className = 'md';
    md.innerHTML = renderMarkdown(m.content);
    bubble.appendChild(md);
  }
  col.appendChild(bubble);

  if (m.role !== 'user') {
    if (m.sources && m.sources.length) {
      const src = document.createElement('div');
      src.className = 'sources';
      src.innerHTML = '<span class="sources-label">Sources</span>';
      m.sources.forEach((name) => {
        const chip = document.createElement('button');
        chip.type = 'button';
        chip.className = 'source-chip';
        chip.innerHTML = ICONS.doc + escapeHtml(name);
        chip.addEventListener('click', () => showSources(m.chunks || [], name));
        src.appendChild(chip);
      });
      col.appendChild(src);
    }

    if (m.meta) {
      const meta = document.createElement('div');
      meta.className = 'meta-line';
      meta.innerHTML = m.meta;
      col.appendChild(meta);
    }

    if (m.pending && !m.pendingResolved) {
      col.appendChild(approvalCard(m));
    } else if (m.pending && m.pendingResolved) {
      const done = document.createElement('div');
      done.className = 'approval-done ' + (m.pendingResolved === 'approved' ? 'ok' : 'bad');
      done.textContent = m.pendingResolved === 'approved'
        ? '✅ Approved and executed'
        : '❎ Rejected — nothing was executed';
      col.appendChild(done);
    }

    const tools = document.createElement('div');
    tools.className = 'msg-tools';
    const copy = document.createElement('button');
    copy.type = 'button';
    copy.className = 'tool-btn';
    copy.innerHTML = ICONS.copy + 'Copy';
    copy.addEventListener('click', async () => {
      try { await navigator.clipboard.writeText(m.content); toast('Copied to clipboard'); }
      catch (_) { toast('Could not copy', true); }
    });
    tools.appendChild(copy);

    if (m.error && m.retryQuery) {
      const retry = document.createElement('button');
      retry.type = 'button';
      retry.className = 'tool-btn';
      retry.innerHTML = ICONS.retry + 'Retry';
      retry.addEventListener('click', () => sendMessage(m.retryQuery));
      tools.appendChild(retry);
    }
    col.appendChild(tools);
  }

  wrap.appendChild(col);
  return wrap;
}

function renderMessages() {
  const box = $('messages');
  box.innerHTML = '';
  const s = activeSession();
  if (!s || !s.messages.length) {
    box.appendChild(heroNode());
  } else {
    s.messages.forEach((m) => box.appendChild(messageNode(m)));
  }
  const title = s && s.title ? s.title : 'New conversation';
  $('chat-title').textContent = title;
  $('chat-subtitle').textContent = s && s.messages.length
    ? s.messages.length + ' messages in this conversation'
    : 'Ask about company policy, products, or anything in your knowledge base.';
  scrollToEnd(true);
}

function renderAll() { renderSessionList(); renderMessages(); }

/* ---------------------------------------------------------------- scrolling */

function nearBottom() {
  const t = $('thread');
  return t.scrollHeight - t.scrollTop - t.clientHeight < 120;
}
function scrollToEnd(force) {
  const t = $('thread');
  if (force || nearBottom()) {
    requestAnimationFrame(() => { t.scrollTop = t.scrollHeight; });
    $('scroll-btn').hidden = true;
  } else {
    $('scroll-btn').hidden = false;
  }
}

/* ---------------------------------------------------------------- pipeline UI */

function buildPipeline() {
  const card = document.createElement('div');
  card.className = 'pipeline';
  card.innerHTML =
    '<div class="pipeline-head"><span class="pipeline-title">Agent pipeline</span><span class="pipeline-time">0s</span></div>' +
    '<div class="steps"></div>';
  return card;
}

function setSteps(card, names) {
  const steps = card.querySelector('.steps');
  // Preserve state of steps that already exist (setSteps may rebuild the list
  // when a late agent like `review` joins the pipeline).
  const previous = {};
  steps.querySelectorAll('.step').forEach((row) => {
    previous[row.dataset.agent] = {
      cls: row.className,
      note: row.querySelector('.step-note').textContent,
    };
  });
  steps.innerHTML = '';
  names.forEach((n) => {
    const info = AGENT_INFO[n] || { label: n };
    const row = document.createElement('div');
    row.className = 'step';
    row.dataset.agent = n;
    row.innerHTML = '<span class="step-icon"></span><span class="step-label">' + escapeHtml(info.label) + '</span><span class="step-note"></span>';
    const prev = previous[n];
    if (prev) {
      row.className = prev.cls;
      row.querySelector('.step-note').textContent = prev.note;
      if (prev.cls.includes('done')) {
        row.querySelector('.step-icon').innerHTML = ICONS.check;
      }
    }
    steps.appendChild(row);
  });
}

function markStep(card, agent, status, note) {
  const row = card.querySelector('.step[data-agent="' + agent + '"]');
  if (!row) return;
  row.classList.remove('running', 'done');
  if (status) row.classList.add(status);
  const icon = row.querySelector('.step-icon');
  icon.innerHTML = status === 'done' ? ICONS.check : '';
  if (note) row.querySelector('.step-note').textContent = note;
}

/* ---------------------------------------------------------------- chat */

function setRunning(on) {
  state.running = on;
  const btn = $('send');
  btn.classList.toggle('stop', on);
  $('send-label').textContent = on ? 'Stop' : 'Send';
  $('send-icon').innerHTML = on
    ? '<rect x="6" y="6" width="12" height="12" rx="2" fill="currentColor" stroke="none"/>'
    : '<path d="M5 12h14M13 6l6 6-6 6"/>';
  btn.title = on ? 'Stop generating' : 'Send message';
}

async function sendMessage(text) {
  const query = (text || '').trim();
  if (!query || state.running) return;

  let s = activeSession();
  if (!s) s = newSession();

  if (!s.messages.length) {
    s.title = query.length > 42 ? query.slice(0, 42) + '…' : query;
  }

  const userMsg = { role: 'user', content: query, ts: Date.now() };
  s.messages.push(userMsg);
  saveSessions();
  renderSessionList();

  const box = $('messages');
  const hero = box.querySelector('.hero');
  if (hero) hero.remove();
  box.appendChild(messageNode(userMsg));

  $('input').value = '';
  autoGrow();
  updateCounter();
  setRunning(true);
  scrollToEnd(true);

  // live bot placeholder
  const wrap = document.createElement('div');
  wrap.className = 'msg bot';
  wrap.innerHTML = '<div class="avatar bot">' + ICONS.bot + '</div>';
  const col = document.createElement('div');
  col.className = 'msg-col';
  const head = document.createElement('div');
  head.className = 'msg-head';
  head.innerHTML = '<span class="who">Assistant</span><span>working…</span>';
  col.appendChild(head);
  const pipeline = buildPipeline();
  setSteps(pipeline, ['orchestrator']);
  markStep(pipeline, 'orchestrator', 'running');
  col.appendChild(pipeline);
  wrap.appendChild(col);
  box.appendChild(wrap);
  scrollToEnd(true);

  const started = Date.now();
  const timer = setInterval(() => {
    const el = pipeline.querySelector('.pipeline-time');
    if (el) el.textContent = Math.round((Date.now() - started) / 1000) + 's';
  }, 1000);

  const controller = new AbortController();
  state.controller = controller;

  let planned = ['orchestrator'];
  let chunks = [];
  let plan = null;
  let review = null;
  let finished = false;

  const finish = (msg) => {
    clearInterval(timer);
    setRunning(false);
    state.controller = null;
    wrap.remove();
    const secs = Math.round((Date.now() - started) / 1000);
    const record = { role: 'assistant', content: msg.content, ts: Date.now(), error: !!msg.error, retryQuery: msg.error ? query : null };

    if (!msg.error) {
      const sources = [];
      chunks.forEach((c) => { if (c.source && sources.indexOf(c.source) === -1) sources.push(c.source); });
      record.sources = sources;
      record.chunks = chunks;
      const bits = [];
      if (plan && plan.intent) bits.push('intent <b>' + escapeHtml(plan.intent) + '</b>');
      if (planned.length > 1) bits.push('pipeline <b>' + escapeHtml(planned.filter((p) => p !== 'synthesis').join(' → ')) + '</b>');
      if (review) {
        bits.push('review <b>' + (review.verdict === 'pass' ? 'approved' : 'revised') + '</b>');
      }
      bits.push('<b>' + secs + 's</b>');
      record.meta = bits.join(' · ');
      if (msg.pending) {
        record.pending = msg.pending;
        record.taskId = msg.taskId;
      }
    }
    s.messages.push(record);
    saveSessions();
    box.appendChild(messageNode(record));
    scrollToEnd();
    renderMessages();
    loadKbCount();
  };

  try {
    const res = await apiFetch('/chat/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Accept': 'text/event-stream' },
      body: JSON.stringify({ message: query, session_id: s.id }),
      signal: controller.signal,
    });
    if (!res.ok || !res.body) throw new Error('Server responded ' + res.status);

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const parts = buffer.split('\n\n');
      buffer = parts.pop() || '';

      for (const part of parts) {
        let event = 'message';
        let data = '';
        part.split('\n').forEach((line) => {
          if (line.startsWith('event:')) event = line.slice(6).trim();
          else if (line.startsWith('data:')) data += line.slice(5).trim();
        });
        if (!data) continue;
        let payload;
        try { payload = JSON.parse(data); } catch (_) { continue; }

        if (event === 'plan') {
          plan = payload.plan || {};
          const agents = Array.isArray(plan.agents) ? plan.agents.slice() : [];
          planned = ['orchestrator'].concat(agents);
          if (agents.length) planned.push('synthesis');
          setSteps(pipeline, planned);
          markStep(pipeline, 'orchestrator', 'done', plan.intent || '');
          scrollToEnd();
        } else if (event === 'agent_start') {
          if (!planned.includes(payload.agent)) {
            planned.push(payload.agent);
            setSteps(pipeline, planned);
            markStep(pipeline, 'orchestrator', 'done', plan ? plan.intent : '');
          }
          markStep(pipeline, payload.agent, 'running');
          head.querySelector('span:last-child').textContent = (payload.label || payload.agent) + '…';
          scrollToEnd();
        } else if (event === 'agent_done') {
          let note = '';
          if (payload.chunk_count != null) note = payload.chunk_count + (payload.chunk_count === 1 ? ' chunk' : ' chunks');
          else if (payload.agent === 'review') note = payload.verdict === 'pass' ? 'approved' : (payload.issues || 0) + ' issue(s)';
          else if (payload.status) note = payload.status;
          if (payload.duration_ms != null) {
            const dur = (payload.duration_ms / 1000).toFixed(1) + 's';
            note = note ? note + ' · ' + dur : dur;
          }
          markStep(pipeline, payload.agent, 'done', note);
        } else if (event === 'final') {
          finished = true;
          (payload.agent_results || []).forEach((r) => {
            if (r.agent === 'research' && r.data && Array.isArray(r.data.chunks)) chunks = r.data.chunks;
            if (r.agent === 'review') review = r.data;
          });
          finish({
            content: payload.final_response || 'No response was produced.',
            pending: payload.pending_action || null,
            taskId: payload.task_id,
          });
        } else if (event === 'error') {
          finished = true;
          finish({ content: 'Something went wrong: ' + payload.message, error: true });
        }
      }
    }
    if (!finished) finish({ content: 'The connection closed before an answer arrived.', error: true });
  } catch (err) {
    if (err.name === 'AbortError') {
      finish({ content: 'Generation stopped.', error: true });
    } else {
      finish({ content: 'Could not reach the assistant: ' + err.message, error: true });
    }
  }
}

function stopGeneration() {
  if (state.controller) { state.controller.abort(); toast('Stopped'); }
}

/* ---------------------------------------------------------------- modals */

let lastFocus = null;

function openModal(title, node) {
  lastFocus = document.activeElement;
  $('modal-title').textContent = title;
  const body = $('modal-body');
  body.innerHTML = '';
  body.appendChild(node);
  $('modal-root').hidden = false;
  $('modal-close').focus();
}

function closeModal() {
  $('modal-root').hidden = true;
  $('modal-body').innerHTML = '';
  if (lastFocus && lastFocus.focus) lastFocus.focus();
}

function showSources(chunks, filter) {
  const node = document.createElement('div');
  node.style.display = 'flex';
  node.style.flexDirection = 'column';
  node.style.gap = '12px';
  const list = (chunks || []).filter((c) => !filter || c.source === filter);
  if (!list.length) {
    node.innerHTML = '<p class="empty-note">No stored excerpts for this source.</p>';
  } else {
    list.forEach((c) => {
      const card = document.createElement('div');
      card.className = 'chunk';
      const score = typeof c.score === 'number' ? (c.score * 100).toFixed(0) + '% match' : '';
      card.innerHTML =
        '<div class="chunk-head"><span>' + escapeHtml(c.source || '') + '</span><span>' + score + '</span></div>' +
        '<div class="chunk-text">' + escapeHtml(c.text || '') + '</div>';
      node.appendChild(card);
    });
  }
  openModal(filter ? 'Source · ' + filter : 'Sources', node);
}

async function openKnowledgeBase() {
  const node = document.createElement('div');
  node.style.display = 'flex';
  node.style.flexDirection = 'column';
  node.style.gap = '16px';
  node.innerHTML = '<div class="skeleton-line" style="width:60%"></div><div class="skeleton-line"></div><div class="skeleton-line" style="width:80%"></div>';
  openModal('Knowledge base', node);

  let data;
  try {
    const r = await apiFetch('/kb/documents');
    data = await r.json();
  } catch (_) {
    node.innerHTML = '<p class="empty-note">Could not load the knowledge base.</p>';
    return;
  }

  node.innerHTML = '';
  $('kb-count').textContent = String(data.count || 0);

  const add = document.createElement('div');
  add.className = 'field';
  add.innerHTML =
    '<label for="kb-source">Add a document</label>' +
    '<input id="kb-source" placeholder="Document name, e.g. security-policy.md" maxlength="120">' +
    '<textarea id="kb-text" placeholder="Paste the document text here…"></textarea>';
  const addBtn = document.createElement('button');
  addBtn.className = 'btn btn-primary btn-sm';
  addBtn.type = 'button';
  addBtn.textContent = 'Add to knowledge base';
  addBtn.style.alignSelf = 'flex-start';
  add.appendChild(addBtn);
  node.appendChild(add);

  addBtn.addEventListener('click', async () => {
    const text = $('kb-text').value.trim();
    const source = $('kb-source').value.trim() || 'pasted-document.md';
    if (!text) { toast('Paste some text first', true); return; }
    addBtn.disabled = true;
    addBtn.textContent = 'Indexing…';
    try {
      const r = await apiFetch('/ingest', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text, source }),
      });
      if (!r.ok) throw new Error('HTTP ' + r.status);
      const d = await r.json();
      toast('Indexed ' + d.chunks_ingested + ' chunks');
      openKnowledgeBase();
    } catch (e) {
      toast('Ingest failed: ' + e.message, true);
      addBtn.disabled = false;
      addBtn.textContent = 'Add to knowledge base';
    }
  });

  const divider = document.createElement('div');
  divider.className = 'section-title';
  divider.textContent = data.available ? (data.count + ' documents indexed') : 'Vector store offline — using keyword fallback';
  node.appendChild(divider);

  (data.documents || []).forEach((doc) => {
    const row = document.createElement('div');
    row.className = 'doc-row';
    row.innerHTML =
      '<div class="doc-info"><div class="doc-name">' + ICONS.doc + escapeHtml(doc.source) + '</div>' +
      '<div class="doc-meta">' + doc.chunks + (doc.chunks === 1 ? ' chunk' : ' chunks') + ' · ' + doc.characters.toLocaleString() + ' characters</div>' +
      '<div class="doc-preview">' + escapeHtml(doc.preview || '') + '…</div></div>';
    const del = document.createElement('button');
    del.className = 'btn btn-danger btn-sm';
    del.type = 'button';
    del.textContent = 'Remove';
    del.addEventListener('click', async () => {
      del.disabled = true;
      try {
        const r = await apiFetch('/kb/documents/' + encodeURIComponent(doc.doc_id), { method: 'DELETE' });
        if (!r.ok) throw new Error('HTTP ' + r.status);
        toast('Removed ' + doc.source);
        openKnowledgeBase();
      } catch (e) { toast('Remove failed: ' + e.message, true); del.disabled = false; }
    });
    row.appendChild(del);
    node.appendChild(row);
  });

  if (!(data.documents || []).length) {
    const p = document.createElement('p');
    p.className = 'empty-note';
    p.textContent = 'Nothing indexed yet. Add a document above to get started.';
    node.appendChild(p);
  }
}

function openApiKeyModal() {
  const node = document.createElement('div');
  node.style.display = 'flex';
  node.style.flexDirection = 'column';
  node.style.gap = '14px';

  const info = document.createElement('p');
  info.className = 'approval-note';
  info.textContent = 'If your deployment has AUTH_ENABLED=true, enter an API key with the user or admin role. Stored only in this browser.';
  node.appendChild(info);

  const field = document.createElement('div');
  field.className = 'field';
  field.innerHTML = '<label for="api-key-input">X-API-Key</label>' +
    '<input id="api-key-input" type="password" autocomplete="off" placeholder="Leave blank to disable" value="' +
    escapeHtml(getApiKey()) + '">';
  node.appendChild(field);

  const row = document.createElement('div');
  row.className = 'approval-actions';
  const save = document.createElement('button');
  save.type = 'button';
  save.className = 'btn btn-primary btn-sm';
  save.textContent = 'Save key';
  save.addEventListener('click', () => {
    setApiKey($('api-key-input').value.trim());
    closeModal();
    toast(getApiKey() ? 'API key saved' : 'API key cleared');
    loadHealth();
  });
  row.appendChild(save);

  const clear = document.createElement('button');
  clear.type = 'button';
  clear.className = 'btn btn-ghost btn-sm';
  clear.textContent = 'Clear';
  clear.addEventListener('click', () => {
    $('api-key-input').value = '';
    setApiKey('');
    toast('API key cleared');
  });
  row.appendChild(clear);
  node.appendChild(row);

  openModal('Authentication', node);
  $('api-key-input').focus();
}

function openAgents() {
  const node = document.createElement('div');
  node.style.display = 'flex';
  node.style.flexDirection = 'column';
  node.style.gap = '12px';
  Object.keys(AGENT_INFO).forEach((key) => {
    const info = AGENT_INFO[key];
    const card = document.createElement('div');
    card.className = 'agent-card';
    card.innerHTML =
      '<div class="a-icon">' + ICONS.bot + '</div>' +
      '<div><h3>' + escapeHtml(info.label) + '</h3><p>' + escapeHtml(info.desc) + '</p></div>';
    node.appendChild(card);
  });
  openModal('Agents in this workspace', node);
}

/* ---------------------------------------------------------------- health */

async function loadHealth() {
  try {
    const r = await apiFetch('/health');
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const h = await r.json();
    state.health = h;
    $('health-dot').className = 'dot ok';
    $('health-label').textContent = 'All systems operational';
    $('model-pill').textContent = h.llm_provider;
    $('status-grid').innerHTML =
      '<span class="chip accent">' + escapeHtml(h.llm_provider) + '</span>' +
      '<span class="chip">' + escapeHtml(h.vector_store) + '</span>' +
      '<span class="chip">' + escapeHtml(h.memory_backend) + '</span>';
  } catch (_) {
    $('health-dot').className = 'dot bad';
    $('health-label').textContent = 'Backend unreachable';
    $('model-pill').textContent = 'offline';
    $('status-grid').innerHTML = '<span class="chip">retrying…</span>';
    setTimeout(loadHealth, 8000);
  }
}

async function loadKbCount() {
  try {
    const r = await apiFetch('/kb/documents');
    const d = await r.json();
    // A store that is still warming up reports unavailable; show a dash
    // rather than a misleading zero, and retry shortly.
    if (!d.available) {
      $('kb-count').textContent = '–';
      setTimeout(loadKbCount, 4000);
      return;
    }
    $('kb-count').textContent = String(d.count || 0);
  } catch (_) {
    $('kb-count').textContent = '–';
    setTimeout(loadKbCount, 8000);
  }
}

/* ---------------------------------------------------------------- theme */

function applyTheme(theme) {
  document.documentElement.setAttribute('data-theme', theme);
  $('theme-icon').innerHTML = theme === 'dark' ? ICONS.sun : ICONS.moon;
  $('theme-label').textContent = theme === 'dark' ? 'Light mode' : 'Dark mode';
  try { localStorage.setItem(THEME_KEY, theme); } catch (_) {}
}

function initTheme() {
  let saved = null;
  try { saved = localStorage.getItem(THEME_KEY); } catch (_) {}
  if (!saved) saved = window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  applyTheme(saved);
}

/* ---------------------------------------------------------------- sidebar */

function openSidebar() { $('sidebar').classList.add('open'); $('scrim').hidden = false; }
function closeSidebar() { $('sidebar').classList.remove('open'); $('scrim').hidden = true; }

/* ---------------------------------------------------------------- composer */

function autoGrow() {
  const el = $('input');
  el.style.height = 'auto';
  el.style.height = Math.min(el.scrollHeight, 190) + 'px';
}
function updateCounter() {
  const n = $('input').value.length;
  const c = $('counter');
  c.textContent = n + ' / 4000';
  c.classList.toggle('warn', n > 3600);
}

/* ---------------------------------------------------------------- events */

function bindEvents() {
  $('composer').addEventListener('submit', (e) => {
    e.preventDefault();
    if (state.running) { stopGeneration(); return; }
    sendMessage($('input').value);
  });

  $('input').addEventListener('input', () => { autoGrow(); updateCounter(); });
  $('input').addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      if (!state.running) sendMessage($('input').value);
    }
  });

  $('new-chat').addEventListener('click', () => {
    if (state.running) { toast('Wait for the current answer to finish'); return; }
    newSession();
    closeSidebar();
    $('input').focus();
  });

  $('clear-chat').addEventListener('click', () => {
    const s = activeSession();
    if (!s || !s.messages.length) return;
    if (state.running) { toast('Wait for the current answer to finish'); return; }
    s.messages = [];
    s.title = 'New conversation';
    apiFetch('/sessions/' + encodeURIComponent(s.id), { method: 'DELETE' }).catch(() => {});
    saveSessions();
    renderAll();
    toast('Conversation cleared');
  });

  $('open-kb').addEventListener('click', () => { closeSidebar(); openKnowledgeBase(); });
  $('open-agents').addEventListener('click', () => { closeSidebar(); openAgents(); });
  $('open-key').addEventListener('click', openApiKeyModal);
  $('theme-toggle').addEventListener('click', () => {
    const next = document.documentElement.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
    applyTheme(next);
  });

  $('menu-btn').addEventListener('click', openSidebar);
  $('sidebar-close').addEventListener('click', closeSidebar);
  $('scrim').addEventListener('click', closeSidebar);

  $('modal-close').addEventListener('click', closeModal);
  $('modal-scrim').addEventListener('click', closeModal);

  $('scroll-btn').addEventListener('click', () => scrollToEnd(true));
  $('thread').addEventListener('scroll', () => { $('scroll-btn').hidden = nearBottom(); });

  // citation chips inside rendered answers
  $('messages').addEventListener('click', (e) => {
    const chip = e.target.closest('.cite');
    if (!chip) return;
    const s = activeSession();
    const name = chip.dataset.source;
    const withChunks = (s ? s.messages : []).filter((m) => m.chunks && m.chunks.length);
    const last = withChunks[withChunks.length - 1];
    showSources(last ? last.chunks : [], name);
  });

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      if (!$('modal-root').hidden) closeModal();
      else closeSidebar();
    }
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
      e.preventDefault();
      if (!state.running) { newSession(); $('input').focus(); }
    }
  });
}

/* ---------------------------------------------------------------- boot */

function init() {
  initTheme();
  loadSessions();
  if (!state.sessions.length) newSession(false);
  state.activeId = state.sessions[0].id;
  bindEvents();
  renderAll();
  updateCounter();
  loadHealth();
  loadKbCount();
  $('input').focus();
}

init();
