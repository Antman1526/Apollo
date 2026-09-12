import assert from 'node:assert/strict';
import test from 'node:test';

import { captionFor, deviceToClient } from '../static/js/browserCopilot.js';

// ── captionFor ──

test('captionFor narrates each agent action on start', () => {
  assert.equal(captionFor({ action: 'navigate', phase: 'start', detail: 'https://example.com/a?b=1' }), 'Agent: opening example.com');
  assert.equal(captionFor({ action: 'navigate', phase: 'start', detail: 'http://localhost:3000/x' }), 'Agent: opening localhost:3000');
  assert.equal(captionFor({ action: 'click', phase: 'start', detail: 'button.login' }), 'Agent: clicking `button.login`');
  assert.equal(captionFor({ action: 'type', phase: 'start', detail: '"hello…" into #q' }), 'Agent: typing "hello…" into #q');
  assert.equal(captionFor({ action: 'get_visible_text', phase: 'start' }), 'Agent: reading the page');
  assert.equal(captionFor({ action: 'get_page_html', phase: 'start' }), 'Agent: reading the page');
  assert.equal(captionFor({ action: 'screenshot', phase: 'start' }), 'Agent: taking a screenshot');
  assert.equal(captionFor({ action: 'wait_for_selector', phase: 'start', detail: '.ready' }), 'Agent: waiting for `.ready`');
  assert.equal(captionFor({ action: 'execute_script', phase: 'start' }), 'Agent: running a script');
  assert.equal(captionFor({ action: 'go_back', phase: 'start' }), 'Agent: going back');
  assert.equal(captionFor({ action: 'go_forward', phase: 'start' }), 'Agent: going forward');
  assert.equal(captionFor({ action: 'reload_page', phase: 'start' }), 'Agent: reloading the page');
});

test('captionFor reports failures with the detail and stays quiet on done', () => {
  assert.equal(captionFor({ action: 'click', phase: 'error', detail: 'Timeout 15000ms exceeded' }), 'Agent: failed — Timeout 15000ms exceeded');
  assert.equal(captionFor({ action: 'click', phase: 'error' }), 'Agent: failed — click');
  assert.equal(captionFor({ action: 'click', phase: 'done', detail: 'button' }), '');
  assert.equal(captionFor({ action: 'navigate', phase: 'done' }), '');
});

test('captionFor tolerates unknown actions and garbage input', () => {
  assert.equal(captionFor({ action: 'hover_thing', phase: 'start' }), 'Agent: hover thing');
  assert.equal(captionFor({ action: 'navigate', phase: 'start', detail: 'not a url' }), 'Agent: opening not a url');
  assert.equal(captionFor({ action: 'navigate', phase: 'start' }), 'Agent: opening a page');
  assert.equal(captionFor(null), '');
  assert.equal(captionFor({}), '');
});

// ── deviceToClient ──

test('deviceToClient maps screencast device px to client px (inverse of canvasCoords)', () => {
  const rect = { left: 100, top: 50, width: 400, height: 300 };
  assert.deepEqual(deviceToClient(0, 0, rect, 800, 600), { x: 100, y: 50 });
  assert.deepEqual(deviceToClient(800, 600, rect, 800, 600), { x: 500, y: 350 });
  assert.deepEqual(deviceToClient(400, 150, rect, 800, 600), { x: 300, y: 125 });
  // Round trip against canvasCoords' forward formula.
  const p = deviceToClient(123, 456, rect, 800, 600);
  assert.equal(Math.round((p.x - rect.left) * (800 / rect.width)), 123);
  assert.equal(Math.round((p.y - rect.top) * (600 / rect.height)), 456);
});

test('deviceToClient returns null when it cannot map', () => {
  const rect = { left: 0, top: 0, width: 400, height: 300 };
  assert.equal(deviceToClient(1, 1, null, 800, 600), null);
  assert.equal(deviceToClient(1, 1, { left: 0, top: 0, width: 0, height: 300 }, 800, 600), null);
  assert.equal(deviceToClient(1, 1, rect, 0, 600), null);
  assert.equal(deviceToClient(1, 1, rect, 800, undefined), null);
  assert.equal(deviceToClient(null, 1, rect, 800, 600), null);
  assert.equal(deviceToClient(NaN, 1, rect, 800, 600), null);
  assert.equal(deviceToClient('5', 1, rect, 800, 600), null);
});
