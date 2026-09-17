import test from 'node:test';
import assert from 'node:assert/strict';
import { buildPaletteItems, fuzzyScore } from '../static/js/paletteItems.js';

const ctx = {
  actions: [{ id: 'new_session', label: 'New chat' }, { id: 'open_calendar', label: 'Open Calendar' }, { id: 'council', label: 'Ask the Council' }],
  sessions: [{ id: 's1', name: 'Rust borrow checker' }, { id: 's2', name: 'Trip to Berlin' }],
  models: [{ id: 'm1', label: 'qwen3-8b', endpoint: 'local' }, { id: 'm2', label: 'claude-sonnet-5', endpoint: 'anthropic' }],
};

test('empty query lists actions first, then sessions, then models', () => {
  const items = buildPaletteItems('', ctx);
  assert.equal(items[0].group, 'Actions');
  assert.ok(items.some(i => i.group === 'Sessions'));
  assert.ok(items.some(i => i.group === 'Models'));
});

test('query filters across groups with fuzzy match', () => {
  const items = buildPaletteItems('brl', ctx);
  assert.ok(items.some(i => i.label === 'Trip to Berlin'));
  assert.ok(!items.some(i => i.label === 'Rust borrow checker'));
});

test('fuzzyScore is 0 for non-subsequence', () => {
  assert.equal(fuzzyScore('xyz', 'calendar'), 0);
  assert.ok(fuzzyScore('cal', 'Open Calendar') > 0);
});

test('groups are capped at 8 items and ranked by score', () => {
  const many = { actions: [], sessions: Array.from({ length: 20 }, (_, i) => ({ id: 's' + i, name: 'Session ' + i })), models: [] };
  assert.equal(buildPaletteItems('', many).filter(i => i.group === 'Sessions').length, 8);
  const ranked = buildPaletteItems('sess 1', many).filter(i => i.group === 'Sessions');
  assert.ok(ranked.length > 0 && ranked.every(i => /1/.test(i.label)));
});
