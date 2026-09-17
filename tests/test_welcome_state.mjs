import test from 'node:test';
import assert from 'node:assert/strict';
import { pickRecentSessions, buildSuggestedPrompts } from '../static/js/welcomeState.js';

test('pickRecentSessions returns 4 most recent non-archived by updated_at', () => {
  const s = [
    { id: 'a', name: 'A', updated_at: '2026-09-01T00:00:00Z', archived: false },
    { id: 'b', name: 'B', updated_at: '2026-09-05T00:00:00Z', archived: true },
    { id: 'c', name: 'C', updated_at: '2026-09-04T00:00:00Z', archived: false },
    { id: 'd', name: 'D', updated_at: '2026-09-03T00:00:00Z', archived: false },
    { id: 'e', name: 'E', updated_at: '2026-09-02T00:00:00Z', archived: false },
    { id: 'f', name: 'F', updated_at: '2026-08-30T00:00:00Z', archived: false },
  ];
  assert.deepEqual(pickRecentSessions(s).map(x => x.id), ['c', 'd', 'e', 'a']);
});

test('buildSuggestedPrompts uses memory categories and falls back to defaults', () => {
  const p = buildSuggestedPrompts([{ category: 'project', text: 'Working on Apollo' }]);
  assert.equal(p.length, 3);
  assert.ok(p.some(x => /Apollo/.test(x)));
  assert.equal(buildSuggestedPrompts([]).length, 3);
});
