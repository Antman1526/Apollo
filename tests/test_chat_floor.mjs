import test from 'node:test';
import assert from 'node:assert/strict';
import { stationForTool, createFloorStrip } from '../static/js/chatFloor.js';

test('tools map to stations', () => {
  assert.equal(stationForTool('web_search'), 'web');
  assert.equal(stationForTool('web_fetch'), 'web');
  assert.equal(stationForTool('bash'), 'shell');
  assert.equal(stationForTool('python'), 'shell');
  assert.equal(stationForTool('python_session'), 'shell');
  assert.equal(stationForTool('browser'), 'browser');
  assert.equal(stationForTool('manage_memory'), 'memory');
  assert.equal(stationForTool('manage_skills'), 'memory');
  assert.equal(stationForTool('reference_search'), 'memory');
  assert.equal(stationForTool('read_file'), 'files');
  assert.equal(stationForTool('write_file'), 'files');
  assert.equal(stationForTool('unknown_tool'), 'desk');
  assert.equal(stationForTool(''), 'desk');
  assert.equal(stationForTool(null), 'desk');
});

test('strip renders stations and moves the figure on tool events', () => {
  const strip = createFloorStrip();
  const html0 = strip.render();
  assert.match(html0, /chat-floor-station--web/);
  assert.match(html0, /viewBox="0 0 1200 420"/);
  strip.onToolStart('web_search');
  assert.equal(strip.state().at, 'web');
  assert.equal(strip.state().busy, true);
  strip.onToolEnd('web_search', true);
  assert.equal(strip.state().busy, false);
  assert.deepEqual(strip.state().visited, ['web']);
});

test('failed tool marks the station and the figure returns to the desk when idle', () => {
  const strip = createFloorStrip();
  strip.onToolStart('bash');
  strip.onToolEnd('bash', false);
  assert.equal(strip.state().failed.includes('shell'), true);
  strip.onTurnEnd();
  assert.equal(strip.state().at, 'desk');
  assert.match(strip.render(), /chat-floor-station--shell[^"]*chat-floor-station--failed/);
});

test('repeat visits are counted but not duplicated consecutively', () => {
  const strip = createFloorStrip();
  strip.onToolStart('bash');
  strip.onToolEnd('bash', true);
  strip.onToolStart('python');
  strip.onToolEnd('python', true);
  strip.onToolStart('web_search');
  strip.onToolEnd('web_search', true);
  assert.deepEqual(strip.state().visited, ['shell', 'web']);
  assert.equal(strip.state().count.shell, 2);
  assert.equal(strip.state().count.web, 1);
});
