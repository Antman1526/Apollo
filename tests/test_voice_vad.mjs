import assert from 'node:assert/strict';
import test from 'node:test';

import { createVadGate } from '../static/js/vad.js';

test('stays silent below threshold', () => {
  const g = createVadGate({ threshold: 0.02, silenceMs: 1000 });
  assert.equal(g.push(0.0, 0), null);
  assert.equal(g.push(0.01, 50), null);
  assert.equal(g.isSpeaking, false);
});

test('emits speechstart when energy crosses threshold', () => {
  const g = createVadGate({ threshold: 0.02, silenceMs: 1000 });
  assert.equal(g.push(0.05, 0), 'speechstart');
  assert.equal(g.isSpeaking, true);
  assert.equal(g.push(0.05, 50), null, 'no repeat speechstart while loud');
});

test('emits speechend after sustained silence', () => {
  const g = createVadGate({ threshold: 0.02, silenceMs: 1000 });
  g.push(0.05, 0);
  assert.equal(g.push(0.0, 500), null, 'silence shorter than silenceMs stays speaking');
  assert.equal(g.push(0.0, 1000), 'speechend', 'silence >= silenceMs ends the turn');
  assert.equal(g.isSpeaking, false);
});

test('a loud blip during silence resets the silence timer', () => {
  const g = createVadGate({ threshold: 0.02, silenceMs: 1000 });
  g.push(0.05, 0);
  g.push(0.0, 900);
  assert.equal(g.push(0.05, 950), null, 'still speaking, timer reset');
  assert.equal(g.push(0.0, 1500), null, 'only 550ms of silence since blip');
  assert.equal(g.push(0.0, 1950), 'speechend');
});

test('reset() returns to not-speaking', () => {
  const g = createVadGate({ threshold: 0.02, silenceMs: 1000 });
  g.push(0.05, 0);
  g.reset();
  assert.equal(g.isSpeaking, false);
  assert.equal(g.push(0.0, 10), null);
});
