import assert from 'node:assert/strict';
import test from 'node:test';
import { stepLayout, seedPositions } from '../static/js/graphLayout.js';

test('connected nodes move closer over iterations than unconnected', () => {
  const nodes = [{ id: 'a' }, { id: 'b' }, { id: 'c' }];
  seedPositions(nodes, 100, 100, 7);          // deterministic seed
  const edges = [{ source: 'a', target: 'b' }];   // a,b spring together; c free
  const d = (p, q) => Math.hypot(p.x - q.x, p.y - q.y);
  const before = d(nodes[0], nodes[1]);
  for (let i = 0; i < 200; i++) stepLayout(nodes, edges, { width: 100, height: 100 });
  const after = d(nodes[0], nodes[1]);
  assert.ok(after < before, 'spring pulls connected nodes together');
  assert.ok(nodes.every(n => Number.isFinite(n.x) && Number.isFinite(n.y)), 'stable coords');
  assert.ok(nodes.every(n => n.x >= 0 && n.x <= 100 && n.y >= 0 && n.y <= 100), 'stays in bounds');
});

test('seedPositions is deterministic for a given seed', () => {
  const a = [{ id: 'x' }, { id: 'y' }, { id: 'z' }];
  const b = [{ id: 'x' }, { id: 'y' }, { id: 'z' }];
  seedPositions(a, 200, 150, 42);
  seedPositions(b, 200, 150, 42);
  a.forEach((n, i) => {
    assert.equal(n.x, b[i].x);
    assert.equal(n.y, b[i].y);
  });
  // and NOT constant across nodes (real spread)
  assert.ok(a[0].x !== a[1].x || a[0].y !== a[1].y);
});

test('seeded positions stay within bounds and initialise velocity', () => {
  const nodes = [{ id: 'a' }, { id: 'b' }];
  seedPositions(nodes, 300, 200, 3);
  for (const n of nodes) {
    assert.ok(n.x >= 0 && n.x <= 300);
    assert.ok(n.y >= 0 && n.y <= 200);
    assert.equal(n.vx, 0);
    assert.equal(n.vy, 0);
  }
});

test('stepLayout handles edges referencing missing nodes without throwing', () => {
  const nodes = [{ id: 'a' }, { id: 'b' }];
  seedPositions(nodes, 100, 100, 1);
  const edges = [{ source: 'a', target: 'ghost' }, { source: 'a', target: 'b' }];
  stepLayout(nodes, edges, { width: 100, height: 100 });
  assert.ok(nodes.every(n => Number.isFinite(n.x) && Number.isFinite(n.y)));
});
