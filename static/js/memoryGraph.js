// Knowledge-graph tab for the Brain/memory modal. Lazily fetches
// /api/memory/graph, runs the pure force layout (graphLayout.js), and renders a
// self-contained force-directed SVG — no CDN/D3, CSP-nonce safe. Clicking a node
// shows its fact and offers a jump to the source chat.
//
// Public entry: openGraphTab() — called by memory.js when the Graph tab opens.

import { seedPositions, stepLayout } from './graphLayout.js';
import sessionModule from './sessions.js';

const SVG_NS = 'http://www.w3.org/2000/svg';
const SEED = 1337;          // fixed → deterministic layout across opens
const TICKS = 300;
const NODE_R = 7;

// Category → a color (CSS custom property where one exists, else a literal).
// Mirrors the .memory-cat-* palette in style.css so the graph matches badges.
const CATEGORY_COLORS = {
  fact: 'var(--accent, var(--fg))',
  identity: 'var(--hl-function)',
  preference: 'var(--hl-keyword)',
  contact: '#98c379',
  project: 'var(--hl-string)',
  goal: 'var(--red)',
  task: '#d19a66',
};

function catColor(category) {
  return CATEGORY_COLORS[category] || 'var(--accent, var(--fg))';
}

let _rendered = false;      // lazy: build once per page load
let _wired = false;

function el(id) { return document.getElementById(id); }

// Called by memory.js on Graph-tab activation. Idempotent.
export function openGraphTab() {
  wireControls();
  if (_rendered) return;
  render();
}

function wireControls() {
  if (_wired) return;
  _wired = true;
  const refresh = el('memory-graph-refresh');
  if (refresh) refresh.addEventListener('click', () => { _rendered = false; render(); });
}

async function render() {
  const svg = el('memory-graph-svg');
  const empty = el('memory-graph-empty');
  const detail = el('memory-graph-detail');
  if (!svg) return;

  _rendered = true;
  clearSvg(svg);
  if (detail) detail.innerHTML = '<span style="opacity:0.55;">Select a node to see the fact.</span>';

  let data;
  try {
    const res = await fetch(`${window.location.origin}/api/memory/graph`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    data = await res.json();
  } catch (err) {
    showEmpty(empty, 'Could not load the graph. Try Refresh.');
    return;
  }

  const nodes = (data && data.nodes) || [];
  const edges = (data && data.edges) || [];

  if (!nodes.length) {
    showEmpty(empty, 'No memories yet — distill a chat first, then they\'ll appear here.');
    return;
  }
  hideEmpty(empty);

  // Layout box: use the rendered SVG size, falling back to sane defaults.
  const rect = svg.getBoundingClientRect();
  const width = Math.max(320, Math.round(rect.width) || 560);
  const height = Math.max(240, Math.round(rect.height) || 400);
  svg.setAttribute('viewBox', `0 0 ${width} ${height}`);

  // Run the pure force layout to convergence (headless — no DOM in the loop).
  seedPositions(nodes, width, height, SEED);
  for (let i = 0; i < TICKS; i++) stepLayout(nodes, edges, { width, height });

  const byId = new Map(nodes.map(n => [n.id, n]));

  // Adjacency for click-highlight of neighbors.
  const neighbors = new Map();
  const addAdj = (a, b) => {
    if (!neighbors.has(a)) neighbors.set(a, new Set());
    neighbors.get(a).add(b);
  };

  // ── Edges (drawn first, under nodes) ──
  const edgeGroup = document.createElementNS(SVG_NS, 'g');
  edgeGroup.setAttribute('class', 'memory-graph-edges');
  const lineEls = [];
  for (const e of edges) {
    const s = byId.get(e.source);
    const t = byId.get(e.target);
    if (!s || !t) continue;
    addAdj(e.source, e.target);
    addAdj(e.target, e.source);
    const line = document.createElementNS(SVG_NS, 'line');
    line.setAttribute('x1', s.x); line.setAttribute('y1', s.y);
    line.setAttribute('x2', t.x); line.setAttribute('y2', t.y);
    line.setAttribute('stroke', 'var(--fg)');
    const semantic = e.type === 'semantic';
    // Semantic = solid, opacity by weight; session = dashed, fainter.
    line.setAttribute('stroke-opacity', semantic ? String(0.15 + 0.35 * (e.weight || 0.5)) : '0.18');
    line.setAttribute('stroke-width', semantic ? '1.4' : '1');
    if (!semantic) line.setAttribute('stroke-dasharray', '3 3');
    line.dataset.source = e.source;
    line.dataset.target = e.target;
    edgeGroup.appendChild(line);
    lineEls.push(line);
  }
  svg.appendChild(edgeGroup);

  // ── Nodes + labels ──
  const nodeGroup = document.createElementNS(SVG_NS, 'g');
  nodeGroup.setAttribute('class', 'memory-graph-nodes');
  const circleEls = new Map();
  for (const n of nodes) {
    const g = document.createElementNS(SVG_NS, 'g');
    g.setAttribute('class', 'memory-graph-node');
    g.style.cursor = 'pointer';

    const c = document.createElementNS(SVG_NS, 'circle');
    c.setAttribute('cx', n.x); c.setAttribute('cy', n.y);
    c.setAttribute('r', NODE_R);
    c.setAttribute('fill', catColor(n.category));
    c.setAttribute('stroke', 'var(--bg)');
    c.setAttribute('stroke-width', '1.5');
    circleEls.set(n.id, c);

    const label = document.createElementNS(SVG_NS, 'text');
    label.setAttribute('x', n.x + NODE_R + 3);
    label.setAttribute('y', n.y + 3);
    label.setAttribute('font-size', '10');
    label.setAttribute('fill', 'var(--fg)');
    label.setAttribute('fill-opacity', '0.75');
    label.setAttribute('pointer-events', 'none');
    label.textContent = shortLabel(n.label || n.text || '');

    g.appendChild(c);
    g.appendChild(label);
    g.addEventListener('click', () => selectNode(n));
    nodeGroup.appendChild(g);
  }
  svg.appendChild(nodeGroup);

  // Click a node → highlight it + its neighbors, dim the rest, show detail.
  function selectNode(node) {
    const near = neighbors.get(node.id) || new Set();
    for (const [id, circle] of circleEls) {
      const active = id === node.id || near.has(id);
      circle.setAttribute('fill-opacity', active ? '1' : '0.25');
      circle.setAttribute('stroke-width', id === node.id ? '2.5' : '1.5');
    }
    for (const line of lineEls) {
      const on = line.dataset.source === node.id || line.dataset.target === node.id;
      line.setAttribute('stroke-opacity', on ? '0.55' : '0.06');
    }
    renderDetail(detail, node);
  }
}

function renderDetail(detail, node) {
  if (!detail) return;
  detail.innerHTML = '';

  const badge = document.createElement('span');
  badge.className = 'memory-cat-badge memory-cat-' + (node.category || 'fact');
  badge.textContent = node.category || 'fact';
  badge.style.display = 'inline-block';
  badge.style.marginBottom = '8px';
  detail.appendChild(badge);

  const text = document.createElement('p');
  text.style.margin = '0 0 12px';
  text.style.lineHeight = '1.45';
  text.style.whiteSpace = 'pre-wrap';
  text.textContent = node.text || node.label || '';
  detail.appendChild(text);

  if (node.session_id) {
    const link = document.createElement('a');
    link.href = '#';
    link.textContent = 'Go to source chat →';
    link.style.color = 'var(--accent, var(--fg))';
    link.style.textDecoration = 'underline';
    link.style.cursor = 'pointer';
    link.addEventListener('click', (ev) => {
      ev.preventDefault();
      goToSession(node.session_id);
    });
    detail.appendChild(link);
  } else {
    const none = document.createElement('span');
    none.style.opacity = '0.5';
    none.textContent = 'No source chat recorded.';
    detail.appendChild(none);
  }
}

// Open the memory's source session. Prefers the real selectSession() export;
// falls back to a hash so the link is never a dead end.
function goToSession(sessionId) {
  const modal = el('memory-modal');
  if (modal) modal.classList.add('hidden');
  const open = sessionModule && sessionModule.selectSession;
  if (typeof open === 'function') {
    try { open(sessionId); return; } catch (_) { /* fall through */ }
  }
  window.location.hash = sessionId;
}

// ── helpers ──
function shortLabel(s) {
  const t = (s || '').trim();
  return t.length <= 22 ? t : t.slice(0, 21).trimEnd() + '…';
}

function clearSvg(svg) {
  while (svg.firstChild) svg.removeChild(svg.firstChild);
}

function showEmpty(empty, msg) {
  if (!empty) return;
  empty.textContent = msg;
  empty.classList.remove('hidden');
}

function hideEmpty(empty) {
  if (empty) empty.classList.add('hidden');
}

export default { openGraphTab };
