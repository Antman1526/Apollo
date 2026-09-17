// static/js/systemPulse.js — Always-visible sidebar "system pulse" strip.
// Summarizes /api/system/status into a single dot (all ready) or a row of
// labelled chips for degraded/idle/stopped components. Pure render helper
// (renderPulseHTML) has no DOM dependency so it can be unit tested directly;
// initSystemPulse wires it to a mount element and a polling refresh loop.

import { escapeStatusHTML } from './systemStatusCard.js';

const INFO_STATES = new Set(['idle', 'stopped', 'limited']);
const MAX_CHIPS = 4;
const SUMMARY_MAX = 48;

function severityForState(state) {
  return INFO_STATES.has(state) ? 'info' : 'alert';
}

function truncate(text, max) {
  const s = String(text || '');
  return s.length > max ? `${s.slice(0, max - 1)}…` : s;
}

function renderChip(label, state, summary) {
  const esc = escapeStatusHTML;
  const severity = severityForState(state);
  const fullSummary = esc(summary);
  const shortSummary = esc(truncate(summary, SUMMARY_MAX));
  const stateClass = esc(state || 'unknown');
  return `<span class="pulse-chip pulse-chip--${stateClass} pulse-chip--${severity}" title="${fullSummary}">${esc(label)}<span class="pulse-chip-summary">${shortSummary}</span></span>`;
}

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
    if (!res.ok) return null;
    return await res.json();
  } catch (_) {
    return null;
  }
}

async function refresh(state) {
  let status = null;
  try {
    status = await state.fetchStatus();
  } catch (_) {
    status = null;
  }
  state.lastFetchTime = Date.now();
  state.mountEl.innerHTML = renderPulseHTML(status) || renderPulseHTML(unavailableStatus());
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

  if (!mountEl.getAttribute('role')) mountEl.setAttribute('role', 'status');
  if (!mountEl.getAttribute('aria-live')) mountEl.setAttribute('aria-live', 'polite');

  const state = { mountEl, fetchStatus, lastFetchTime: 0 };

  mountEl.addEventListener('click', () => {
    if (typeof onOpen === 'function') onOpen();
  });

  refresh(state);
  setInterval(() => {
    if (document.visibilityState === 'visible') refresh(state);
  }, intervalMs);

  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible' && Date.now() - state.lastFetchTime >= intervalMs) {
      refresh(state);
    }
  });
}
