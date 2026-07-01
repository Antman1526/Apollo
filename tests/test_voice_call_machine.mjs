import assert from 'node:assert/strict';
import test from 'node:test';

import { createCallMachine } from '../static/js/voiceCall.js';

function spyEffects() {
  const calls = [];
  const rec = (name) => (arg) => calls.push([name, arg]);
  return {
    calls,
    startCapture: rec('startCapture'),
    stopCapture: rec('stopCapture'),
    submitMessage: rec('submitMessage'),
    speak: rec('speak'),
    stopSpeak: rec('stopSpeak'),
    teardown: rec('teardown'),
    onState: rec('onState'),
  };
}

test('start moves idle -> listening', () => {
  const eff = spyEffects();
  const m = createCallMachine(eff);
  assert.equal(m.state, 'idle');
  m.dispatch('start');
  assert.equal(m.state, 'listening');
});

test('speechStart begins capture', () => {
  const eff = spyEffects();
  const m = createCallMachine(eff);
  m.dispatch('start');
  m.dispatch('speechStart');
  assert.equal(m.state, 'capturing');
  assert.ok(eff.calls.some(([n]) => n === 'startCapture'));
});

test('speechEnd stops capture and transcribes', () => {
  const eff = spyEffects();
  const m = createCallMachine(eff);
  m.dispatch('start');
  m.dispatch('speechStart');
  m.dispatch('speechEnd');
  assert.equal(m.state, 'transcribing');
  assert.ok(eff.calls.some(([n]) => n === 'stopCapture'));
});

test('non-empty transcript submits and goes to thinking', () => {
  const eff = spyEffects();
  const m = createCallMachine(eff);
  m.dispatch('start');
  m.dispatch('speechStart');
  m.dispatch('speechEnd');
  m.dispatch('transcribed', { text: '  hello  ' });
  assert.equal(m.state, 'thinking');
  const submit = eff.calls.find(([n]) => n === 'submitMessage');
  assert.deepEqual(submit, ['submitMessage', 'hello']);
});

test('empty transcript returns to listening without submit', () => {
  const eff = spyEffects();
  const m = createCallMachine(eff);
  m.dispatch('start');
  m.dispatch('speechStart');
  m.dispatch('speechEnd');
  m.dispatch('transcribed', { text: '   ' });
  assert.equal(m.state, 'listening');
  assert.ok(!eff.calls.some(([n]) => n === 'submitMessage'));
});

test('assistantComplete speaks the reply', () => {
  const eff = spyEffects();
  const m = createCallMachine(eff);
  m.dispatch('start');
  m.dispatch('speechStart');
  m.dispatch('speechEnd');
  m.dispatch('transcribed', { text: 'hi' });
  m.dispatch('assistantComplete', { text: 'hello there' });
  assert.equal(m.state, 'speaking');
  assert.deepEqual(eff.calls.find(([n]) => n === 'speak'), ['speak', 'hello there']);
});

test('speakEnd returns to listening', () => {
  const eff = spyEffects();
  const m = createCallMachine(eff);
  m.dispatch('start');
  m.dispatch('speechStart');
  m.dispatch('speechEnd');
  m.dispatch('transcribed', { text: 'hi' });
  m.dispatch('assistantComplete', { text: 'reply' });
  m.dispatch('speakEnd');
  assert.equal(m.state, 'listening');
});

test('barge-in: speaking + speechStart stops speech and captures', () => {
  const eff = spyEffects();
  const m = createCallMachine(eff);
  m.dispatch('start');
  m.dispatch('speechStart');
  m.dispatch('speechEnd');
  m.dispatch('transcribed', { text: 'hi' });
  m.dispatch('assistantComplete', { text: 'a long reply' });
  m.dispatch('speechStart');
  assert.equal(m.state, 'capturing');
  assert.ok(eff.calls.some(([n]) => n === 'stopSpeak'));
  assert.ok(eff.calls.some(([n]) => n === 'startCapture'));
});

test('end tears down and returns to idle from any state', () => {
  const eff = spyEffects();
  const m = createCallMachine(eff);
  m.dispatch('start');
  m.dispatch('speechStart');
  m.dispatch('end');
  assert.equal(m.state, 'idle');
  assert.ok(eff.calls.some(([n]) => n === 'teardown'));
});

test('assistantComplete with empty text skips speaking', () => {
  const eff = spyEffects();
  const m = createCallMachine(eff);
  m.dispatch('start');
  m.dispatch('speechStart');
  m.dispatch('speechEnd');
  m.dispatch('transcribed', { text: 'hi' });
  m.dispatch('assistantComplete', { text: '' });
  assert.equal(m.state, 'listening');
  assert.ok(!eff.calls.some(([n]) => n === 'speak'));
});

test('onState fires on each transition', () => {
  const eff = spyEffects();
  const m = createCallMachine(eff);
  m.dispatch('start');
  const states = eff.calls.filter(([n]) => n === 'onState').map(([, s]) => s);
  assert.deepEqual(states, ['listening']);
});

test('synchronous speakEnd from speak() does not deadlock (TTS-unavailable path)', () => {
  // When TTS is disabled, the speak effect fires speakEnd immediately. The
  // machine must already be in `speaking` when that lands, or it parks in
  // `speaking` forever. Simulate the re-entrant synchronous dispatch.
  let m;
  const eff = {
    speak: () => { m.dispatch('speakEnd'); },
  };
  m = createCallMachine(eff);
  m.dispatch('start');
  m.dispatch('speechStart');
  m.dispatch('speechEnd');
  m.dispatch('transcribed', { text: 'hi' });
  m.dispatch('assistantComplete', { text: 'reply' });
  assert.equal(m.state, 'listening', 'must not be stuck in speaking after a synchronous speakEnd');
});
