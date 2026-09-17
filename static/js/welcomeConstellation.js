// Live "memory constellation" rendered behind the welcome (empty-chat)
// screen — a dimmed force-directed graph of the user's memory nodes
// (graphLayout.js) that brightens nodes related to whatever is being typed
// in the composer. Pure layout/render helpers below do no DOM work and are
// unit-tested directly under Node; mountConstellation() wires them to the
// live #welcome-constellation SVG in the browser.

import { seedPositions, stepLayout } from './graphLayout.js';

const MAX_NODES = 120;
const TICKS = 120;
const SEED = 1337; // same seed memoryGraph.js uses, for a familiar layout
const INSET = 8;
const CACHE_MS = 5 * 60 * 1000;

function _esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

/** Ids of nodes related to free-text input: a node matches when any
 * lowercase token (>=3 chars, punctuation stripped) from `text` appears as
 * a substring of its label or text. Empty input / only-short-tokens input
 * matches nothing. */
export function relatedNodeIds(text, nodes) {
  const words = (String(text || '').toLowerCase().match(/[a-z0-9]+/g) || [])
    .filter((t) => t.length >= 3);
  if (!words.length) return [];
  const out = [];
  for (const n of nodes || []) {
    const hay = `${n.label || ''} ${n.text || ''}`.toLowerCase();
    if (words.some((w) => hay.includes(w))) out.push(n.id);
  }
  return out;
}

/** Deterministic, bounded force layout for the constellation background.
 * Caps to the first MAX_NODES nodes (order-preserving — a plain, stable
 * truncation of whatever order the caller's graph already has) and drops
 * any edge whose endpoint was capped away, so every returned edge can
 * always be drawn from the returned nodes' own coordinates. */
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
    category: n.category,
    size: n.size,
    x: clamp(n.x, minX, maxX),
    y: clamp(n.y, minY, maxY),
  }));

  return { nodes: outNodes, edges };
}

/** Inner SVG markup (a `<g>` of edges, then a `<g>` of nodes) for a laid-out
 * constellation. '' when there are no nodes — the caller hides the <svg>
 * (no --on class) in that case. `relatedIds` may be a Set or an array. */
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
    const label = n.label || n.text || '';
    const x = Number(n.x) || 0, y = Number(n.y) || 0;
    nodesHtml += `<circle class="${cls}" r="${r}" cx="${x}" cy="${y}" data-id="${_esc(n.id)}"><title>${_esc(label)}</title></circle>`;
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

/** Wire the live constellation to `#<svgId>`, brightening nodes related to
 * `#<inputId>`'s value as the user types. Idempotent — safe to call once at
 * startup; it self-manages re-renders on welcome-screen (re)shows, composer
 * input, and viewport resize from then on. */
export function mountConstellation({
  svgId = 'welcome-constellation',
  inputId = 'message',
  fetchGraph = _defaultFetchGraph,
} = {}) {
  if (_mounted) return;
  _mounted = true;

  let lastLayout = { nodes: [], edges: [] };

  async function renderNow() {
    const svg = document.getElementById(svgId);
    if (!svg) return;
    const ws = document.getElementById('welcome-screen');
    if (ws && ws.classList.contains('hidden')) return;

    if (_isIncognitoActive()) {
      svg.innerHTML = '';
      svg.classList.remove('welcome-constellation--on');
      lastLayout = { nodes: [], edges: [] };
      return;
    }

    const rect = svg.getBoundingClientRect();
    const width = Math.round(rect.width) || 800;
    const height = Math.round(rect.height) || 400;

    let graph;
    try {
      graph = await fetchGraph();
    } catch (_) {
      graph = { nodes: [], edges: [] };
    }

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
}

export default { relatedNodeIds, layoutConstellation, renderConstellationSVG, mountConstellation };
