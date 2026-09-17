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
  assert.equal(stationForTool('create_document'), 'files');
  assert.equal(stationForTool('manage_documents'), 'files');
  assert.equal(stationForTool('unknown_tool'), 'desk');
  assert.equal(stationForTool(''), 'desk');
  assert.equal(stationForTool(null), 'desk');
});

test('strip renders stations and moves the figure on tool events', () => {
  const strip = createFloorStrip();
  const html0 = strip.render();
  assert.match(html0, /chat-floor-station--web/);
  assert.match(html0, /viewBox="0 0 1200 360"/);
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

// A tool_output can land for a tool the figure has already walked away from.
test('a late tool_output marks its own station without freeing the current one', () => {
  const strip = createFloorStrip();
  strip.onToolStart('bash');
  strip.onToolEnd('web_search', false);
  assert.equal(strip.state().failed.includes('web'), true);
  assert.equal(strip.state().at, 'shell');
  assert.equal(strip.state().busy, true);
});

test('the walked path is empty until a station is visited', () => {
  const strip = createFloorStrip();
  const empty = strip.render().match(/class="chat-floor-path" points="([^"]*)"/);
  assert.ok(empty);
  assert.equal(empty[1], '');
  strip.onToolStart('bash');
  const walked = strip.render().match(/class="chat-floor-path" points="([^"]*)"/);
  assert.ok(walked);
  assert.equal(walked[1].split(' ').length, 2);
});

// Minimal element shim: records setAttribute calls and innerHTML assignments.
function makeNode() {
  return { attrs: {}, setAttribute(k, v) { this.attrs[k] = v; } };
}

function makeRoot(resolve) {
  const root = {
    writes: [],
    nodes: {},
    set innerHTML(v) { this.writes.push(v); },
    get innerHTML() { return this.writes[this.writes.length - 1]; },
    querySelector(sel) { return resolve(sel, root); },
  };
  return root;
}

test('mount patches the mounted nodes instead of re-rendering', () => {
  const strip = createFloorStrip();
  const root = makeRoot((sel, r) => {
    if (!r.nodes[sel]) r.nodes[sel] = makeNode();
    return r.nodes[sel];
  });
  strip.mount(root);
  assert.equal(root.writes.length, 1);
  const fig = root.querySelector('.chat-floor-fig');
  const before = fig.attrs.transform;
  strip.onToolStart('bash');
  assert.equal(root.writes.length, 1, 'no second innerHTML assignment');
  assert.notEqual(fig.attrs.transform, before);
  assert.match(fig.attrs.transform, /^translate\(/);
  const station = root.querySelector('[data-station="shell"]');
  assert.match(station.attrs.class, /chat-floor-station--active/);
});

test('mount falls back to a full re-render when the figure node is gone', () => {
  const strip = createFloorStrip();
  const root = makeRoot((sel) => (sel === '.chat-floor-fig' ? null : makeNode()));
  strip.mount(root);
  assert.equal(root.writes.length, 1);
  strip.onToolStart('bash');
  assert.equal(root.writes.length, 2);
  assert.match(root.writes[1], /chat-floor-station--shell[^"]*chat-floor-station--active/);
});
