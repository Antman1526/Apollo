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

test('renders the focus figure as the same SVG minifig used on the floor', () => {
  const html = paperclip.renderFocusFigureHTML({
    id: 'coder',
    name: 'Coder',
    role: 'coding',
    zone: 'working',
    task: 'Implement The Floor',
    thinking: true,
  });

  assert.match(html, /paperclip-focus-card/);
  assert.match(html, /paperclip-fig/);
  assert.match(html, /paperclip-fig-head/);
  assert.match(html, /paperclip-fig-torso/);
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
  // Simulate one rendered frame so subsequent movement is walk-animated.
  paperclip.commitWorkspaceLayout(state, paperclip.computeWorkspaceLayout(state));
  paperclip.applyFloorEvent(state, {
    type: 'activity.logged',
    payload: { fromAgentId: 'researcher', toAgentId: 'coder', message: 'Handing off source notes.' },
  });

  const layout = paperclip.computeWorkspaceLayout(state);
  assert.equal(layout.agents.length, 2);
  assert.equal(layout.interactions.length, 1);
  assert.ok(layout.agents.every((agent) => Number.isFinite(agent.x) && Number.isFinite(agent.y)));
  const researcher = layout.agents.find((agent) => agent.id === 'researcher');
  assert.equal(researcher.moving, true);

  const html = paperclip.renderWorkspaceHTML(state);
  assert.match(html, /paperclip-workspace-map/);
  assert.match(html, /paperclip-roaming-agent/);
  assert.match(html, /paperclip-walk-path/);
  assert.match(html, /paperclip-interaction-arc/);
  assert.match(html, /Handing off source notes/);
});

test('agents walk exactly once per move, not on every render tick', () => {
  const state = paperclip.createFloorState();
  paperclip.applyFloorEvent(state, {
    type: 'agent.status',
    payload: { agentId: 'a1', name: 'Ada', status: 'queued' },
  });

  // Fresh agents appear in place without a walk animation.
  const first = paperclip.computeWorkspaceLayout(state);
  assert.equal(first.agents[0].moving, false);
  paperclip.commitWorkspaceLayout(state, first);

  // A zone change walks once...
  paperclip.applyFloorEvent(state, {
    type: 'agent.status',
    payload: { agentId: 'a1', status: 'review' },
  });
  const second = paperclip.computeWorkspaceLayout(state);
  assert.equal(second.agents[0].moving, true);
  paperclip.commitWorkspaceLayout(state, second);

  // ...and the next render tick must NOT replay the walk.
  const third = paperclip.computeWorkspaceLayout(state);
  assert.equal(third.agents[0].moving, false);
  assert.equal(third.agents[0].fromX, third.agents[0].x);
  assert.equal(third.agents[0].fromY, third.agents[0].y);
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

test('assigns each agent a stable personal desk in the office', () => {
  const state = paperclip.createFloorState();
  paperclip.applyFloorEvent(state, {
    type: 'agent.status',
    payload: { agentId: 'a1', name: 'Ada', status: 'queued' },
  });
  paperclip.applyFloorEvent(state, {
    type: 'agent.status',
    payload: { agentId: 'b2', name: 'Ben', status: 'running' },
  });

  const first = paperclip.computeWorkspaceLayout(state);
  assert.equal(first.desks.length, 2);
  const adaDesk = first.desks.find((desk) => desk.ownerId === 'a1');
  const benDesk = first.desks.find((desk) => desk.ownerId === 'b2');
  assert.ok(adaDesk && benDesk);
  assert.notDeepEqual([adaDesk.x, adaDesk.y], [benDesk.x, benDesk.y]);

  // Changing zones must not move the agent's assigned desk.
  paperclip.applyFloorEvent(state, {
    type: 'agent.status',
    payload: { agentId: 'a1', status: 'running' },
  });
  const second = paperclip.computeWorkspaceLayout(state);
  const adaDeskAfter = second.desks.find((desk) => desk.ownerId === 'a1');
  assert.deepEqual([adaDeskAfter.x, adaDeskAfter.y], [adaDesk.x, adaDesk.y]);
});

test('working agents sit at their own desk; idle agents stand by it', () => {
  const state = paperclip.createFloorState();
  paperclip.applyFloorEvent(state, {
    type: 'agent.status',
    payload: { agentId: 'a1', name: 'Ada', status: 'running', task: 'Wire SSE retry' },
  });
  paperclip.applyFloorEvent(state, {
    type: 'agent.status',
    payload: { agentId: 'b2', name: 'Ben', status: 'queued' },
  });

  const layout = paperclip.computeWorkspaceLayout(state);
  const ada = layout.agents.find((agent) => agent.id === 'a1');
  const adaDesk = layout.desks.find((desk) => desk.ownerId === 'a1');
  assert.equal(ada.x, adaDesk.x);
  assert.equal(ada.pose, 'sitting');
  assert.equal(adaDesk.active, true);

  const ben = layout.agents.find((agent) => agent.id === 'b2');
  const benDesk = layout.desks.find((desk) => desk.ownerId === 'b2');
  assert.equal(ben.x, benDesk.x);
  assert.equal(ben.pose, 'standing');
  assert.equal(benDesk.active, false);

  const html = paperclip.renderWorkspaceHTML(state);
  assert.match(html, /paperclip-desk-nameplate/);
  assert.match(html, /Ada/);
});

test('conversations pair the sender message with a task-based reply', () => {
  const state = paperclip.createFloorState();
  paperclip.applyFloorEvent(state, {
    type: 'agent.status',
    payload: { agentId: 'lead', name: 'Lead', status: 'review', task: 'Audit floor layout' },
  });
  paperclip.applyFloorEvent(state, {
    type: 'agent.status',
    payload: { agentId: 'dev', name: 'Dev', status: 'running', task: 'Ship desk view' },
  });
  paperclip.applyFloorEvent(state, {
    type: 'activity.logged',
    payload: { fromAgentId: 'lead', toAgentId: 'dev', message: 'How is the desk view going?' },
  });

  const layout = paperclip.computeWorkspaceLayout(state);
  assert.equal(layout.conversations.length, 1);
  const convo = layout.conversations[0];
  assert.equal(convo.fromText, 'How is the desk view going?');
  assert.match(convo.toText, /Ship desk view/);

  const html = paperclip.renderWorkspaceHTML(state);
  assert.match(html, /paperclip-chat-bubble/);
  assert.match(html, /How is the desk view going\?/);
  assert.match(html, /Ship desk view/);
});

test('conversations do not overwrite display names with agent ids', () => {
  const state = paperclip.createFloorState();
  paperclip.applyFloorEvent(state, {
    type: 'agent.status',
    payload: { agentId: 'lead', name: 'Lead', status: 'review' },
  });
  paperclip.applyFloorEvent(state, {
    type: 'agent.status',
    payload: { agentId: 'dev', name: 'Dev', status: 'running' },
  });
  paperclip.applyFloorEvent(state, {
    type: 'activity.logged',
    payload: { fromAgentId: 'lead', toAgentId: 'dev', message: 'Ping' },
  });

  assert.equal(state.agents.get('lead').name, 'Lead');
  assert.equal(state.agents.get('dev').name, 'Dev');
});

test('the sender walks over to the receiver for the newest conversation', () => {
  const state = paperclip.createFloorState();
  paperclip.applyFloorEvent(state, {
    type: 'agent.status',
    payload: { agentId: 'lead', name: 'Lead', status: 'done' },
  });
  paperclip.applyFloorEvent(state, {
    type: 'agent.status',
    payload: { agentId: 'dev', name: 'Dev', status: 'running' },
  });
  paperclip.applyFloorEvent(state, {
    type: 'activity.logged',
    payload: { fromAgentId: 'lead', toAgentId: 'dev', message: 'Quick sync?' },
  });

  const layout = paperclip.computeWorkspaceLayout(state);
  const lead = layout.agents.find((agent) => agent.id === 'lead');
  const dev = layout.agents.find((agent) => agent.id === 'dev');
  assert.ok(Math.abs(lead.x - dev.x) <= 15, `sender should stand next to receiver (dx=${Math.abs(lead.x - dev.x)})`);
  assert.ok(Math.abs(lead.y - dev.y) <= 15, `sender should stand next to receiver (dy=${Math.abs(lead.y - dev.y)})`);
  assert.equal(lead.pose, 'talking');
  assert.equal(dev.pose, 'talking');
});

test('done agents walk to the exit door and depart after lingering', () => {
  const state = paperclip.createFloorState();
  paperclip.applyFloorEvent(state, {
    type: 'agent.status',
    payload: { agentId: 'a1', name: 'Ada', status: 'running', task: 'Ship the feature' },
  });
  paperclip.commitWorkspaceLayout(state, paperclip.computeWorkspaceLayout(state));

  // Finishing the task sends the agent walking to the exit door.
  paperclip.applyFloorEvent(state, {
    type: 'agent.status',
    payload: { agentId: 'a1', status: 'done' },
  });
  let layout = paperclip.computeWorkspaceLayout(state);
  const ada = layout.agents.find((agent) => agent.id === 'a1');
  assert.ok(ada, 'agent still visible while lingering at the door');
  assert.equal(ada.moving, true);
  assert.ok(ada.x <= 16 && ada.y >= 76, `should stand by the exit (${ada.x},${ada.y})`);

  const html = paperclip.renderWorkspaceHTML(state);
  assert.match(html, /paperclip-exit-door/);
  assert.match(html, /paperclip-exit-sign/);

  // After the linger window the agent has left the office...
  state.agents.get('a1').doneAt = Date.now() - 60000;
  layout = paperclip.computeWorkspaceLayout(state);
  assert.equal(layout.agents.find((agent) => agent.id === 'a1'), undefined);
  // ...but their desk stays, and the Board still knows them.
  assert.ok(layout.desks.find((desk) => desk.ownerId === 'a1'));
  assert.doesNotMatch(paperclip.renderWorkspaceHTML(state), /data-agent-id="a1"/);

  // New activity brings them back through the door.
  paperclip.applyFloorEvent(state, {
    type: 'agent.status',
    payload: { agentId: 'a1', status: 'running', task: 'Hotfix' },
  });
  layout = paperclip.computeWorkspaceLayout(state);
  assert.ok(layout.agents.find((agent) => agent.id === 'a1'));
});

test('status callouts narrate what blocked/review agents are doing', () => {
  const state = paperclip.createFloorState();
  paperclip.applyFloorEvent(state, {
    type: 'agent.status',
    payload: { agentId: 'ops', name: 'Ops', status: 'blocked', task: 'Need API creds' },
  });
  paperclip.applyFloorEvent(state, {
    type: 'agent.status',
    payload: { agentId: 'rev', name: 'Rev', status: 'review', task: 'Audit the diff' },
  });

  const layout = paperclip.computeWorkspaceLayout(state);
  assert.ok(layout.callouts.length >= 2, `expected callouts, got ${layout.callouts.length}`);
  const texts = layout.callouts.map((c) => c.text).join(' | ');
  assert.match(texts, /Need API creds/);
  assert.match(texts, /Audit the diff/);

  const html = paperclip.renderWorkspaceHTML(state);
  assert.match(html, /paperclip-callout-bubble/);
  assert.match(html, /Need API creds/);
});

test('busy agents murmur their current work when not in a conversation', () => {
  const state = paperclip.createFloorState();
  paperclip.applyFloorEvent(state, {
    type: 'agent.status',
    payload: { agentId: 'dev', name: 'Dev', status: 'running', task: 'Refactor hub' },
  });

  const layout = paperclip.computeWorkspaceLayout(state);
  assert.equal(layout.murmurs.length, 1);
  assert.match(layout.murmurs[0].text, /Refactor hub/);

  const html = paperclip.renderWorkspaceHTML(state);
  assert.match(html, /paperclip-murmur-bubble/);
  assert.match(html, /Refactor hub/);
});

test('renders an isometric office scene with furniture', () => {
  const state = paperclip.createFloorState();
  paperclip.applyFloorEvent(state, {
    type: 'agent.status',
    payload: { agentId: 'a1', name: 'Ada', status: 'running', task: 'Build the scene' },
  });

  const html = paperclip.renderWorkspaceHTML(state);
  assert.match(html, /paperclip-iso-scene/);
  assert.match(html, /viewBox="0 0 1200 740"/);
  assert.match(html, /paperclip-iso-floor/);
  assert.match(html, /paperclip-iso-wall/);
  assert.match(html, /paperclip-agent-desk/);
  assert.match(html, /station-review/);
  assert.match(html, /station-blocked/);
  assert.match(html, /station-done/);
  assert.match(html, /paperclip-iso-name/);
});

test('decorates the office with windows, a clock, desk props and status dots', () => {
  const state = paperclip.createFloorState();
  paperclip.applyFloorEvent(state, {
    type: 'agent.status',
    payload: { agentId: 'a1', name: 'Ada', status: 'running', task: 'Polish pass' },
  });
  paperclip.applyFloorEvent(state, {
    type: 'agent.status',
    payload: { agentId: 'b2', name: 'Ben', status: 'queued' },
  });

  const html = paperclip.renderWorkspaceHTML(state);
  assert.match(html, /paperclip-iso-window/);
  assert.match(html, /paperclip-window-light/);
  assert.match(html, /paperclip-wall-clock/);
  assert.match(html, /paperclip-desk-prop/);
  assert.match(html, /paperclip-chip-dot/);
  // Zone classes drive the status dot color.
  assert.match(html, /zone-working/);
  assert.match(html, /zone-backlog/);
});

test('projects logical floor coords onto the isometric stage', () => {
  const origin = paperclip.isoProject(0, 0);
  const right = paperclip.isoProject(100, 0);
  const down = paperclip.isoProject(0, 100);
  // +x heads right-and-down, +y heads left-and-down — classic 2:1-ish iso.
  assert.ok(right.px > origin.px && right.py > origin.py);
  assert.ok(down.px < origin.px && down.py > origin.py);
  // The far corner is the deepest point on screen.
  const far = paperclip.isoProject(100, 100);
  assert.ok(far.py > right.py && far.py > down.py);
});

test('depth-sorts agents and furniture together so the scene occludes correctly', () => {
  const state = paperclip.createFloorState();
  paperclip.applyFloorEvent(state, {
    type: 'agent.status',
    payload: { agentId: 'near', name: 'Near', status: 'review' }, // review table, deeper
  });
  paperclip.applyFloorEvent(state, {
    type: 'agent.status',
    payload: { agentId: 'far', name: 'Farr', status: 'running' }, // desk row, higher up
  });

  const html = paperclip.renderWorkspaceHTML(state);
  const farAt = html.indexOf('data-agent-id="far"');
  const nearAt = html.indexOf('data-agent-id="near"');
  const tableAt = html.indexOf('station-review');
  assert.ok(farAt !== -1 && nearAt !== -1 && tableAt !== -1);
  // SVG paints in document order: the desk agent is deeper into the room than
  // the review table, so it draws before that furniture, while the agent
  // seated at the table draws after it (ties paint agents after furniture).
  assert.ok(farAt < tableAt, 'desk agent should render behind the review table');
  assert.ok(nearAt > tableAt, 'review agent should render in front of the review table');
});

test('agents render inside the SVG scene so furniture can occlude them', () => {
  const state = paperclip.createFloorState();
  paperclip.applyFloorEvent(state, {
    type: 'agent.status',
    payload: { agentId: 'a1', name: 'Ada', status: 'running', task: 'Occlusion pass' },
  });

  const html = paperclip.renderWorkspaceHTML(state);
  const svgEnd = html.indexOf('</svg>');
  const agentAt = html.indexOf('data-agent-id="a1"');
  assert.ok(svgEnd !== -1 && agentAt !== -1);
  assert.ok(agentAt < svgEnd, 'agent markup must live inside the scene SVG');
  // Interactive semantics survive the move out of <button>.
  assert.match(html, /role="button"/);
  assert.match(html, /tabindex="0"/);
});

test('tolerates transient stream errors while EventSource reconnects', () => {
  class FakeEventSource {
    constructor() {
      this.readyState = 0;
      FakeEventSource.instance = this;
    }

    close() {
      this.closed = true;
    }
  }
  FakeEventSource.CLOSED = 2;

  const errors = [];
  const stream = paperclip.createLiveEventStream({
    EventSource: FakeEventSource,
    onError: (event) => errors.push(event),
  });

  FakeEventSource.instance.onopen();
  assert.equal(stream.state, 'live');

  // Browser EventSource auto-reconnects: a transient error is not fatal.
  FakeEventSource.instance.readyState = 0;
  FakeEventSource.instance.onerror(new Error('blip'));
  assert.equal(stream.state, 'connecting');
  assert.equal(errors.length, 0);

  FakeEventSource.instance.readyState = 2;
  FakeEventSource.instance.onerror(new Error('gone'));
  assert.equal(stream.state, 'preview');
  assert.equal(errors.length, 1);
});

test('a waiting stream stays live without forwarding placeholder events', () => {
  class FakeEventSource {
    constructor() {
      FakeEventSource.instance = this;
    }

    close() {}
  }

  const received = [];
  const stream = paperclip.createLiveEventStream({
    EventSource: FakeEventSource,
    onEvent: (event) => received.push(event),
  });

  FakeEventSource.instance.onmessage({
    data: '{"type":"paperclip.stream.waiting","payload":{"reason":"no_events_yet"}}',
  });

  assert.equal(stream.state, 'live');
  assert.deepEqual(received, []);
});

test('allowlists role CSS classes while preserving role labels as text', () => {
  const html = paperclip.renderFocusFigureHTML({
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

test('agents can be selected with Enter or Space', () => {
  let keyHandler = null;
  const fakeDocument = {
    addEventListener(type, handler) {
      if (type === 'keydown') keyHandler = handler;
    },
  };
  const calls = [];
  paperclip.bindAgentSelection(fakeDocument, (agentId) => calls.push(agentId));
  assert.equal(typeof keyHandler, 'function');

  const fakeTarget = {
    closest(selector) {
      if (selector === '[data-agent-id]') {
        return {
          dataset: { agentId: 'coder' },
          closest() { return { classList: { contains: () => false } }; },
        };
      }
      return null;
    },
  };
  keyHandler({ key: 'Enter', target: fakeTarget, preventDefault() {} });
  keyHandler({ key: ' ', target: fakeTarget, preventDefault() {} });
  keyHandler({ key: 'x', target: fakeTarget, preventDefault() {} });
  assert.deepEqual(calls, ['coder', 'coder']);
});

test('interaction arcs age out with the conversation window', () => {
  const state = paperclip.createFloorState();
  paperclip.applyFloorEvent(state, {
    type: 'agent.status',
    payload: { agentId: 'lead', name: 'Lead', status: 'review' },
  });
  paperclip.applyFloorEvent(state, {
    type: 'agent.status',
    payload: { agentId: 'dev', name: 'Dev', status: 'running' },
  });
  paperclip.applyFloorEvent(state, {
    type: 'activity.logged',
    payload: { fromAgentId: 'lead', toAgentId: 'dev', message: 'Old news' },
  });

  // Fresh message: arc + conversation render.
  let layout = paperclip.computeWorkspaceLayout(state);
  assert.equal(layout.interactions.length, 1);

  // Same message a minute later: both expire.
  state.messages[0].at = Date.now() - 60000;
  layout = paperclip.computeWorkspaceLayout(state);
  assert.equal(layout.interactions.length, 0);
  assert.equal(layout.conversations.length, 0);
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
