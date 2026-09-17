// static/js/briefing.js
// "Today" briefing card on the welcome screen — server-composed from unread
// email, today's calendar events, pinned/due notes, and scheduled tasks due
// today (GET /api/briefing/today). renderBriefingHTML/formatBriefingTime are
// pure (unit tested under Node, tests/test_briefing_render.mjs);
// mountBriefingCard/initBriefing touch the DOM/fetch and run at app load.

const CACHE_TTL_MS = 5 * 60 * 1000;

function escapeHtml(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
}

/** "9:00 AM" for a timed ISO datetime string; "" for a date-only string,
 * missing value, or anything unparsable. */
export function formatBriefingTime(iso) {
  if (!iso || typeof iso !== 'string' || iso.length <= 10) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  return d.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
}

function headerHtml(b) {
  const d = new Date(`${b.date}T00:00:00`);
  const dateLabel = Number.isNaN(d.getTime()) ? escapeHtml(b.date || '')
    : escapeHtml(d.toLocaleDateString(undefined, { weekday: 'long', month: 'long', day: 'numeric' }));
  const counts = b.counts || {};
  const pills = [['emails', 'Mail'], ['events', 'Today'], ['notes', 'Notes'], ['tasks', 'Tasks']]
    .filter(([key]) => counts[key])
    .map(([key, label]) => `<span class="briefing-pill">${counts[key]} ${escapeHtml(label)}</span>`)
    .join('');
  const chevron = '<svg class="briefing-chevron" aria-hidden="true" width="12" height="12" viewBox="0 0 24 24" '
    + 'fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="9 18 15 12 9 6"/></svg>';
  return `<div class="briefing-header">${chevron}<span class="briefing-title">Today · ${dateLabel}</span>`
    + `<span class="briefing-pills">${pills}</span></div>`;
}

function mailSectionHtml(emails) {
  if (!emails || !emails.length) return '';
  const rows = emails.map((e) => `<li class="briefing-row"><span class="briefing-row-primary">${escapeHtml(e.from || '')}</span>`
    + `<span class="briefing-row-secondary">${escapeHtml(e.subject || '')}</span></li>`).join('');
  return `<div class="briefing-section"><span class="briefing-section-label">Mail</span><ul class="briefing-list">${rows}</ul></div>`;
}

function eventsSectionHtml(events) {
  if (!events || !events.length) return '';
  const rows = events.map((ev) => {
    const when = ev.all_day ? 'all day' : formatBriefingTime(ev.dtstart);
    return `<li class="briefing-row"><span class="briefing-row-time">${escapeHtml(when)}</span>`
      + `<span class="briefing-row-primary">${escapeHtml(ev.summary || '')}</span></li>`;
  }).join('');
  return `<div class="briefing-section"><span class="briefing-section-label">Today</span><ul class="briefing-list">${rows}</ul></div>`;
}

function notesSectionHtml(notes) {
  if (!notes || !notes.length) return '';
  const rows = notes.map((n) => {
    const items = (n.open_items || []).map((t) => `<li class="briefing-subitem">${escapeHtml(t)}</li>`).join('');
    const sub = items ? `<ul class="briefing-subitems">${items}</ul>` : '';
    return `<li class="briefing-row"><span class="briefing-row-primary">${escapeHtml(n.title || '')}</span>${sub}</li>`;
  }).join('');
  return `<div class="briefing-section"><span class="briefing-section-label">Notes</span><ul class="briefing-list">${rows}</ul></div>`;
}

function tasksSectionHtml(tasks) {
  if (!tasks || !tasks.length) return '';
  const rows = tasks.map((t) => `<li class="briefing-row"><span class="briefing-row-time">${escapeHtml(formatBriefingTime(t.next_run))}</span>`
    + `<span class="briefing-row-primary">${escapeHtml(t.name || '')}</span></li>`).join('');
  return `<div class="briefing-section"><span class="briefing-section-label">Tasks</span><ul class="briefing-list">${rows}</ul></div>`;
}

/** Pure HTML string builder for the briefing card. `b` is the
 * GET /api/briefing/today response shape: {date, emails, events, notes,
 * tasks, counts, summary, warnings}. Sections render only when non-empty;
 * an all-empty briefing renders a single empty-state line instead. */
export function renderBriefingHTML(b) {
  const briefing = b || {};
  const sections = [
    mailSectionHtml(briefing.emails), eventsSectionHtml(briefing.events),
    notesSectionHtml(briefing.notes), tasksSectionHtml(briefing.tasks),
  ].join('');
  const summaryHtml = briefing.summary ? `<p class="briefing-summary">${escapeHtml(briefing.summary)}</p>` : '';
  const warningsHtml = (briefing.warnings && briefing.warnings.length)
    ? `<div class="briefing-warnings">${escapeHtml(briefing.warnings.join(' · '))}</div>` : '';
  const bodyHtml = sections || '<div class="briefing-empty">Nothing on your plate</div>';
  return `${headerHtml(briefing)}<div class="briefing-body">${summaryHtml}${bodyHtml}${warningsHtml}</div>`;
}

// ── DOM wiring ──────────────────────────────────────────────────────────

let _cache = null; // { at, briefing }

function _isIncognitoActive() {
  const chk = document.getElementById('incognito-toggle');
  if (chk) return !!chk.checked;
  const btn = document.getElementById('incognito-btn');
  return !!(btn && btn.classList.contains('active'));
}

function _isWelcomeVisible() {
  const ws = document.getElementById('welcome-screen');
  if (!ws || ws.classList.contains('hidden')) return false;
  const style = window.getComputedStyle ? window.getComputedStyle(ws) : null;
  return !(style && style.display === 'none');
}

async function _getBriefing(fetchBriefing, { summary = false, force = false } = {}) {
  const now = Date.now();
  if (!force && _cache && (now - _cache.at) < CACHE_TTL_MS && (!summary || _cache.briefing.summary)) {
    return _cache.briefing;
  }
  const briefing = await fetchBriefing(summary);
  _cache = { at: now, briefing };
  return briefing;
}

async function _handleReadAloud(container, fetchBriefing, onReadAloud) {
  const btn = container.querySelector('.briefing-readaloud-btn');
  if (btn) { btn.disabled = true; btn.textContent = 'Reading…'; }
  try {
    let text = _cache && _cache.briefing && _cache.briefing.summary;
    if (!text) {
      const withSummary = await _getBriefing(fetchBriefing, { summary: true, force: true });
      text = withSummary && withSummary.summary;
    }
    if (!text) throw new Error('No summary available to read');
    const res = await fetch('/api/tts/synthesize', {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text, format: 'base64' }),
    });
    if (!res.ok) throw new Error('TTS request failed');
    const data = await res.json();
    if (!data.audio) throw new Error('No audio returned');
    new Audio(`data:audio/wav;base64,${data.audio}`).play();
    if (typeof onReadAloud === 'function') onReadAloud({ ok: true });
  } catch (error) {
    if (typeof onReadAloud === 'function') onReadAloud({ ok: false, error });
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Read aloud'; }
  }
}

function _render(container, briefing, fetchBriefing, onReadAloud) {
  container.innerHTML = renderBriefingHTML(briefing);
  const header = container.querySelector('.briefing-header');
  if (header) {
    header.setAttribute('role', 'button');
    header.setAttribute('tabindex', '0');
    header.setAttribute('aria-expanded', container.classList.contains('welcome-briefing--open') ? 'true' : 'false');
    const toggle = () => {
      const open = container.classList.toggle('welcome-briefing--open');
      header.setAttribute('aria-expanded', open ? 'true' : 'false');
    };
    header.addEventListener('click', toggle);
    header.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggle(); }
    });
  }
  const body = container.querySelector('.briefing-body');
  if (body) {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'briefing-readaloud-btn';
    btn.textContent = 'Read aloud';
    btn.addEventListener('click', (e) => { e.stopPropagation(); _handleReadAloud(container, fetchBriefing, onReadAloud); });
    body.appendChild(btn);
  }
}

/** Mount the briefing card into `#${mountId}`. Fetches lazily (cached 5min)
 * once the welcome screen is visible, and again on every `apollo:welcome`
 * re-show — skipping (and clearing) while Nobody/incognito mode is active.
 * Returns a controller used by initBriefing's palette action to
 * expand + force-refresh on demand. */
export function mountBriefingCard({ mountId = 'welcome-briefing', fetchBriefing, onReadAloud } = {}) {
  const container = document.getElementById(mountId);
  if (!container || typeof fetchBriefing !== 'function') return null;

  async function load(force) {
    if (_isIncognitoActive()) {
      container.innerHTML = '';
      container.classList.remove('welcome-briefing--open');
      return;
    }
    try {
      const briefing = await _getBriefing(fetchBriefing, { force: !!force });
      _render(container, briefing, fetchBriefing, onReadAloud);
    } catch (_) {
      container.innerHTML = '';
    }
  }

  let debounceTimer = null;
  window.addEventListener('apollo:welcome', () => {
    if (debounceTimer) clearTimeout(debounceTimer);
    debounceTimer = setTimeout(() => {
      debounceTimer = null;
      if (_isWelcomeVisible()) load(false);
    }, 150);
  });
  if (_isWelcomeVisible()) load(false);

  return {
    container,
    expand() {
      container.classList.add('welcome-briefing--open');
      const header = container.querySelector('.briefing-header');
      if (header) header.setAttribute('aria-expanded', 'true');
      return load(false);
    },
  };
}

/** Wire the briefing card + the palette action ("Today's briefing"). */
export function initBriefing(deps = {}) {
  const showToast = deps.showToast;
  const card = mountBriefingCard({
    mountId: deps.mountId,
    fetchBriefing: deps.fetchBriefing || (async (summary) => {
      const res = await fetch(`/api/briefing/today?summary=${summary ? 1 : 0}`, { credentials: 'same-origin' });
      if (!res.ok) throw new Error('Briefing request failed');
      return res.json();
    }),
    onReadAloud: deps.onReadAloud,
  });

  window.addEventListener('apollo:palette-action', (event) => {
    if (!event || !event.detail || event.detail.id !== 'briefing') return;
    event.preventDefault();
    if (!_isWelcomeVisible()) {
      if (typeof showToast === 'function') showToast('Open a new chat to see today’s briefing');
      return;
    }
    if (!card) return;
    card.expand();
    if (card.container && typeof card.container.scrollIntoView === 'function') {
      card.container.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
  });
}

export default { renderBriefingHTML, formatBriefingTime, mountBriefingCard, initBriefing };
