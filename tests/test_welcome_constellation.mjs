import test from 'node:test';
import assert from 'node:assert/strict';
import { relatedNodeIds, layoutConstellation, renderConstellationSVG, mountConstellation } from '../static/js/welcomeConstellation.js';

test('relatedNodeIds matches on word overlap, case-insensitive, min 3 chars', () => {
  const nodes = [{ id: 'a', label: 'Apollo release plan' }, { id: 'b', label: 'Berlin trip' }, { id: 'c', label: 'Rust borrow checker' }];
  assert.deepEqual(relatedNodeIds('plan the apollo demo', nodes), ['a']);
  assert.deepEqual(relatedNodeIds('to', nodes), []);
  assert.deepEqual(relatedNodeIds('', nodes), []);
});

test('relatedNodeIds matches on text, drops stopwords via word boundaries, and caps at 12', () => {
  // Matches the `text` field, not just `label`.
  const textNode = [{ id: 't', text: 'Deploying the Apollo release pipeline' }];
  assert.deepEqual(relatedNodeIds('pipeline', textNode), ['t']);

  // "the" is a stopword, and even un-stopped it's a whole-word match only —
  // it must not fuzzily hit "Theme" as a substring.
  const themeNode = [{ id: 'theme', label: 'Theme colors' }];
  assert.deepEqual(relatedNodeIds('the', themeNode), []);

  // Word-boundary matching: "plan" must not match "planning" as a substring.
  assert.deepEqual(relatedNodeIds('plan', [{ id: 'w', label: 'planning ahead' }]), []);

  // Calm highlighting: never more than the top 12, ties keep node order.
  const many = Array.from({ length: 15 }, (_, i) => ({ id: 'p' + i, label: 'Project apollo status' }));
  const ids = relatedNodeIds('project apollo', many);
  assert.equal(ids.length, 12);
  assert.deepEqual(ids, many.slice(0, 12).map((n) => n.id));
});

test('layoutConstellation is deterministic and bounded', () => {
  const g = { nodes: [{ id: 'a' }, { id: 'b' }, { id: 'c' }], edges: [{ source: 'a', target: 'b' }] };
  const p1 = layoutConstellation(g, 800, 400);
  const p2 = layoutConstellation(g, 800, 400);
  assert.deepEqual(p1, p2);
  for (const n of p1.nodes) { assert.ok(n.x >= 0 && n.x <= 800 && n.y >= 0 && n.y <= 400); }
});

test('layoutConstellation caps to 120 nodes and drops dangling edges', () => {
  const nodes = Array.from({ length: 150 }, (_, i) => ({ id: 'n' + i, size: i }));
  const edges = [{ source: 'n0', target: 'n149' }, { source: 'n0', target: 'n1' }];
  const p = layoutConstellation({ nodes, edges }, 800, 400);
  assert.equal(p.nodes.length, 120);
  assert.equal(p.edges.length, 1);
});

test('renderConstellationSVG marks related nodes and never emits a per-node title', () => {
  const layout = { nodes: [{ id: 'a', label: '<b>x</b>', category: 'fact', x: 10, y: 10 }, { id: 'b', label: 'y', category: 'goal', x: 50, y: 50 }], edges: [{ source: 'a', target: 'b' }] };
  const svg = renderConstellationSVG(layout, new Set(['a']));
  // <title> is dropped entirely (the svg is aria-hidden + pointer-events:none,
  // so a per-circle accessible name can never be reached) — and a hostile
  // label still can't inject raw markup anywhere in the output.
  assert.doesNotMatch(svg, /<title/);
  assert.doesNotMatch(svg, /<b>x<\/b>/);
  // data-id is the one remaining user-data attribute: a hostile id stays inert.
  const hostile = renderConstellationSVG({ nodes: [{ id: 'a" onclick="x()', x: 1, y: 1 }], edges: [] }, new Set());
  assert.doesNotMatch(hostile, /onclick="x/);
  assert.match(hostile, /data-id="a&quot; onclick=&quot;x\(\)"/);
  assert.match(svg, /constellation-node--related/);
  assert.match(svg, /constellation-edge/);
  assert.equal(renderConstellationSVG({ nodes: [], edges: [] }, new Set()), '');
});

test('renderConstellationSVG sanitizes a hostile category into a plain class token', () => {
  const layout = { nodes: [{ id: 'x', label: 'l', category: 'evil" onclick="hack()', x: 1, y: 1 }], edges: [] };
  const svg = renderConstellationSVG(layout, new Set());
  assert.doesNotMatch(svg, /onclick=/);
  assert.doesNotMatch(svg, /evil"/);
  assert.match(svg, /constellation-node--evilonclickhack/);
});

// Minimal DOM shim (same style as tests/test_paperclip_floor_ui.mjs) — just
// enough for mountConstellation() to run its real render + partial-update
// paths without a browser, so we can prove the input handler patches
// existing circles in place instead of re-rendering the whole svg.
function _classList(initial = []) {
  const set = new Set(initial);
  return {
    add: (c) => set.add(c),
    remove: (c) => set.delete(c),
    contains: (c) => set.has(c),
    toggle: (c, on) => { on ? set.add(c) : set.delete(c); },
  };
}

test('mountConstellation patches only r/class on input, without reassigning innerHTML', async (t) => {
  t.after(() => { delete global.window; delete global.localStorage; delete global.document; });
  const circle = { dataset: { id: 'a' }, classList: _classList(), setAttribute(k, v) { if (k === 'r') this.r = v; } };
  let html = '';
  let writes = 0;
  const svgEl = {
    classList: _classList(),
    getBoundingClientRect: () => ({ width: 800, height: 400 }),
    setAttribute() {},
    querySelectorAll: (sel) => (sel === '.constellation-node' ? [circle] : []),
    get innerHTML() { return html; },
    set innerHTML(v) { html = v; writes += 1; },
  };
  const inputHandlers = [];
  const inputEl = { value: '', addEventListener: (t, fn) => { if (t === 'input') inputHandlers.push(fn); } };
  const wsEl = { classList: { contains: () => false } };

  global.window = { addEventListener() {}, getComputedStyle: () => ({ display: 'block' }) };
  global.localStorage = { getItem: () => null };
  global.document = {
    getElementById: (id) => ({ 'welcome-constellation': svgEl, 'welcome-screen': wsEl, message: inputEl }[id] || null),
  };

  const graph = { nodes: [{ id: 'a', label: 'Apollo plan' }], edges: [] };
  mountConstellation({ fetchGraph: async () => graph });
  await new Promise((r) => setTimeout(r, 20)); // let the initial async render settle

  assert.equal(writes, 1);
  const writesAfterMount = writes;

  inputEl.value = 'apollo';
  inputHandlers.forEach((fn) => fn());
  await new Promise((r) => setTimeout(r, 150)); // clear the 120ms input debounce

  assert.equal(writes, writesAfterMount); // no re-render — innerHTML untouched
  assert.equal(circle.r, '5');
  assert.ok(circle.classList.contains('constellation-node--related'));
});
