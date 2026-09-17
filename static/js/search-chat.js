// Search Chat Module — Ctrl+K universal command palette.
// Actions, sessions and models are matched locally (paletteItems.js);
// message hits still come from /api/search and render under "Messages".

import uiModule from './ui.js';
import sessionModule from './sessions.js';
import { buildPaletteItems } from './paletteItems.js';

let API_BASE = '';
let deps = {};
let debounceTimer = null;
let selectedIndex = -1;
let rows = [];             // flat, selectable rows in render order
let messageResults = [];   // raw /api/search hits for the current query
let messageQuery = '';     // query the message hits belong to

const MESSAGE_MIN_CHARS = 3;

// Palette actions. `dispatch: true` fires an app event instead of a keybind
// action so later features can subscribe without touching this module.
const PALETTE_ACTIONS = [
  { id: 'new_session', label: 'New chat' },
  { id: 'toggle_sidebar', label: 'Toggle sidebar' },
  { id: 'settings', label: 'Open Settings' },
  { id: 'incognito', label: 'Toggle Nobody mode' },
  { id: 'open_calendar', label: 'Open Calendar' },
  { id: 'open_notes', label: 'Open Notes' },
  { id: 'open_tasks', label: 'Open Tasks' },
  { id: 'open_gallery', label: 'Open Gallery' },
  { id: 'open_memory', label: 'Open Brain' },
  { id: 'open_research', label: 'Open Deep Research' },
  { id: 'open_library', label: 'Open Library' },
  { id: 'open_cookbook', label: 'Open Cookbook' },
  { id: 'open_compare', label: 'Open Compare' },
  { id: 'open_theme', label: 'Open Theme' },
  { id: 'open_browser', label: 'Open Browser' },
  { id: 'council', label: 'Ask the Council', dispatch: true },
  { id: 'briefing', label: "Today's briefing", dispatch: true },
];

function el(id) { return document.getElementById(id); }

var escapeHtml = uiModule.esc;

// ── Palette context ──────────────────────────────────────────────────

// Mirrors welcomeState._isListableSession so the palette lists exactly what
// the sidebar does (that helper is not exported).
function _isListableSession(s) {
  if (!s || s.archived) return false;
  if (s.folder === 'Assistant') return false;
  const name = (s.name || '').trim();
  if (name === 'Nobody' || name === 'Incognito') return false;
  return true;
}

function collectSessions() {
  const get = deps.getSessions || (sessionModule && sessionModule.getSessions);
  let list = [];
  try { list = (typeof get === 'function' ? get() : []) || []; } catch (_) { list = []; }
  return list.filter(_isListableSession);
}

function collectModels() {
  const get = deps.getCachedItems;
  const chatCapable = deps.isChatCapable || (() => true);
  let items = [];
  try { items = (typeof get === 'function' ? get() : []) || []; } catch (_) { items = []; }
  const out = [];
  for (const item of items) {
    if (!item) continue;
    if ((item.model_type || 'llm') === 'image') continue;   // chat palette only
    const endpoint = item.endpoint_name || '';
    const ids = (item.models || []).concat(item.models_extra || []);
    const names = (item.models_display || item.models || [])
      .concat(item.models_extra_display || item.models_extra || []);
    ids.forEach((mid, i) => {
      if (!chatCapable(item, mid)) return;
      out.push({
        id: `${item.endpoint_id || item.url || endpoint}::${mid}`,
        label: names[i] || mid,
        endpoint,
        url: item.url || '',
        modelId: mid,
        endpointId: item.endpoint_id || null,
      });
    });
  }
  return out;
}

function paletteContext() {
  return { actions: PALETTE_ACTIONS, sessions: collectSessions(), models: collectModels() };
}

// ── Rendering ────────────────────────────────────────────────────────

function highlightMatch(text, query) {
  if (!query) return escapeHtml(text);
  const escaped = escapeHtml(text);
  const regex = new RegExp('(' + query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + ')', 'gi');
  return escaped.replace(regex, '<mark class="search-highlight">$1</mark>');
}

function formatTimestamp(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  const now = new Date();
  const diff = now - d;
  if (diff < 86400000) {
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  }
  if (diff < 604800000) {
    return d.toLocaleDateString([], { weekday: 'short', hour: '2-digit', minute: '2-digit' });
  }
  return d.toLocaleDateString([], { month: 'short', day: 'numeric', year: 'numeric' });
}

function groupHeaderHTML(label) {
  return `<div class="palette-group search-group-header">${escapeHtml(label)}</div>`;
}

function paletteRowHTML(row, index) {
  const hint = row.hint ? `<div class="palette-row-hint">${escapeHtml(row.hint)}</div>` : '';
  return `<div class="palette-row" data-row-index="${index}">
    <div class="palette-row-label">${escapeHtml(row.label)}</div>
    ${hint}
  </div>`;
}

function messageRowHTML(item, index, query) {
  const roleLabel = item.role === 'user' ? 'You' : 'AI';
  return `<div class="palette-row search-result-item" data-row-index="${index}">
    <div class="search-result-role">${roleLabel}</div>
    <div class="search-result-snippet">${highlightMatch(item.content_snippet, query)}</div>
    <div class="search-result-time">${formatTimestamp(item.timestamp)}</div>
  </div>`;
}

function render(query) {
  const container = el('search-results');
  if (!container) return;

  const items = buildPaletteItems(query, paletteContext());
  rows = [];
  let html = '';
  let group = null;

  for (const item of items) {
    if (item.group !== group) {
      group = item.group;
      html += groupHeaderHTML(group);
    }
    html += paletteRowHTML(item, rows.length);
    rows.push(item);
  }

  if (query.length >= MESSAGE_MIN_CHARS && messageQuery === query && messageResults.length) {
    html += groupHeaderHTML('Messages');
    for (const hit of messageResults) {
      html += messageRowHTML(hit, rows.length, query);
      rows.push({ kind: 'message', group: 'Messages', id: hit.session_id, label: hit.session_name || '' });
    }
  }

  if (!rows.length) {
    html = query ? '<div class="search-empty">No results found</div>' : '';
  }

  container.innerHTML = html;
  selectedIndex = rows.length ? 0 : -1;
  updateSelection();
}

function updateSelection() {
  const container = el('search-results');
  if (!container) return;
  const nodes = container.querySelectorAll('.palette-row');
  nodes.forEach((node, i) => node.classList.toggle('active', i === selectedIndex));
  if (selectedIndex >= 0 && nodes[selectedIndex]) {
    nodes[selectedIndex].scrollIntoView({ block: 'nearest' });
  }
}

// ── Activation ───────────────────────────────────────────────────────

function navigateToSession(sessionId) {
  closeSearch();
  const select = deps.selectSession || (sessionModule && sessionModule.selectSession);
  if (typeof select === 'function') select(sessionId);
}

function activateRow(row) {
  if (!row) return;
  if (row.kind === 'session' || row.kind === 'message') {
    navigateToSession(row.id);
    return;
  }
  if (row.kind === 'model') {
    closeSearch();
    const create = deps.createDirectChat || (sessionModule && sessionModule.createDirectChat);
    if (typeof create === 'function') create(row.url, row.modelId, row.endpointId);
    return;
  }
  if (row.kind === 'action') {
    const action = PALETTE_ACTIONS.find(a => a.id === row.id);
    closeSearch();
    if (action && action.dispatch) {
      window.dispatchEvent(new CustomEvent('apollo:palette-action', { detail: { id: row.id } }));
      return;
    }
    if (typeof deps.runAction === 'function') deps.runAction(row.id);
  }
}

// ── Public API ───────────────────────────────────────────────────────

export function openSearch() {
  const overlay = el('search-overlay');
  if (!overlay) return;
  overlay.classList.remove('hidden');
  const input = el('search-input');
  if (input) {
    input.value = '';
    input.focus();
  }
  selectedIndex = -1;
  rows = [];
  messageResults = [];
  messageQuery = '';
  render('');
}

export function closeSearch() {
  const overlay = el('search-overlay');
  if (!overlay) return;
  overlay.classList.add('hidden');
  const results = el('search-results');
  if (results) results.innerHTML = '';
  if (debounceTimer) { clearTimeout(debounceTimer); debounceTimer = null; }
  selectedIndex = -1;
  rows = [];
  messageResults = [];
  messageQuery = '';
}

export function isOpen() {
  const overlay = el('search-overlay');
  return overlay && !overlay.classList.contains('hidden');
}

// ── Events ───────────────────────────────────────────────────────────

function handleKeydown(e) {
  if (!isOpen()) return;
  const count = rows.length;

  if (e.key === 'ArrowDown') {
    e.preventDefault();
    selectedIndex = count > 0 ? Math.min(selectedIndex + 1, count - 1) : -1;
    updateSelection();
  } else if (e.key === 'ArrowUp') {
    e.preventDefault();
    selectedIndex = count > 0 ? Math.max(selectedIndex - 1, 0) : -1;
    updateSelection();
  } else if (e.key === 'Enter') {
    e.preventDefault();
    if (selectedIndex >= 0 && rows[selectedIndex]) activateRow(rows[selectedIndex]);
  } else if (e.key === 'Escape') {
    e.preventDefault();
    closeSearch();
  }
}

function handleInput(e) {
  const query = e.target.value.trim();
  if (debounceTimer) { clearTimeout(debounceTimer); debounceTimer = null; }

  if (query.length < MESSAGE_MIN_CHARS) {
    messageResults = [];
    messageQuery = '';
    render(query);
    return;
  }

  render(query);   // local groups update immediately; messages follow
  debounceTimer = setTimeout(async () => {
    try {
      const res = await fetch(`${API_BASE}/api/search?q=${encodeURIComponent(query)}&limit=20`);
      if (!res.ok) return;
      const data = await res.json();
      messageResults = Array.isArray(data) ? data : [];
      messageQuery = query;
      const input = el('search-input');
      if (!isOpen() || !input || input.value.trim() !== query) return;
      render(query);
    } catch (err) {
      console.error('Search error:', err);
    }
  }, 300);
}

/**
 * @param {string} apiBase
 * @param {Object} [injected] - {getSessions, selectSession, getCachedItems,
 *   isChatCapable, createDirectChat, runAction}. Optional: without it the
 *   palette still lists sessions and message hits via the sessions module.
 */
export function init(apiBase, injected) {
  API_BASE = apiBase || '';
  deps = injected || {};

  const input = el('search-input');
  if (input) {
    input.addEventListener('input', handleInput);
    input.addEventListener('keydown', handleKeydown);
  }

  const container = el('search-results');
  if (container) {
    container.addEventListener('click', (e) => {
      const node = e.target.closest ? e.target.closest('.palette-row') : null;
      if (!node) return;
      const idx = Number(node.dataset.rowIndex);
      if (Number.isInteger(idx) && rows[idx]) activateRow(rows[idx]);
    });
    container.addEventListener('mousemove', (e) => {
      const node = e.target.closest ? e.target.closest('.palette-row') : null;
      if (!node) return;
      const idx = Number(node.dataset.rowIndex);
      if (Number.isInteger(idx) && idx !== selectedIndex) {
        selectedIndex = idx;
        updateSelection();
      }
    });
  }

  // Close on overlay click (not popup click)
  const overlay = el('search-overlay');
  if (overlay) {
    overlay.addEventListener('click', (e) => {
      if (e.target === overlay) closeSearch();
    });
  }
}

const searchChatModule = {
  init,
  openSearch,
  closeSearch,
  isOpen,
};

export default searchChatModule;
