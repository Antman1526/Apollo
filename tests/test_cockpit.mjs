import test from 'node:test';
import assert from 'node:assert/strict';
import { reduceCockpit, renderCockpitHTML, createCockpit } from '../static/js/cockpit.js';

test('reduceCockpit folds metrics and model_info', () => {
  let s = reduceCockpit(undefined, { type: 'model_info', model: 'qwen3-8b', context_length: 32768 });
  s = reduceCockpit(s, {
    type: 'metrics',
    data: { tokens_per_second: 41.2, context_percent: 4.6, input_tokens: 1200, output_tokens: 300 },
  });
  assert.equal(s.model, 'qwen3-8b');
  assert.equal(s.tps, 41.2);
  assert.equal(s.percent, 4.6);
  assert.equal(s.window, 32768);
  assert.equal(s.fillSource, 'real');
});

test('reduceCockpit prefers context_percent over the token sum and carries the window forward', () => {
  let s = reduceCockpit(undefined, { type: 'model_info', model: 'm' });
  s = reduceCockpit(s, { type: 'metrics', data: { context_percent: 12.3, total_tokens: 999999, context_length: 8192 } });
  assert.equal(s.percent, 12.3);
  assert.equal(s.fillSource, 'real');
  assert.equal(s.window, 8192);
  // A later metrics event with no context_length must not drop the window a
  // real percent already established.
  s = reduceCockpit(s, { type: 'metrics', data: { tokens_per_second: 9 } });
  assert.equal(s.window, 8192);
  assert.equal(s.percent, 12.3);
});

test('reduceCockpit falls back to input+output/window only when context_percent is absent', () => {
  let s = reduceCockpit(undefined, { type: 'model_info', model: 'm', context_length: 8192 });
  s = reduceCockpit(s, { type: 'metrics', data: { input_tokens: 4096, output_tokens: 0 } });
  assert.equal(s.percent, 50);
  assert.equal(s.fillSource, 'fallback');
  assert.equal(s.used, 4096);
});

test('reduceCockpit ignores unrelated events and keeps prior state', () => {
  const s0 = reduceCockpit(undefined, { type: 'model_info', model: 'm' });
  const s1 = reduceCockpit(s0, { type: 'tool_start', tool: 'bash' });
  assert.deepEqual(s1, s0);
});

test('compacted clears the fill but keeps the window for the next metrics event', () => {
  const s0 = { model: 'm', tps: 5, percent: 80, fillSource: 'real', used: null, window: 8192 };
  const s1 = reduceCockpit(s0, { type: 'compacted', context_length: 8192 });
  assert.equal(s1.percent, null);
  assert.equal(s1.used, null);
  assert.equal(s1.window, 8192);
  assert.equal(s1.model, 'm');
});

test('renderCockpitHTML hides unknown fields and bands the fill to match the footer ring (warm 70, hot 85)', () => {
  const html = renderCockpitHTML({ model: 'x', tps: null, percent: null, window: null, used: null, fillSource: null });
  assert.doesNotMatch(html, /tok\/s/);
  assert.doesNotMatch(html, /cockpit-fill/);

  const ok = renderCockpitHTML({ model: 'x', tps: 12.5, percent: 9.8, fillSource: 'real', used: null, window: 8192 });
  assert.match(ok, /12\.5 tok\/s/);
  assert.match(ok, /title="Last response"/);
  assert.match(ok, /cockpit-fill--ok/);
  assert.match(ok, /10% of window/); // Math.round(9.8) === 10

  const warm = renderCockpitHTML({ model: 'x', tps: 1, percent: 76, fillSource: 'real', used: null, window: 8192 });
  assert.match(warm, /cockpit-fill--warm/);

  const hot = renderCockpitHTML({ model: 'x', tps: 1, percent: 90, fillSource: 'real', used: null, window: 8192 });
  assert.match(hot, /cockpit-fill--hot/);
});

test('renderCockpitHTML shows the honest used/window tooltip only for the fallback path', () => {
  const fallback = renderCockpitHTML({ model: 'x', tps: 1, percent: 50, fillSource: 'fallback', used: 4096, window: 8192 });
  assert.match(fallback, /title="4,096 \/ 8,192 tokens"/);
  assert.match(fallback, /aria-label="4,096 of 8,192 tokens"/);
  assert.match(fallback, /role="img"/);

  const real = renderCockpitHTML({ model: 'x', tps: 1, percent: 50, fillSource: 'real', used: null, window: 8192 });
  assert.match(real, /title="50% of window"/);
  assert.match(real, /aria-label="Context 50% of window"/);
});

test('reset clears everything', () => {
  const s = reduceCockpit(
    { model: 'x', tps: 1, percent: 1, window: 1, used: 1, fillSource: 'real' },
    { type: 'cockpit_reset' }
  );
  assert.deepEqual(s, { model: null, tps: null, window: null, percent: null, fillSource: null, used: null });
});

test('createCockpit skips setting innerHTML when the rendered output is unchanged', () => {
  let setCount = 0;
  let value = '';
  const mountEl = {
    set innerHTML(v) {
      setCount += 1;
      value = v;
    },
    get innerHTML() {
      return value;
    },
  };
  const c = createCockpit(mountEl);
  const afterCreate = setCount;
  c.push({ type: 'model_info', model: 'm' });
  const afterFirstPush = setCount;
  assert.ok(afterFirstPush > afterCreate, 'expected a render when content changes');
  c.push({ type: 'model_info', model: 'm' });
  assert.equal(setCount, afterFirstPush, 'expected no re-render for an identical event');
});
