// Apollo — homeBrief module.
//
// The empty welcome screen mounts one of two things into #welcome-setup:
//   (a) a live setup checklist while a fresh install still has gaps
//       (no model, no web search, no mailbox), or
//   (b) a compact "home brief" — today's calendar, what's due, inbox
//       counts, the warm local model — once set up.
//
// buildChecklist / buildBrief / buildDayPrompt are pure (no DOM, no
// globals) so they run under node:test; render() and the self-init at the
// bottom are the only parts that touch the document.

const DISMISS_KEY = 'apollo-home-checklist-dismissed';
const UI_VIS_KEY = 'apollo-ui-visibility';
const BRIEF_URL = '/api/home/brief';
const REFRESH_THROTTLE_MS = 15000;
const MAX_ROWS = 6;

// ── Pure builders ──────────────────────────────────────────────────────

const SEARCH_DETAIL = {
  sidecar: 'SearXNG sidecar running',
  fallback: 'Using a fallback search provider',
  off: 'Web search is off',
  unknown: 'Search status unknown',
};

function plural(n, word) {
  return `${n} ${word}${n === 1 ? '' : 's'}`;
}

/**
 * Turn the /api/home/brief `setup` block into checklist rows.
 * @param {object} setup
 * @returns {Array<{key:string,label:string,ready:boolean,detail:string,action:string}>}
 */
export function buildChecklist(setup) {
  const s = setup || {};
  const models = s.models || {};
  const search = s.search || {};
  const email = s.email || {};

  const endpoints = Number(models.endpoints) || 0;
  let modelDetail = endpoints > 0 ? `${plural(endpoints, 'endpoint')} connected` : 'No model connected yet';
  if (models.warm_model) modelDetail += ` · ${models.warm_model} warm`;

  const state = search.state || 'unknown';
  const searchDetail = search.detail || SEARCH_DETAIL[state] || SEARCH_DETAIL.unknown;

  const accounts = Number(email.accounts) || 0;
  const emailDetail = accounts > 0 ? `${plural(accounts, 'mailbox')} connected` : 'No mailbox connected';

  return [
    { key: 'models', label: 'Connect a model', ready: !!models.ready, detail: modelDetail, action: 'setup' },
    { key: 'search', label: 'Web search', ready: !!search.ready, detail: searchDetail, action: 'settings-search' },
    { key: 'email', label: 'Email', ready: !!email.ready, detail: emailDetail, action: 'email-add' },
  ];
}

function parseWhen(value) {
  if (!value) return null;
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? null : d;
}

function fmtTime(d) {
  try {
    return d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
  } catch (_) {
    return `${d.getHours()}:${String(d.getMinutes()).padStart(2, '0')}`;
  }
}

function fmtDay(d) {
  try {
    return d.toLocaleDateString([], { month: 'short', day: 'numeric' });
  } catch (_) {
    return `${d.getMonth() + 1}/${d.getDate()}`;
  }
}

function sameDay(a, b) {
  return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
}

function dueLabel(d, now) {
  if (!d) return '';
  if (d < now) return `Overdue · ${sameDay(d, now) ? fmtTime(d) : fmtDay(d)}`;
  return `Due ${sameDay(d, now) ? fmtTime(d) : fmtDay(d)}`;
}

/**
 * Turn the /api/home/brief `brief` block into display cards.
 * @param {object} brief
 * @param {{now?: Date}} [opts]
 * @returns {{sections: Array<{key:string,title:string,items:Array<{text:string,sub:string}>}>, empty: boolean}}
 */
export function buildBrief(brief, opts = {}) {
  const b = brief || {};
  const now = opts.now instanceof Date ? opts.now : new Date();
  const sections = [];

  const cal = Array.isArray(b.calendar_today) ? b.calendar_today : [];
  if (cal.length) {
    sections.push({
      key: 'today',
      title: 'Today',
      items: cal.slice(0, MAX_ROWS).map((ev) => {
        const start = parseWhen(ev.start);
        const end = parseWhen(ev.end);
        let sub = 'All day';
        if (!ev.all_day && start) {
          sub = fmtTime(start);
          if (end && end > start) sub += ` – ${fmtTime(end)}`;
        }
        if (ev.calendar) sub += ` · ${ev.calendar}`;
        return { text: ev.title || '(untitled)', sub };
      }),
    });
  }

  const due = [];
  for (const t of Array.isArray(b.tasks_due) ? b.tasks_due : []) {
    const when = parseWhen(t.due);
    due.push({ text: t.title || 'Untitled task', when, kind: 'task' });
  }
  for (const n of Array.isArray(b.notes_due) ? b.notes_due : []) {
    const when = parseWhen(n.due_date);
    due.push({ text: n.title || '(untitled)', when, kind: 'note' });
  }
  if (due.length) {
    // Overdue first (oldest overdue at the top), then soonest upcoming.
    due.sort((x, y) => {
      const xo = x.when && x.when < now ? 0 : 1;
      const yo = y.when && y.when < now ? 0 : 1;
      if (xo !== yo) return xo - yo;
      const xt = x.when ? x.when.getTime() : Infinity;
      const yt = y.when ? y.when.getTime() : Infinity;
      return xt - yt;
    });
    sections.push({
      key: 'due',
      title: 'Due',
      items: due.slice(0, MAX_ROWS).map((d) => ({
        text: d.text,
        sub: [dueLabel(d.when, now), d.kind === 'note' ? 'Note' : 'Task'].filter(Boolean).join(' · '),
      })),
    });
  }

  if (b.email && typeof b.email === 'object') {
    const unread = Number(b.email.unread) || 0;
    const urgent = Number(b.email.urgent) || 0;
    if (unread > 0 || urgent > 0) {
      sections.push({
        key: 'inbox',
        title: 'Inbox',
        items: [{
          text: `${plural(unread, 'unread message')}`,
          sub: urgent > 0 ? `${plural(urgent, 'urgent')}` : '',
        }],
      });
    }
  }

  return { sections, empty: sections.length === 0 };
}

/**
 * A ready-to-send prompt summarizing the brief for the composer.
 * @param {object} brief
 * @returns {string}
 */
export function buildDayPrompt(brief) {
  const b = brief || {};
  const lines = ["Here's my day so far:"];
  const cal = Array.isArray(b.calendar_today) ? b.calendar_today : [];
  if (cal.length) {
    lines.push('Calendar:');
    for (const ev of cal.slice(0, MAX_ROWS)) {
      const start = parseWhen(ev.start);
      const when = ev.all_day || !start ? 'all day' : fmtTime(start);
      lines.push(`- ${ev.title || '(untitled)'} (${when})`);
    }
  }
  const tasks = Array.isArray(b.tasks_due) ? b.tasks_due : [];
  const notes = Array.isArray(b.notes_due) ? b.notes_due : [];
  if (tasks.length || notes.length) {
    lines.push('Due:');
    for (const t of tasks.slice(0, MAX_ROWS)) {
      lines.push(`- ${t.title || 'Untitled task'}${t.status === 'overdue' ? ' (overdue)' : ''}`);
    }
    for (const n of notes.slice(0, MAX_ROWS)) {
      lines.push(`- ${n.title || '(untitled)'} (note)`);
    }
  }
  if (b.email && typeof b.email === 'object') {
    const unread = Number(b.email.unread) || 0;
    const urgent = Number(b.email.urgent) || 0;
    lines.push(`Inbox: ${unread} unread${urgent ? `, ${urgent} urgent` : ''}.`);
  }
  if (lines.length === 1) lines.push('Nothing scheduled or due.');
  lines.push('What should I focus on first, and is there anything I should prepare for?');
  return lines.join('\n');
}

// ── DOM rendering ──────────────────────────────────────────────────────

function readDismissed() {
  try {
    return typeof localStorage !== 'undefined' && localStorage.getItem(DISMISS_KEY) === '1';
  } catch (_) {
    return false;
  }
}

function writeDismissed(value) {
  try {
    if (value) localStorage.setItem(DISMISS_KEY, '1');
    else localStorage.removeItem(DISMISS_KEY);
  } catch (_) { /* storage unavailable */ }
}

function briefVisibilityOn() {
  try {
    const raw = typeof localStorage !== 'undefined' ? localStorage.getItem(UI_VIS_KEY) : null;
    if (!raw) return true;
    const state = JSON.parse(raw);
    return !(state && state['welcome-brief'] === false);
  } catch (_) {
    return true;
  }
}

function isIncognito() {
  const chk = document.getElementById('incognito-toggle');
  return !!(chk && chk.checked);
}

function h(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === 'class') node.className = v;
    else if (k === 'text') node.textContent = v;
    else if (k.startsWith('on') && typeof v === 'function') node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v === true ? '' : String(v));
  }
  for (const c of [].concat(children)) {
    if (c == null) continue;
    node.appendChild(typeof c === 'string' ? document.createTextNode(c) : c);
  }
  return node;
}

function openSettingsTab(tab) {
  const mod = typeof window !== 'undefined' ? window.settingsModule : null;
  if (mod && typeof mod.open === 'function') {
    try { mod.open(tab); return; } catch (_) { /* fall through */ }
  }
  const btn = document.getElementById('user-bar-settings');
  if (btn) btn.click();
  const tabBtn = document.querySelector(`[data-settings-tab="${tab}"]`);
  if (tabBtn) tabBtn.click();
}

function triggerEmailAdd() {
  const plus = document.getElementById('email-compose-btn');
  if (plus) { plus.click(); return; }
  const title = document.getElementById('email-section-title');
  if (title) title.click();
}

function fillComposer(text) {
  const input = document.getElementById('message');
  if (!input) return;
  input.value = text;
  input.dispatchEvent(new Event('input', { bubbles: true }));
  input.focus();
}

function actionButton(row) {
  if (row.action === 'setup') {
    // The existing slash-command delegation turns this into `/setup`.
    return h('button', { type: 'button', class: 'home-row-action setup-trigger-link', text: 'Connect' });
  }
  if (row.action === 'settings-search') {
    return h('button', {
      type: 'button', class: 'home-row-action', text: 'Open settings',
      onclick: (e) => { e.preventDefault(); openSettingsTab('search'); },
    });
  }
  if (row.action === 'email-add') {
    return h('button', {
      type: 'button', class: 'home-row-action', text: 'Add mailbox',
      onclick: (e) => { e.preventDefault(); triggerEmailAdd(); },
    });
  }
  return null;
}

function renderChecklist(root, setup, onDismiss) {
  const rows = buildChecklist(setup);
  const list = h('ul', { class: 'home-checklist', role: 'list' }, rows.map((row) => {
    const glyph = h('span', { class: `home-glyph ${row.ready ? 'is-ready' : ''}`, 'aria-hidden': 'true', text: row.ready ? '✓' : '○' });
    const body = h('div', { class: 'home-row-body' }, [
      h('span', { class: 'home-row-label', text: row.label }),
      h('span', { class: 'home-row-detail', text: row.detail }),
    ]);
    const li = h('li', { class: `home-row ${row.ready ? 'is-ready' : 'is-pending'}` }, [glyph, body]);
    li.appendChild(h('span', { class: 'a11y-visually-hidden', text: row.ready ? 'Done' : 'Not set up' }));
    if (!row.ready) {
      const btn = actionButton(row);
      if (btn) li.appendChild(btn);
    }
    return li;
  }));
  const card = h('section', { class: 'home-card home-card-setup', 'aria-label': 'Setup checklist' }, [
    h('h2', { class: 'home-card-title', text: 'Finish setting up' }),
    list,
    h('div', { class: 'home-card-foot' }, [
      h('button', { type: 'button', class: 'home-link', text: 'Dismiss', onclick: (e) => { e.preventDefault(); onDismiss(); } }),
    ]),
  ]);
  root.appendChild(card);
}

function renderBrief(root, brief) {
  const built = buildBrief(brief);
  const wrap = h('div', { class: 'home-brief-cards' });
  if (built.empty) {
    wrap.appendChild(h('p', { class: 'home-quiet', text: 'Nothing due today.' }));
  } else {
    for (const sec of built.sections) {
      wrap.appendChild(h('section', { class: `home-card home-card-${sec.key}`, 'aria-label': sec.title }, [
        h('h2', { class: 'home-card-title', text: sec.title }),
        h('ul', { class: 'home-list', role: 'list' }, sec.items.map((item) => h('li', { class: 'home-item' }, [
          h('span', { class: 'home-item-text', text: item.text }),
          item.sub ? h('span', { class: 'home-item-sub', text: item.sub }) : null,
        ]))),
      ]));
    }
  }
  root.appendChild(wrap);
  const foot = h('div', { class: 'home-brief-foot' }, [
    h('button', {
      type: 'button', class: 'home-ask-btn', text: 'Ask Apollo about my day',
      onclick: (e) => { e.preventDefault(); fillComposer(buildDayPrompt(brief)); },
    }),
    brief && brief.warm_model ? h('span', { class: 'home-warm', text: `Warm model: ${brief.warm_model}` }) : null,
  ]);
  root.appendChild(foot);
}

/**
 * Render checklist-or-brief into `root`.
 * @param {HTMLElement} root
 * @param {object} data  the /api/home/brief payload
 * @param {{incognito?: boolean, dismissed?: boolean, visible?: boolean}} [opts]
 */
export function render(root, data, opts = {}) {
  if (!root) return;
  const incognito = opts.incognito === undefined ? isIncognito() : !!opts.incognito;
  const visible = opts.visible === undefined ? briefVisibilityOn() : !!opts.visible;
  root.replaceChildren();
  if (incognito || !visible || !data) {
    root.hidden = true;
    return;
  }
  const setup = data.setup || {};
  const rows = buildChecklist(setup);
  const allReady = rows.every((r) => r.ready);
  const dismissed = opts.dismissed === undefined ? readDismissed() : !!opts.dismissed;
  const modelsReady = !!(setup.models && setup.models.ready);
  const showBrief = allReady || (modelsReady && dismissed);
  root.hidden = false;
  root.classList.toggle('is-checklist', !showBrief);
  root.classList.toggle('is-brief', showBrief);
  if (showBrief) {
    renderBrief(root, data.brief || {});
  } else {
    renderChecklist(root, setup, () => {
      writeDismissed(true);
      render(root, data, { ...opts, dismissed: true });
    });
  }
}

// ── Self-init ──────────────────────────────────────────────────────────

async function fetchBrief() {
  const res = await fetch(BRIEF_URL, { credentials: 'same-origin', headers: { Accept: 'application/json' } });
  if (!res.ok) throw new Error(`home brief ${res.status}`);
  return res.json();
}

function init() {
  const root = document.getElementById('welcome-setup');
  if (!root) return;
  let last = 0;
  let lastData = null;
  let inflight = null;

  const rerender = () => render(root, lastData);

  const refresh = (force = false) => {
    const cc = document.getElementById('chat-container');
    if (cc && !cc.classList.contains('welcome-active')) return;
    if (isIncognito() || !briefVisibilityOn()) { rerender(); return; }
    const now = Date.now();
    if (!force && now - last < REFRESH_THROTTLE_MS) { rerender(); return; }
    if (inflight) return;
    last = now;
    inflight = fetchBrief()
      .then((data) => { lastData = data; rerender(); })
      .catch((err) => {
        if (window.console && console.debug) console.debug('[homeBrief] fetch failed', err);
        if (!lastData) root.hidden = true;
      })
      .finally(() => { inflight = null; });
  };

  refresh(true);

  const cc = document.getElementById('chat-container');
  if (cc && typeof MutationObserver !== 'undefined') {
    let wasActive = cc.classList.contains('welcome-active');
    new MutationObserver(() => {
      const active = cc.classList.contains('welcome-active');
      if (active && !wasActive) refresh();
      wasActive = active;
    }).observe(cc, { attributes: true, attributeFilter: ['class'] });
  }

  // The Nobody toggle flips a hidden checkbox without firing `change`;
  // re-render after its click handler has run.
  const inc = document.getElementById('incognito-btn');
  if (inc) inc.addEventListener('click', () => setTimeout(rerender, 0));
  const ind = document.getElementById('incognito-indicator');
  if (ind) ind.addEventListener('click', () => setTimeout(rerender, 0));

  // The Home Brief visibility toggle persists to localStorage; other tabs
  // arrive via the storage event, this tab via the checkbox's change event.
  window.addEventListener('storage', (e) => { if (e.key === UI_VIS_KEY) rerender(); });
  document.addEventListener('change', (e) => {
    const t = e.target;
    if (t && t.getAttribute && t.getAttribute('data-ui-key') === 'welcome-brief') setTimeout(rerender, 0);
  });
}

if (typeof document !== 'undefined') {
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
}
