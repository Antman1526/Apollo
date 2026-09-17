// static/js/systemPulse.js — Always-visible sidebar "system pulse" strip.
// Summarizes /api/system/status (admin-only) into a single dot when every
// component is ready, or a row of alert chips for anything that is not.
// renderPulseHTML has no DOM dependency so it is unit tested directly;
// initSystemPulse wires it to a mount element with a synchronous
// placeholder (no layout jump), a de-duped refresh loop (no redundant
// screen-reader announcements), and admin-forbidden handling.

import { escapeStatusHTML } from './systemStatusCard.js';

const MAX_CHIPS = 4;
const SUMMARY_MAX = 48;

function truncate(text, max) {
  const s = String(text || '');
  return s.length > max ? `${s.slice(0, max - 1)}…` : s;
}

function sanitizeStateToken(state) {
  return String(state || 'unknown').replace(/[^a-z0-9_-]/gi, '') || 'unknown';
}

function renderChip(label, state, summary) {
  const esc = escapeStatusHTML;
  const stateClass = sanitizeStateToken(state);
  const fullSummary = esc(summary);
  const shortSummary = esc(truncate(summary, SUMMARY_MAX));
  return `<span class="pulse-chip pulse-chip--${stateClass} pulse-chip--alert" title="${fullSummary}">${esc(label)}<span class="pulse-chip-summary">${shortSummary}</span></span>`;
}

// Every component here has ready:false — idle/limited components stay
// ready:true from the backend, so there is no separate "informational"
// severity: anything surfaced here needs attention.
export function renderPulseHTML(status) {
  if (!status || typeof status !== 'object' || !status.components || typeof status.components !== 'object') {
    return '';
  }
  const entries = Object.entries(status.components);
  const notReady = entries.filter(([, component]) => component && !component.ready);
  if (notReady.length === 0) {
    return '<span class="pulse-dot pulse-dot--ready" title="All systems ready"></span><span class="pulse-text">All systems ready</span>';
  }
  const shown = notReady.slice(0, MAX_CHIPS);
  const extra = notReady.length - shown.length;
  const chips = shown
    .map(([, component]) => renderChip(component.label || '', component.state || 'unknown', component.summary || ''))
    .join('');
  const more = extra > 0 ? `<span class="pulse-more">+${extra}</span>` : '';
  return chips + more;
}

function unavailableStatus() {
  return {
    ok: false,
    ready_count: 0,
    total: 1,
    components: {
      _unavailable: { label: 'Status', ready: false, state: 'unavailable', summary: 'Unreachable' },
    },
  };
}

async function defaultFetchStatus() {
  try {
    const res = await fetch('/api/system/status', { credentials: 'same-origin' });
    // /api/system/status is admin-only (require_admin). A non-admin caller
    // gets 401/403 — that's not an outage, so it renders nothing rather
    // than a permanent "Unreachable" chip.
    if (res.status === 401 || res.status === 403) return { forbidden: true };
    if (!res.ok) return null;
    return await res.json();
  } catch (_) {
    return null;
  }
}

function wrapButton(innerHtml) {
  return `<button type="button" class="system-pulse-btn" aria-label="Open system status">${innerHtml}</button>`;
}

// Skips the DOM write (and the aria-live re-announcement that comes with
// it) when the rendered markup hasn't actually changed since last time.
function paint(state, innerHtml) {
  const html = innerHtml ? wrapButton(innerHtml) : '';
  if (html === state.lastHtml) return;
  state.lastHtml = html;
  state.mountEl.innerHTML = html;
}

async function refresh(state) {
  let status = null;
  try {
    status = await state.fetchStatus();
  } catch (_) {
    status = null;
  }
  state.lastFetchTime = Date.now();
  if (status && status.forbidden) {
    paint(state, '');
    state.stopped = true;
    if (state.timerId) {
      clearInterval(state.timerId);
      state.timerId = null;
    }
    return;
  }
  paint(state, renderPulseHTML(status) || renderPulseHTML(unavailableStatus()));
}

export function initSystemPulse(options = {}) {
  const {
    mountId = 'system-pulse',
    intervalMs = 60000,
    onOpen,
    fetchStatus = defaultFetchStatus,
  } = options;

  const mountEl = document.getElementById(mountId);
  if (!mountEl || mountEl.__pulseInited) return;
  mountEl.__pulseInited = true;

  const state = { mountEl, fetchStatus, lastFetchTime: 0, lastHtml: null, timerId: null, stopped: false };

  // Neutral placeholder so the strip doesn't jump in height once the first
  // real fetch resolves; .system-pulse's min-height covers the rest.
  paint(state, '<span class="pulse-dot pulse-dot--pending" title="Checking…"></span>');

  mountEl.addEventListener('click', (event) => {
    if (event.target.closest('.system-pulse-btn') && typeof onOpen === 'function') onOpen();
  });

  refresh(state);
  state.timerId = setInterval(() => {
    if (!state.stopped && document.visibilityState === 'visible') refresh(state);
  }, intervalMs);

  document.addEventListener('visibilitychange', () => {
    if (!state.stopped && document.visibilityState === 'visible' && Date.now() - state.lastFetchTime >= intervalMs) {
      refresh(state);
    }
  });
}
