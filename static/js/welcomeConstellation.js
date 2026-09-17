// Live "memory constellation" behind the welcome (empty-chat) screen — a
// dimmed force-directed graph of memory nodes (graphLayout.js) that
// brightens nodes related to composer input. Pure helpers below do no DOM
// work (unit-tested under Node); mountConstellation() wires them to the
// live #welcome-constellation SVG.

import { seedPositions, stepLayout } from './graphLayout.js';

const MAX_NODES = 120;
const TICKS = 120;
const SEED = 1337; // same seed memoryGraph.js uses, for a familiar layout
const INSET = 8;
const CACHE_MS = 5 * 60 * 1000;
const MAX_RELATED = 12; // keep highlighting calm — never light up the whole graph
const UI_VIS_KEY = 'apollo-ui-visibility'; // same key app.js's Customize UI uses

// Common words that would otherwise light up almost every memory.
const STOPWORDS = new Set([
  'the', 'and', 'for', 'with', 'that', 'this', 'what', 'how', 'can', 'you',
  'your', 'from', 'about', 'into', 'have', 'has', 'are', 'was', 'were',
  'will', 'would', 'could', 'should', 'just', 'like', 'want', 'need',
  'some', 'more',
]);

function _esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function _tokenize(text) {
  return String(text || '').toLowerCase().match(/[a-z0-9]+/g) || [];
}

/** Ids of nodes related to free-text input: query tokens (>=3 chars, no
 * punctuation, no stopwords) are whole-word matched (never substring —
 * "plan" won't hit "planning") against each node's tokenized label+text,
 * scored by match count, capped to the top MAX_RELATED (ties keep order). */
export function relatedNodeIds(text, nodes) {
  const words = _tokenize(text).filter((t) => t.length >= 3 && !STOPWORDS.has(t));
  if (!words.length) return [];
  const scored = [];
  (nodes || []).forEach((n, i) => {
    const hay = new Set(_tokenize(`${n.label || ''} ${n.text || ''}`));
    let score = 0;
    for (const w of words) if (hay.has(w)) score += 1;
    if (score > 0) scored.push({ id: n.id, score, i });
  });
  scored.sort((a, b) => (b.score - a.score) || (a.i - b.i));
  return scored.slice(0, MAX_RELATED).map((s) => s.id);
}

/** Deterministic, bounded force layout. Caps to the first MAX_NODES nodes
 * (order-preserving truncation) and drops any edge whose endpoint was
 * capped away, so every returned edge can be drawn from returned nodes. */
export function layoutConstellation(graph, w, h) {
  const width = w > 0 ? w : 800;
  const height = h > 0 ? h : 400;
  const nodes = ((graph && graph.nodes) || []).slice(0, MAX_NODES).map((n) => ({ ...n }));
  const ids = new Set(nodes.map((n) => n.id));
  const edges = ((graph && graph.edges) || [])
    .filter((e) => ids.has(e.source) && ids.has(e.target))
    .map((e) => ({ source: e.source, target: e.target }));

  seedPositions(nodes, width, height, SEED);
  for (let i = 0; i < TICKS; i++) stepLayout(nodes, edges, { width, height });
  const minX = INSET;
  const maxX = Math.max(INSET, width - INSET);
  const minY = INSET;
  const maxY = Math.max(INSET, height - INSET);
  const clamp = (v, lo, hi) => Math.round(Math.min(hi, Math.max(lo, v)) * 10) / 10;
  const outNodes = nodes.map((n) => ({
    id: n.id,
    label: n.label,
    text: n.text,
    category: n.category,
    size: n.size,
    x: clamp(n.x, minX, maxX),
    y: clamp(n.y, minY, maxY),
  }));
  return { nodes: outNodes, edges };
}

/** Inner SVG markup (edges `<g>`, then nodes `<g>`) for a laid-out
 * constellation; '' when there are no nodes. `relatedIds` may be a Set or
 * an array. No per-node <title>: the svg is aria-hidden with
 * pointer-events:none, so a circle's accessible name is never reachable. */
export function renderConstellationSVG(layout, relatedIds) {
  const nodes = (layout && layout.nodes) || [];
  if (!nodes.length) return '';
  const edges = (layout && layout.edges) || [];
  const related = relatedIds instanceof Set ? relatedIds : new Set(relatedIds || []);
  const byId = new Map(nodes.map((n) => [n.id, n]));
  let edgesHtml = '';
  for (const e of edges) {
    const s = byId.get(e.source);
    const t = byId.get(e.target);
    if (!s || !t) continue;
    edgesHtml += `<line class="constellation-edge" x1="${s.x}" y1="${s.y}" x2="${t.x}" y2="${t.y}"></line>`;
  }
  let nodesHtml = '';
  for (const n of nodes) {
    const isRelated = related.has(n.id);
    // Category comes from user-importable memory packs; keep it a plain
    // class token so it can never break out of the attribute.
    const cat = String(n.category || 'fact').toLowerCase().replace(/[^a-z0-9_-]/g, '') || 'fact';
    const cls = `constellation-node constellation-node--${cat}${isRelated ? ' constellation-node--related' : ''}`;
    const r = isRelated ? 5 : 3;
    const x = Number(n.x) || 0, y = Number(n.y) || 0;
    nodesHtml += `<circle class="${cls}" r="${r}" cx="${x}" cy="${y}" data-id="${_esc(n.id)}"></circle>`;
  }
  return `<g class="constellation-edges">${edgesHtml}</g><g class="constellation-nodes">${nodesHtml}</g>`;
}

// ───────────────────────── live mount (browser only) ─────────────────────

function _debounce(fn, ms) {
  let timer = null;
  return (...args) => {
    if (timer) clearTimeout(timer);
    timer = setTimeout(() => { timer = null; fn(...args); }, ms);
  };
}

function _isIncognitoActive() {
  const chk = document.getElementById('incognito-toggle');
  if (chk) return !!chk.checked;
  const btn = document.getElementById('incognito-btn');
  return !!(btn && btn.classList.contains('active'));
}

// Mirrors floorHook.js's floorEnabled(): applyUIVis() only sweeps DOM
// present when it runs, so a render loop has to read the stored
// apollo-ui-visibility preference itself.
function _constellationEnabled() {
  try {
    const raw = localStorage.getItem(UI_VIS_KEY);
    if (!raw) return true;
    const state = JSON.parse(raw);
    return !state || state['memory-constellation'] !== false;
  } catch (_) {
    return true;
  }
}

const _cache = { data: null, at: 0 };
async function _defaultFetchGraph() {
  const now = Date.now();
  if (_cache.data && now - _cache.at < CACHE_MS) return _cache.data;
  try {
    const res = await fetch(`${window.location.origin}/api/memory/graph`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    const out = { nodes: (data && data.nodes) || [], edges: (data && data.edges) || [] };
    _cache.data = out;
    _cache.at = now;
    return out;
  } catch (_) {
    return { nodes: [], edges: [] };
  }
}

let _mounted = false;
function _isWelcomeHidden() {
  const ws = document.getElementById('welcome-screen');
  return !!(ws && ws.classList.contains('hidden'));
}

/** Wire the live constellation to `#<svgId>`, brightening nodes related to
 * `#<inputId>`'s value as typed. Idempotent — call once at startup; it
 * self-manages re-renders on welcome-screen shows, input, and resize. */
export function mountConstellation({
  svgId = 'welcome-constellation',
  inputId = 'message',
  fetchGraph = _defaultFetchGraph,
} = {}) {
  if (_mounted) return;
  _mounted = true;
  let lastLayout = { nodes: [], edges: [] };

  function _clear(svg) {
    svg.innerHTML = '';
    svg.classList.remove('welcome-constellation--on');
    lastLayout = { nodes: [], edges: [] };
  }

  async function renderNow() {
    const svg = document.getElementById(svgId);
    if (!svg) return;
    if (_isWelcomeHidden()) return;
    if (!_constellationEnabled()) return;
    const computed = typeof window.getComputedStyle === 'function' ? window.getComputedStyle(svg) : null;
    if (computed && computed.display === 'none') return;
    if (_isIncognitoActive()) { _clear(svg); return; }
    const rect = svg.getBoundingClientRect();
    const width = Math.round(rect.width) || 800;
    const height = Math.round(rect.height) || 400;
    let graph;
    try {
      graph = await fetchGraph();
    } catch (_) {
      graph = { nodes: [], edges: [] };
    }
    // The welcome screen or incognito could have toggled while the fetch
    // was in flight — don't paint a stale/unwanted render over new state.
    if (_isWelcomeHidden()) return;
    if (_isIncognitoActive()) { _clear(svg); return; }
    const layout = layoutConstellation(graph || { nodes: [], edges: [] }, width, height);
    lastLayout = layout;
    svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
    const input = document.getElementById(inputId);
    const related = relatedNodeIds(input ? input.value : '', layout.nodes);
    svg.innerHTML = renderConstellationSVG(layout, related);
    svg.classList.toggle('welcome-constellation--on', layout.nodes.length > 0);
  }

  function updateRelated() {
    const svg = document.getElementById(svgId);
    if (!svg || !lastLayout.nodes.length) return;
    const input = document.getElementById(inputId);
    const related = new Set(relatedNodeIds(input ? input.value : '', lastLayout.nodes));
    const circles = svg.querySelectorAll('.constellation-node');
    circles.forEach((c) => {
      const isRelated = related.has(c.dataset.id);
      c.classList.toggle('constellation-node--related', isRelated);
      c.setAttribute('r', isRelated ? '5' : '3');
    });
  }

  const onWelcome = _debounce(renderNow, 150);
  const onInput = _debounce(updateRelated, 120);
  const onResize = _debounce(renderNow, 250);
  renderNow();
  window.addEventListener('apollo:welcome', onWelcome);
  window.addEventListener('resize', onResize);
  const input = document.getElementById(inputId);
  if (input) input.addEventListener('input', onInput);
  // The svg's own box can change without a viewport resize (sidebar
  // toggle, modal open/close) — track it directly too (pattern from
  // ui.js's textarea observer / modalSnap.js's pane observers).
  const svgEl = document.getElementById(svgId);
  if (svgEl && typeof ResizeObserver !== 'undefined') {
    new ResizeObserver(onResize).observe(svgEl);
  }
}

export default { relatedNodeIds, layoutConstellation, renderConstellationSVG, mountConstellation };
