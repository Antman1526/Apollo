import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';

global.document = {
  readyState: 'loading',
  addEventListener() {},
  getElementById() { return null; },
};
global.window = { open() {} };

const paperclip = await import('../static/js/paperclip.js');

test('normalizes Paperclip live events into an agent floor model', () => {
  const state = paperclip.createFloorState();

  paperclip.applyFloorEvent(state, {
    type: 'agent.status',
    payload: {
      agentId: 'researcher',
      name: 'Researcher',
      status: 'running',
      task: 'Read source pack',
    },
  });
  paperclip.applyFloorEvent(state, {
    type: 'heartbeat.run.log',
    payload: {
      agentId: 'researcher',
      chunk: 'Scanning routes/paperclip_routes.py',
    },
  });

  assert.equal(state.agents.get('researcher').zone, 'working');
  assert.equal(state.agents.get('researcher').thinking, true);
  assert.deepEqual(state.agents.get('researcher').transcript, [
    'Scanning routes/paperclip_routes.py',
  ]);
});

test('renders agents as little Lego-like humans with task state', () => {
  const html = paperclip.renderLegoAgentHTML({
    id: 'coder',
    name: 'Coder',
    role: 'coding',
    zone: 'working',
    task: 'Implement The Floor',
    thinking: true,
  });

  assert.match(html, /paperclip-lego-agent/);
  assert.match(html, /paperclip-lego-head/);
  assert.match(html, /paperclip-lego-torso/);
  assert.match(html, /Coder/);
  assert.match(html, /Implement The Floor/);
  assert.doesNotMatch(html, /<script/i);
});

test('renders a walking workspace with positioned agents and interactions', () => {
  const state = paperclip.createFloorState();
  paperclip.applyFloorEvent(state, {
    type: 'agent.status',
    payload: { agentId: 'researcher', name: 'Researcher', role: 'research', status: 'running' },
  });
  paperclip.applyFloorEvent(state, {
    type: 'agent.status',
    payload: { agentId: 'coder', name: 'Coder', role: 'coding', status: 'review' },
  });
  paperclip.applyFloorEvent(state, {
    type: 'activity.logged',
    payload: { fromAgentId: 'researcher', toAgentId: 'coder', message: 'Handing off source notes.' },
  });

  const layout = paperclip.computeWorkspaceLayout(state);
  assert.equal(layout.agents.length, 2);
  assert.equal(layout.interactions.length, 1);
  assert.ok(layout.agents.every((agent) => Number.isFinite(agent.x) && Number.isFinite(agent.y)));

  const html = paperclip.renderWorkspaceHTML(state);
  assert.match(html, /paperclip-workspace-map/);
  assert.match(html, /paperclip-roaming-agent/);
  assert.match(html, /paperclip-walk-path/);
  assert.match(html, /paperclip-interaction-arc/);
  assert.match(html, /Handing off source notes/);
});

test('shows agents talking and sitting at desks while performing tasks', () => {
  const state = paperclip.createFloorState();
  paperclip.applyFloorEvent(state, {
    type: 'agent.status',
    payload: {
      agentId: 'builder',
      name: 'Builder',
      role: 'coding',
      status: 'running',
      task: 'Write collector tests',
    },
  });
  paperclip.applyFloorEvent(state, {
    type: 'agent.status',
    payload: {
      agentId: 'helper',
      name: 'Helper',
      role: 'ops',
      status: 'review',
      task: 'Check auth notes',
    },
  });
  paperclip.applyFloorEvent(state, {
    type: 'activity.logged',
    payload: {
      fromAgentId: 'helper',
      toAgentId: 'builder',
      message: 'Need a token plan.',
    },
  });

  const layout = paperclip.computeWorkspaceLayout(state);
  const builder = layout.agents.find((agent) => agent.id === 'builder');
  assert.equal(builder.pose, 'talking');
  assert.equal(builder.workingAtDesk, true);

  const html = paperclip.renderWorkspaceHTML(state);
  assert.match(html, /pose-talking/);
  assert.match(html, /paperclip-speech-burst/);
  assert.match(html, /paperclip-agent-desk/);
  assert.match(html, /paperclip-desk-screen/);
  assert.match(html, /Write collector tests/);
});

test('allowlists role CSS classes while preserving role labels as text', () => {
  const html = paperclip.renderLegoAgentHTML({
    id: 'reviewer',
    name: 'Reviewer',
    role: 'review danger" onclick="alert(1)',
    zone: 'review',
    task: 'Inspect payload safety',
    thinking: false,
  });

  assert.match(html, /role-review/);
  assert.doesNotMatch(html, /onclick/);
  assert.doesNotMatch(html, /role-review danger/);
});

test('agent selection uses one delegated click handler per document', () => {
  assert.equal(typeof paperclip.bindAgentSelection, 'function');

  let delegatedHandler = null;
  const fakeDocument = {
    addEventListener(type, handler, options) {
      if (type === 'click') {
        delegatedHandler = handler;
        assert.equal(options, true);
      }
    },
    querySelectorAll() {
      throw new Error('selection binding should not query and bind every rendered agent');
    },
  };

  const calls = [];
  paperclip.bindAgentSelection(fakeDocument, (agentId) => calls.push(agentId));
  paperclip.bindAgentSelection(fakeDocument, (agentId) => calls.push(agentId));

  assert.equal(typeof delegatedHandler, 'function');
  delegatedHandler({
    target: {
      closest(selector) {
        assert.equal(selector, '[data-agent-id]');
        return {
          dataset: { agentId: 'coder' },
          closest() { return { classList: { contains: () => false } }; },
        };
      },
    },
    preventDefault() {},
  });

  assert.deepEqual(calls, ['coder']);
});

test('starts a live Paperclip event stream when EventSource is available', () => {
  assert.equal(typeof paperclip.createLiveEventStream, 'function');

  const received = [];
  class FakeEventSource {
    constructor(url, options) {
      this.url = url;
      this.options = options;
      FakeEventSource.instances.push(this);
    }

    close() {
      this.closed = true;
    }
  }
  FakeEventSource.instances = [];

  const stream = paperclip.createLiveEventStream({
    EventSource: FakeEventSource,
    url: '/api/paperclip/stream',
    onEvent: (event) => received.push(event),
  });

  assert.equal(stream.state, 'connecting');
  assert.equal(FakeEventSource.instances[0].url, '/api/paperclip/stream');
  FakeEventSource.instances[0].onmessage({ data: '{"type":"agent.status","payload":{"agentId":"coder","status":"running"}}' });
  assert.deepEqual(received, [{ type: 'agent.status', payload: { agentId: 'coder', status: 'running' } }]);
  stream.close();
  assert.equal(FakeEventSource.instances[0].closed, true);
});

test('falls back to preview when live streaming is unavailable', () => {
  const stream = paperclip.createLiveEventStream({
    EventSource: undefined,
    url: '/api/paperclip/stream',
    onEvent: () => {},
  });

  assert.equal(stream.state, 'preview');
  assert.equal(typeof stream.close, 'function');
});

test('falls back to preview when Apollo reports the collector is unavailable', () => {
  const errors = [];
  class FakeEventSource {
    constructor() {
      FakeEventSource.instance = this;
    }

    close() {
      this.closed = true;
    }
  }

  const stream = paperclip.createLiveEventStream({
    EventSource: FakeEventSource,
    onError: (event) => errors.push(event),
  });

  FakeEventSource.instance.onmessage({
    data: '{"type":"paperclip.stream.unavailable","payload":{"reason":"collector_unavailable"}}',
  });

  assert.equal(stream.state, 'preview');
  assert.equal(errors[0].payload.reason, 'collector_unavailable');
});

test('package test script runs the Paperclip JavaScript tests', () => {
  const pkg = JSON.parse(fs.readFileSync(new URL('../package.json', import.meta.url), 'utf8'));

  assert.match(pkg.scripts?.['test:js'] || '', /node --test tests\/test_paperclip_floor_ui\.mjs/);
});

test('status updates both sidebar and rail Paperclip launchers', () => {
  assert.equal(typeof paperclip.applyStatus, 'function');

  const elements = new Map([
    ['tool-paperclip-btn', { style: { display: 'none' } }],
    ['rail-paperclip', { style: { display: 'none' } }],
    ['set-paperclip-section', { dataset: {} }],
    ['set-paperclipState', { textContent: '' }],
    ['set-paperclipEndpoint', { textContent: '' }],
    ['set-paperclipOpen', { disabled: true }],
  ]);
  const originalDocument = global.document;
  global.document = {
    getElementById(id) {
      return elements.get(id) || null;
    },
  };

  try {
    paperclip.applyStatus({ enabled: true, browser_url: 'http://paperclip.test', model_endpoint: 'apollo' });
    assert.equal(elements.get('tool-paperclip-btn').style.display, '');
    assert.equal(elements.get('rail-paperclip').style.display, '');
    assert.equal(elements.get('set-paperclipOpen').disabled, false);

    paperclip.applyStatus({ enabled: false });
    assert.equal(elements.get('tool-paperclip-btn').style.display, 'none');
    assert.equal(elements.get('rail-paperclip').style.display, 'none');
    assert.equal(elements.get('set-paperclipOpen').disabled, true);
  } finally {
    global.document = originalDocument;
  }
});
