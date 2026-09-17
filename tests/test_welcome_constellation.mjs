import test from 'node:test';
import assert from 'node:assert/strict';
import { relatedNodeIds, layoutConstellation, renderConstellationSVG } from '../static/js/welcomeConstellation.js';

test('relatedNodeIds matches on word overlap, case-insensitive, min 3 chars', () => {
  const nodes = [{ id: 'a', label: 'Apollo release plan' }, { id: 'b', label: 'Berlin trip' }, { id: 'c', label: 'Rust borrow checker' }];
  assert.deepEqual(relatedNodeIds('plan the apollo demo', nodes), ['a']);
  assert.deepEqual(relatedNodeIds('to', nodes), []);
  assert.deepEqual(relatedNodeIds('', nodes), []);
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

test('renderConstellationSVG escapes labels and marks related nodes', () => {
  const layout = { nodes: [{ id: 'a', label: '<b>x</b>', category: 'fact', x: 10, y: 10 }, { id: 'b', label: 'y', category: 'goal', x: 50, y: 50 }], edges: [{ source: 'a', target: 'b' }] };
  const svg = renderConstellationSVG(layout, new Set(['a']));
  assert.doesNotMatch(svg, /<b>x<\/b>/);
  assert.match(svg, /&lt;b&gt;x&lt;\/b&gt;/);
  assert.match(svg, /constellation-node--related/);
  assert.match(svg, /constellation-edge/);
  assert.equal(renderConstellationSVG({ nodes: [], edges: [] }, new Set()), '');
});
