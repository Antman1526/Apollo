import assert from 'node:assert/strict';
import test from 'node:test';

const lifecycle = await import('../static/js/chat/requestLifecycle.js');

test('only connection-class failures are recoverable', () => {
  assert.equal(lifecycle.isRecoverableStreamError(new TypeError('fetch failed')), true);
  assert.equal(lifecycle.isRecoverableStreamError(new Error('ECONNRESET')), true);
  assert.equal(lifecycle.isRecoverableStreamError(new Error('HTTP 500')), false);
  assert.equal(lifecycle.isRecoverableStreamError(new Error('JSON parse failure')), false);
});

test('recovery prompt preserves only a bounded tail of partial output', () => {
  const prompt = lifecycle.buildRecoveryPrompt('x'.repeat(500));
  assert.match(prompt, /x{400}/);
  assert.doesNotMatch(prompt, /x{401}/);
  assert.match(lifecycle.buildRecoveryPrompt(''), /produced anything/);
});
