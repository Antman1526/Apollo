import test from 'node:test';
import assert from 'node:assert/strict';
import { reduceCockpit, renderCockpitHTML } from '../static/js/cockpit.js';

test('reduceCockpit folds metrics and model_info', () => {
  let s = reduceCockpit(undefined, { type: 'model_info', model: 'qwen3-8b', context_length: 32768, local: true });
  s = reduceCockpit(s, { type: 'metrics', data: { tokens_per_second: 41.2, input_tokens: 1200, output_tokens: 300 } });
  assert.equal(s.model, 'qwen3-8b');
  assert.equal(s.tps, 41.2);
  assert.equal(s.used, 1500);
  assert.equal(s.window, 32768);
});

test('reduceCockpit ignores unrelated events and keeps prior state', () => {
  const s0 = reduceCockpit(undefined, { type: 'model_info', model: 'm' });
  const s1 = reduceCockpit(s0, { type: 'tool_start', tool: 'bash' });
  assert.deepEqual(s1, s0);
});

test('renderCockpitHTML hides unknown fields and colors the fill by ratio', () => {
  const html = renderCockpitHTML({ model: 'x', tps: null, used: null, window: null });
  assert.doesNotMatch(html, /tok\/s/);
  assert.doesNotMatch(html, /cockpit-fill/);
  const html2 = renderCockpitHTML({ model: 'x', tps: 12.5, used: 800, window: 8192 });
  assert.match(html2, /12\.5 tok\/s/);
  assert.match(html2, /cockpit-fill/);
  assert.match(html2, /cockpit-fill--ok/);
  assert.match(renderCockpitHTML({ model: 'x', tps: 1, used: 7000, window: 8192 }), /cockpit-fill--hot/);
  assert.match(renderCockpitHTML({ model: 'x', tps: 1, used: 6200, window: 8192 }), /cockpit-fill--warm/);
});

test('reset clears everything', () => {
  const s = reduceCockpit({ model: 'x', tps: 1, used: 1, window: 1 }, { type: 'cockpit_reset' });
  assert.deepEqual(s, { model: null, tps: null, used: null, window: null });
});
