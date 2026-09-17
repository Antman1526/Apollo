import test from 'node:test';
import assert from 'node:assert/strict';
import { pickRecentSessions, buildSuggestedPrompts, renderWelcomeStateHTML } from '../static/js/welcomeState.js';

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

test('pickRecentSessions falls back to last_message_at/created_at and filters sidebar-excluded sessions', () => {
  const s = [
    { id: 'x', name: 'X', created_at: '2026-09-01T00:00:00Z' },
    { id: 'y', name: 'Y', last_message_at: '2026-09-05T00:00:00Z' },
    { id: 'z', name: 'Z', folder: 'Assistant', last_message_at: '2026-09-06T00:00:00Z' },
  ];
  assert.deepEqual(pickRecentSessions(s).map(x => x.id), ['y', 'x']);
  assert.deepEqual(pickRecentSessions([{ id: 'a', archived: true }]), []);
});

test('renderWelcomeStateHTML renders nothing for empty groups and escapes unsafe text', () => {
  const empty = renderWelcomeStateHTML({ recent: [], prompts: [] });
  assert.equal(empty.recentHtml, '');
  assert.equal(empty.promptsHtml, '');

  const { recentHtml, promptsHtml } = renderWelcomeStateHTML({
    recent: [{ id: '1', name: '<script>alert(1)</script>"quote"' }],
    prompts: ["<img src=x onerror=alert(1)>'q'"],
  });
  assert.ok(!recentHtml.includes('<script>'));
  assert.ok(recentHtml.includes('&lt;script&gt;'));
  assert.ok(recentHtml.includes('&quot;quote&quot;'));
  assert.ok(!promptsHtml.includes('<img src=x'));
  assert.ok(promptsHtml.includes('&#39;q&#39;'));
});
