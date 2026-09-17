import assert from 'node:assert/strict';
import test from 'node:test';

import { createVadGate, createMicVad } from '../static/js/vad.js';
import { resolveVadConfig, VAD_DEFAULTS } from '../static/js/voiceCall.js';

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

// ── createMicVad: the ONE onLevel tap in this module (createVadGate itself has
// no onLevel option — see push() above) ──
//
// createMicVad touches Web Audio globals, so these tests stand up a minimal
// fake AudioContext/analyser and a controllable requestAnimationFrame that
// captures the tick callback instead of auto-scheduling it (auto-invoking
// would recurse forever, since tick() re-arms itself via rAF at its end).
// Each test fires exactly one frame by calling env.tick().
function makeFakeAudioEnv(initialRms) {
  let currentRms = initialRms;
  let capturedTick = null;
  const prevWindow = global.window;
  const prevRAF = global.requestAnimationFrame;
  const prevCAF = global.cancelAnimationFrame;

  class FakeAnalyser {
    constructor() { this.fftSize = 512; }
    connect() {}
    getFloatTimeDomainData(buf) { buf.fill(currentRms); }
  }
  class FakeAudioContext {
    constructor() { this.state = 'running'; }
    createMediaStreamSource() { return { connect() {}, disconnect() {} }; }
    createAnalyser() { return new FakeAnalyser(); }
    resume() {}
    close() {}
  }

  global.window = { AudioContext: FakeAudioContext };
  global.requestAnimationFrame = (fn) => { capturedTick = fn; return 1; };
  global.cancelAnimationFrame = () => {};

  return {
    tick() {
      assert.ok(capturedTick, 'expected createMicVad to have scheduled a tick');
      capturedTick();
    },
    restore() {
      global.window = prevWindow;
      global.requestAnimationFrame = prevRAF;
      global.cancelAnimationFrame = prevCAF;
    },
  };
}

test('createMicVad plumbs the live rms into onLevel on every unpaused tick', () => {
  const env = makeFakeAudioEnv(0.05);
  try {
    const levels = [];
    const events = [];
    const gate = createVadGate({ threshold: 0.02, silenceMs: 1000 });
    const mic = createMicVad({
      stream: {},
      gate,
      onEvent: (ev) => events.push(ev),
      onLevel: (rms) => levels.push(rms),
    });
    env.tick();
    assert.equal(levels.length, 1);
    assert.ok(Math.abs(levels[0] - 0.05) < 1e-9, `expected onLevel(~0.05), got ${levels[0]}`);
    assert.deepEqual(events, ['speechstart'], 'gate/onEvent behavior is unaffected by onLevel');
    mic.destroy();
  } finally {
    env.restore();
  }
});

test('createMicVad: a throwing onLevel does not change onEvent output', () => {
  const env = makeFakeAudioEnv(0.05);
  try {
    const events = [];
    const gate = createVadGate({ threshold: 0.02, silenceMs: 1000 });
    const mic = createMicVad({
      stream: {},
      gate,
      onEvent: (ev) => events.push(ev),
      onLevel: () => { throw new Error('boom'); },
    });
    assert.doesNotThrow(() => env.tick());
    assert.deepEqual(events, ['speechstart']);
    mic.destroy();
  } finally {
    env.restore();
  }
});

// ── resolveVadConfig: pure toggle-state → effective VAD config ──

test('resolveVadConfig falls back to defaults for empty/undefined input', () => {
  assert.deepEqual(resolveVadConfig(), VAD_DEFAULTS);
  assert.deepEqual(resolveVadConfig({}), VAD_DEFAULTS);
  assert.deepEqual(resolveVadConfig(null), VAD_DEFAULTS);
});

test('resolveVadConfig uses persisted overrides when valid', () => {
  const cfg = resolveVadConfig({ voiceVadThreshold: 0.05, voiceSilenceMs: 800 });
  assert.equal(cfg.threshold, 0.05);
  assert.equal(cfg.silenceMs, 800);
});

test('resolveVadConfig rejects non-finite / non-positive values per-field', () => {
  // Bad threshold falls back but a valid silenceMs is still honored, and vice versa.
  assert.deepEqual(resolveVadConfig({ voiceVadThreshold: 0, voiceSilenceMs: 900 }),
    { threshold: VAD_DEFAULTS.threshold, silenceMs: 900 });
  assert.deepEqual(resolveVadConfig({ voiceVadThreshold: -1 }),
    { threshold: VAD_DEFAULTS.threshold, silenceMs: VAD_DEFAULTS.silenceMs });
  assert.deepEqual(resolveVadConfig({ voiceSilenceMs: 'abc', voiceVadThreshold: 0.03 }),
    { threshold: 0.03, silenceMs: VAD_DEFAULTS.silenceMs });
  assert.deepEqual(resolveVadConfig({ voiceSilenceMs: NaN }),
    { threshold: VAD_DEFAULTS.threshold, silenceMs: VAD_DEFAULTS.silenceMs });
});

test('resolveVadConfig coerces numeric strings', () => {
  const cfg = resolveVadConfig({ voiceVadThreshold: '0.04', voiceSilenceMs: '1500' });
  assert.equal(cfg.threshold, 0.04);
  assert.equal(cfg.silenceMs, 1500);
});
