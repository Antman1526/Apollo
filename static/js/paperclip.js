// Paperclip integration UI. The Apollo-native "Floor" renders agents as an
// animated visual workspace; the classic iframe still loads Paperclip's OWN
// origin because its Vite build and API calls are rooted at /.

let _frameSrc = '';
let _status = null;
let _view = 'floor';
let _demoTimer = null;
let _demoIndex = 0;
let _liveStream = null;
let _liveRetryTimer = null;
let _floorState = createFloorState();

const ZONES = [
  { id: 'backlog', label: 'Backlog' },
  { id: 'working', label: 'Working' },
  { id: 'review', label: 'Review' },
  { id: 'blocked', label: 'Blocked' },
  { id: 'done', label: 'Done' },
];

// Shared corners of the office. Agents go here for cross-cutting states;
// backlog/working states keep them at their own assigned desk instead.
const SHARED_STATIONS = {
  review: { id: 'review', label: 'Review Table', x: 76, y: 18 },
  blocked: { id: 'blocked', label: 'Help Bar', x: 14, y: 74 },
  done: { id: 'done', label: 'Done Dock', x: 76, y: 70 },
};

// Personal desk slots laid out as two office rows. Each agent is assigned the
// next free slot on first sight and keeps it for the session.
const OFFICE_DESKS = [
  { x: 16, y: 32 }, { x: 36, y: 32 }, { x: 56, y: 32 },
  { x: 16, y: 56 }, { x: 36, y: 56 }, { x: 56, y: 56 },
];

// How long an activity message keeps two agents in a face-to-face chat.
const CONVERSATION_WINDOW_MS = 45000;

const ROLE_LABELS = {
  research: 'Research',
  coding: 'Code',
  ops: 'Ops',
  review: 'Review',
};

const ALLOWED_ROLES = new Set(Object.keys(ROLE_LABELS));
const ALLOWED_ZONES = new Set(ZONES.map((zone) => zone.id));
const boundAgentSelectionDocs = new WeakSet();

const DEMO_EVENTS = [
  { type: 'agent.status', payload: { agentId: 'researcher', name: 'Researcher', role: 'research', status: 'queued', task: 'Collect source context' } },
  { type: 'agent.status', payload: { agentId: 'coder', name: 'Coder', role: 'coding', status: 'running', task: 'Build The Floor UI' } },
  { type: 'agent.status', payload: { agentId: 'reviewer', name: 'Reviewer', role: 'review', status: 'review', task: 'Check route safety' } },
  { type: 'agent.status', payload: { agentId: 'operator', name: 'Operator', role: 'ops', status: 'blocked', task: 'Waiting for collector auth' } },
  { type: 'heartbeat.run.log', payload: { agentId: 'coder', chunk: 'Rendering Lego-like agent figures' } },
  { type: 'activity.logged', payload: { fromAgentId: 'researcher', toAgentId: 'coder', message: 'Paperclip events map cleanly to zones.' } },
  { type: 'heartbeat.run.status', payload: { agentId: 'researcher', status: 'running', task: 'Read live-events-ws.ts' } },
  { type: 'heartbeat.run.log', payload: { agentId: 'researcher', chunk: 'Found heartbeat.run.log transcript chunks.' } },
  { type: 'activity.logged', payload: { fromAgentId: 'coder', toAgentId: 'reviewer', message: 'Floor and Board views ready for review.' } },
  { type: 'heartbeat.run.status', payload: { agentId: 'reviewer', status: 'running', task: 'Validate visual states' } },
  { type: 'heartbeat.run.status', payload: { agentId: 'coder', status: 'done', task: 'The Floor shell' } },
  { type: 'heartbeat.run.status', payload: { agentId: 'operator', status: 'queued', task: 'Prepare collector spike' } },
  { type: 'heartbeat.run.status', payload: { agentId: 'operator', status: 'running', task: 'Provision collector auth spike' } },
  { type: 'activity.logged', payload: { fromAgentId: 'reviewer', toAgentId: 'operator', message: 'Need auth notes before collector work.' } },
  { type: 'heartbeat.run.status', payload: { agentId: 'researcher', status: 'review', task: 'Package Paperclip findings' } },
  { type: 'heartbeat.run.status', payload: { agentId: 'reviewer', status: 'done', task: 'Visual QA complete' } },
];

const PREVIEW_SEED_COUNT = 6;

function $(id) { return document.getElementById(id); }

function escapeHTML(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function zoneForStatus(status) {
  const s = String(status || '').toLowerCase();
  if (['running', 'working', 'in_progress', 'active', 'thinking'].includes(s)) return 'working';
  if (['review', 'reviewing', 'needs_review'].includes(s)) return 'review';
  if (['blocked', 'error', 'failed', 'crashed'].includes(s)) return 'blocked';
  if (['done', 'complete', 'completed', 'success'].includes(s)) return 'done';
  return 'backlog';
}

function normalizeRole(role) {
  const token = String(role || 'coding').trim().toLowerCase().match(/[a-z0-9_-]+/)?.[0] || '';
  return ALLOWED_ROLES.has(token) ? token : 'coding';
}

function normalizeZone(zone) {
  const token = String(zone || 'backlog').trim().toLowerCase().match(/[a-z0-9_-]+/)?.[0] || '';
  return ALLOWED_ZONES.has(token) ? token : 'backlog';
}

function createFloorState() {
  return {
    agents: new Map(),
    deskAssignments: new Map(),
    activity: [],
    messages: [],
    selectedAgentId: '',
    source: 'preview',
    lastUpdated: null,
  };
}

function payloadAgentId(payload = {}) {
  return payload.agentId || payload.agent_id || payload.agent?.id || payload.run?.agentId || payload.run?.agent_id || payload.id || '';
}

function ensureAgent(state, id, payload = {}) {
  const agentId = id || payloadAgentId(payload) || `agent-${state.agents.size + 1}`;
  const existing = state.agents.get(agentId) || {
    id: agentId,
    name: payload.name || payload.agent?.name || agentId,
    role: normalizeRole(payload.role || payload.agent?.role),
    status: 'queued',
    zone: 'backlog',
    task: '',
    thinking: false,
    transcript: [],
    tools: [],
    messages: [],
    updatedAt: Date.now(),
  };
  existing.name = payload.name || payload.agent?.name || existing.name;
  existing.role = normalizeRole(payload.role || payload.agent?.role || existing.role);
  state.agents.set(agentId, existing);
  if (state.deskAssignments && !state.deskAssignments.has(agentId)) {
    state.deskAssignments.set(agentId, state.deskAssignments.size);
  }
  if (!state.selectedAgentId) state.selectedAgentId = agentId;
  return existing;
}

function pushLimited(items, item, limit = 24) {
  items.unshift(item);
  if (items.length > limit) items.length = limit;
}

function applyFloorEvent(state, event) {
  if (!state || !event) return state;
  const payload = event.payload || {};
  const type = event.type || '';
  state.lastUpdated = Date.now();

  if (type === 'agent.status' || type === 'heartbeat.run.queued' || type === 'heartbeat.run.status') {
    const agentId = payloadAgentId(payload);
    const known = state.agents.has(agentId);
    const agent = ensureAgent(state, agentId, payload);
    const status = payload.status || payload.state || (type === 'heartbeat.run.queued' ? 'queued' : agent.status);
    // A brand-new agent has no previous spot to walk from.
    agent.previousZone = known ? agent.zone : undefined;
    agent.status = status;
    agent.zone = zoneForStatus(status);
    agent.task = payload.task || payload.title || payload.run?.title || agent.task;
    agent.thinking = agent.zone === 'working';
    agent.updatedAt = Date.now();
    pushLimited(state.activity, {
      kind: 'status',
      text: `${agent.name} -> ${agent.zone}`,
      at: agent.updatedAt,
    }, 18);
    return state;
  }

  if (type === 'heartbeat.run.log') {
    const agentId = payloadAgentId(payload);
    const known = state.agents.has(agentId);
    const agent = ensureAgent(state, agentId, payload);
    const chunk = payload.chunk || payload.text || payload.message || '';
    if (chunk) pushLimited(agent.transcript, chunk, 32);
    agent.previousZone = known ? agent.zone : undefined;
    agent.status = 'running';
    agent.zone = 'working';
    agent.thinking = true;
    agent.updatedAt = Date.now();
    return state;
  }

  if (type === 'heartbeat.run.event') {
    const agentId = payloadAgentId(payload);
    const known = state.agents.has(agentId);
    const agent = ensureAgent(state, agentId, payload);
    const tool = payload.tool || payload.name || payload.event || 'tool';
    pushLimited(agent.tools, tool, 8);
    agent.previousZone = known ? agent.zone : undefined;
    agent.zone = 'working';
    agent.thinking = true;
    agent.updatedAt = Date.now();
    return state;
  }

  if (type === 'activity.logged') {
    const fromId = payload.fromAgentId || payload.from_agent_id || payload.from;
    const toId = payload.toAgentId || payload.to_agent_id || payload.to;
    const text = payload.message || payload.text || payload.summary || 'Activity logged';
    const activity = { kind: 'message', fromId, toId, text, at: Date.now() };
    pushLimited(state.activity, activity, 18);
    if (fromId && toId) {
      pushLimited(state.messages, activity, 12);
      const from = ensureAgent(state, fromId, { name: fromId });
      const to = ensureAgent(state, toId, { name: toId });
      pushLimited(from.messages, `To ${to.name}: ${text}`, 8);
      pushLimited(to.messages, `From ${from.name}: ${text}`, 8);
    }
    return state;
  }

  pushLimited(state.activity, {
    kind: 'event',
    text: type || 'Paperclip event',
    at: Date.now(),
  }, 18);
  return state;
}

function renderLegoAgentHTML(agent, selected = false) {
  const roleKey = normalizeRole(agent.role);
  const zoneKey = normalizeZone(agent.zone);
  const role = ROLE_LABELS[roleKey] || ROLE_LABELS.coding;
  const classes = [
    'paperclip-agent-tile',
    selected ? 'selected' : '',
    agent.thinking ? 'thinking' : '',
    `zone-${zoneKey}`,
    `role-${roleKey}`,
  ].filter(Boolean).join(' ');
  return `
    <button type="button" class="${classes}" data-agent-id="${escapeHTML(agent.id)}" title="${escapeHTML(agent.name)}">
      <span class="paperclip-lego-agent" aria-hidden="true">
        <span class="paperclip-lego-head"><span class="paperclip-lego-face"></span></span>
        <span class="paperclip-lego-body">
          <span class="paperclip-lego-arm left"></span>
          <span class="paperclip-lego-torso"></span>
          <span class="paperclip-lego-arm right"></span>
        </span>
        <span class="paperclip-lego-legs"><span></span><span></span></span>
      </span>
      <span class="paperclip-agent-copy">
        <span class="paperclip-agent-name">${escapeHTML(agent.name)}</span>
        <span class="paperclip-agent-role">${escapeHTML(role)} / ${escapeHTML(zoneKey)}</span>
        <span class="paperclip-agent-task">${escapeHTML(agent.task || 'Ready')}</span>
      </span>
      <span class="paperclip-thinking-dots" aria-hidden="true"><span></span><span></span><span></span></span>
    </button>
  `;
}

function clampX(x) { return Math.max(4, Math.min(86, x)); }
function clampY(y) { return Math.max(10, Math.min(78, y)); }

function deskPointFor(state, agentId) {
  const index = state.deskAssignments?.get(agentId) ?? 0;
  const slot = OFFICE_DESKS[index % OFFICE_DESKS.length];
  const lap = Math.floor(index / OFFICE_DESKS.length);
  return { x: clampX(slot.x + lap * 5), y: clampY(slot.y + lap * 4) };
}

function stationPoint(zoneId, index = 0) {
  const station = SHARED_STATIONS[zoneId] || SHARED_STATIONS.review;
  const spread = [
    { x: 0, y: 0 },
    { x: 8, y: 6 },
    { x: -7, y: 8 },
    { x: 5, y: -7 },
    { x: -8, y: -5 },
  ][index % 5];
  return {
    x: clampX(station.x + spread.x),
    y: clampY(station.y + spread.y),
  };
}

function workspacePoint(state, agentId, zoneId, index = 0) {
  // Backlog and working agents are at their own desk; everyone else heads to
  // the matching shared corner of the office.
  if (zoneId === 'working' || zoneId === 'backlog') return deskPointFor(state, agentId);
  return stationPoint(zoneId, index);
}

function conversationLineFor(agent) {
  const task = String(agent.task || '').trim();
  switch (agent.zone) {
    case 'working': return task ? `On it — ${task}.` : 'On it now.';
    case 'review': return task ? `Reviewing ${task} — almost there.` : 'Deep in review.';
    case 'blocked': return task ? `Stuck on ${task}, could use a hand.` : 'Blocked — could use a hand.';
    case 'done': return task ? `Just wrapped ${task}.` : 'All wrapped up here.';
    default: return task ? `Next on my list: ${task}.` : 'Picking up the next task.';
  }
}

function computeWorkspaceLayout(state = _floorState) {
  const zoneCounts = {};
  const agents = sortedAgents(state).map((agent) => {
    const zoneIndex = zoneCounts[agent.zone] || 0;
    zoneCounts[agent.zone] = zoneIndex + 1;
    const point = workspacePoint(state, agent.id, agent.zone, zoneIndex);
    const fromPoint = workspacePoint(state, agent.id, agent.previousZone || agent.zone, zoneIndex);
    return {
      ...agent,
      x: point.x,
      y: point.y,
      fromX: fromPoint.x,
      fromY: fromPoint.y,
      moving: agent.previousZone && agent.previousZone !== agent.zone,
    };
  });
  const byId = new Map(agents.map((agent) => [agent.id, agent]));
  const interactions = state.messages
    .map((message) => ({
      ...message,
      from: byId.get(message.fromId),
      to: byId.get(message.toId),
    }))
    .filter((message) => message.from && message.to)
    .slice(0, 6);

  // Recent messages become live conversations: the sender's words plus a
  // reply the receiver derives from their own task.
  const now = Date.now();
  const conversations = interactions
    .filter((interaction) => !interaction.at || now - interaction.at <= CONVERSATION_WINDOW_MS)
    .slice(0, 2)
    .map((interaction) => ({
      ...interaction,
      fromText: interaction.text,
      toText: conversationLineFor(interaction.to),
    }));

  // For the newest conversation the sender physically walks over.
  const meeting = conversations[0];
  if (meeting && meeting.from.id !== meeting.to.id) {
    const side = meeting.to.x >= meeting.from.x ? -1 : 1;
    meeting.from.x = clampX(meeting.to.x + side * 13);
    meeting.from.y = clampY(meeting.to.y + 4);
    meeting.from.moving = meeting.from.x !== meeting.from.fromX || meeting.from.y !== meeting.from.fromY;
  }

  const talkingIds = new Set();
  for (const conversation of conversations) {
    talkingIds.add(conversation.from.id);
    talkingIds.add(conversation.to.id);
  }
  for (const agent of agents) {
    agent.talking = talkingIds.has(agent.id);
    agent.workingAtDesk = agent.zone === 'working' || agent.thinking;
    agent.pose = agent.talking ? 'talking' : agent.moving ? 'walking' : agent.workingAtDesk ? 'sitting' : 'standing';
  }

  // Heads-down agents murmur what they are working on.
  const murmurs = agents
    .filter((agent) => agent.zone === 'working' && !agent.talking && (agent.transcript.length || agent.task))
    .sort((a, b) => (b.updatedAt || 0) - (a.updatedAt || 0))
    .slice(0, 2)
    .map((agent) => ({
      id: agent.id,
      x: agent.x,
      y: agent.y,
      text: agent.transcript[0] || `Working on ${agent.task}`,
    }));

  const desks = agents.map((agent) => {
    const point = deskPointFor(state, agent.id);
    return {
      ownerId: agent.id,
      ownerName: agent.name,
      x: point.x,
      y: point.y,
      active: agent.zone === 'working',
      occupied: agent.zone === 'working' || agent.zone === 'backlog',
    };
  });

  return {
    stations: Object.values(SHARED_STATIONS),
    desks,
    agents,
    interactions,
    conversations,
    murmurs,
  };
}

function renderWorkspaceAgentHTML(agent, selected = false) {
  const roleKey = normalizeRole(agent.role);
  const zoneKey = normalizeZone(agent.zone);
  const role = ROLE_LABELS[roleKey] || ROLE_LABELS.coding;
  const classes = [
    'paperclip-roaming-agent',
    selected ? 'selected' : '',
    agent.thinking ? 'thinking' : '',
    agent.moving ? 'walking' : '',
    agent.talking ? 'talking' : '',
    agent.workingAtDesk ? 'working-at-desk' : '',
    `pose-${agent.pose || 'standing'}`,
    `role-${roleKey}`,
  ].filter(Boolean).join(' ');
  return `
    <button type="button" class="${classes}" data-agent-id="${escapeHTML(agent.id)}"
      title="${escapeHTML(`${agent.name}: ${agent.task || agent.zone}`)}"
      style="--agent-x:${agent.x}%;--agent-y:${agent.y}%;--from-x:${agent.fromX}%;--from-y:${agent.fromY}%;">
      <span class="paperclip-walk-path" aria-hidden="true"></span>
      <span class="paperclip-lego-agent" aria-hidden="true">
        <span class="paperclip-lego-head"><span class="paperclip-lego-face"></span></span>
        <span class="paperclip-lego-body">
          <span class="paperclip-lego-arm left"></span>
          <span class="paperclip-lego-torso"></span>
          <span class="paperclip-lego-arm right"></span>
        </span>
        <span class="paperclip-lego-legs"><span></span><span></span></span>
      </span>
      ${agent.talking ? '<span class="paperclip-speech-burst" aria-hidden="true"><span></span><span></span><span></span></span>' : ''}
      <span class="paperclip-roaming-label">
        <strong>${escapeHTML(agent.name)}</strong>
        <small>${escapeHTML(role)} / ${escapeHTML(zoneKey)}</small>
        ${agent.workingAtDesk ? `<em>${escapeHTML(agent.task || 'Working')}</em>` : ''}
      </span>
      <span class="paperclip-thinking-dots" aria-hidden="true"><span></span><span></span><span></span></span>
    </button>
  `;
}

function renderInteractionArcs(layout) {
  if (!layout.interactions.length) return '';
  return `
    <svg class="paperclip-interaction-layer" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
      ${layout.interactions.map((interaction) => {
        const midX = (interaction.from.x + interaction.to.x) / 2;
        const midY = Math.min(interaction.from.y, interaction.to.y) - 10;
        return `<path class="paperclip-interaction-arc" d="M ${interaction.from.x} ${interaction.from.y} Q ${midX} ${midY} ${interaction.to.x} ${interaction.to.y}" />`;
      }).join('')}
    </svg>
  `;
}

function renderDeskHTML(desk) {
  const classes = [
    'paperclip-agent-desk',
    desk.active ? 'active' : '',
    desk.occupied ? 'occupied' : 'empty',
  ].filter(Boolean).join(' ');
  return `
    <div class="${classes}" style="--desk-x:${desk.x}%;--desk-y:${desk.y}%;" aria-hidden="true">
      <span class="paperclip-desk-chair"></span>
      <span class="paperclip-desk-screen"></span>
      <span class="paperclip-desk-keyboard"></span>
      <span class="paperclip-desk-nameplate">${escapeHTML(desk.ownerName)}</span>
    </div>
  `;
}

function renderConversationHTML(layout) {
  const bubbles = [];
  for (const conversation of layout.conversations) {
    bubbles.push(`
      <div class="paperclip-chat-bubble from-bubble" style="--bubble-x:${conversation.from.x}%;--bubble-y:${conversation.from.y}%;">
        <strong>${escapeHTML(conversation.from.name)}</strong>${escapeHTML(conversation.fromText)}
      </div>
    `);
    bubbles.push(`
      <div class="paperclip-chat-bubble to-bubble" style="--bubble-x:${conversation.to.x}%;--bubble-y:${conversation.to.y}%;">
        <strong>${escapeHTML(conversation.to.name)}</strong>${escapeHTML(conversation.toText)}
      </div>
    `);
  }
  for (const murmur of layout.murmurs) {
    bubbles.push(`
      <div class="paperclip-murmur-bubble" style="--bubble-x:${murmur.x}%;--bubble-y:${murmur.y}%;">
        ${escapeHTML(murmur.text)}
      </div>
    `);
  }
  return bubbles.join('');
}

function renderWorkspaceHTML(state = _floorState) {
  const layout = computeWorkspaceLayout(state);
  return `
    <div class="paperclip-workspace-map">
      <div class="paperclip-workspace-grid" aria-hidden="true"></div>
      <div class="paperclip-office-plant" aria-hidden="true"></div>
      ${layout.stations.map((station) => `
        <div class="paperclip-workstation station-${station.id}" style="--station-x:${station.x}%;--station-y:${station.y}%;">
          <span>${escapeHTML(station.label)}</span>
        </div>
      `).join('')}
      ${layout.desks.map((desk) => renderDeskHTML(desk)).join('')}
      ${renderInteractionArcs(layout)}
      ${layout.agents.map((agent) => renderWorkspaceAgentHTML(agent, agent.id === state.selectedAgentId)).join('')}
      ${renderConversationHTML(layout)}
    </div>
  `;
}

function sortedAgents(state = _floorState) {
  const zoneRank = new Map(ZONES.map((zone, index) => [zone.id, index]));
  return Array.from(state.agents.values()).sort((a, b) => {
    const zoneDelta = (zoneRank.get(a.zone) ?? 99) - (zoneRank.get(b.zone) ?? 99);
    return zoneDelta || a.name.localeCompare(b.name);
  });
}

function renderActivityHTML(state) {
  if (!state.activity.length) return '<div class="paperclip-empty-line">No activity</div>';
  return state.activity.map((item) => `
    <div class="paperclip-activity-item ${item.kind === 'message' ? 'message' : ''}">
      <span>${escapeHTML(item.text)}</span>
    </div>
  `).join('');
}

function renderFocusHTML(state) {
  const agent = state.agents.get(state.selectedAgentId) || sortedAgents(state)[0];
  if (!agent) return '<div class="paperclip-empty-line">No agents</div>';
  const transcript = agent.transcript.length
    ? agent.transcript.map((line) => `<div class="paperclip-transcript-line">${escapeHTML(line)}</div>`).join('')
    : '<div class="paperclip-empty-line">No transcript</div>';
  const tools = agent.tools.length
    ? agent.tools.map((tool) => `<span class="paperclip-tool-chip">${escapeHTML(tool)}</span>`).join('')
    : '<span class="paperclip-tool-chip muted">idle</span>';
  const messages = agent.messages.length
    ? agent.messages.map((msg) => `<div class="paperclip-message-line">${escapeHTML(msg)}</div>`).join('')
    : '<div class="paperclip-empty-line">No messages</div>';
  return `
    <div class="paperclip-focus-head">
      ${renderLegoAgentHTML(agent, true)}
      <div class="paperclip-focus-status">${escapeHTML(agent.status)} / ${escapeHTML(agent.task || 'Ready')}</div>
    </div>
    <div class="paperclip-pane-title">Tools</div>
    <div class="paperclip-tool-row">${tools}</div>
    <div class="paperclip-pane-title">Transcript</div>
    <div class="paperclip-transcript">${transcript}</div>
    <div class="paperclip-pane-title">Messages</div>
    <div class="paperclip-messages">${messages}</div>
  `;
}

function renderZones(state = _floorState) {
  const zoneGrid = $('paperclip-zone-grid');
  if (!zoneGrid) return;
  zoneGrid.innerHTML = renderWorkspaceHTML(state);
}

function renderBoard(state = _floorState) {
  const board = $('paperclip-board-view');
  if (!board) return;
  const agents = sortedAgents(state);
  board.innerHTML = ZONES.map((zone) => {
    const zoneAgents = agents.filter((agent) => agent.zone === zone.id);
    return `
      <section class="paperclip-board-lane">
        <div class="paperclip-board-lane-head">
          <span>${zone.label}</span>
          <span>${zoneAgents.length}</span>
        </div>
        <div class="paperclip-board-cards">
          ${zoneAgents.map((agent) => `
            <button type="button" class="paperclip-board-card ${agent.id === state.selectedAgentId ? 'selected' : ''}" data-agent-id="${escapeHTML(agent.id)}">
              <span>${escapeHTML(agent.name)}</span>
              <small>${escapeHTML(agent.task || 'Ready')}</small>
            </button>
          `).join('') || '<div class="paperclip-empty-line">Empty</div>'}
        </div>
      </section>
    `;
  }).join('');
}

function renderFloor() {
  renderZones(_floorState);
  renderBoard(_floorState);
  const focus = $('paperclip-focus-pane');
  if (focus) focus.innerHTML = renderFocusHTML(_floorState);
  const rail = $('paperclip-activity-rail');
  if (rail) rail.innerHTML = renderActivityHTML(_floorState);
  const liveState = $('paperclip-live-state');
  if (liveState) liveState.textContent = liveStateLabel();
  const liveCount = $('paperclip-live-count');
  if (liveCount) liveCount.textContent = `${_floorState.agents.size} agents`;
  bindAgentSelection();
}

function liveStateLabel() {
  if (_liveStream?.state === 'live') {
    return _floorState.source === 'live' ? 'Live' : 'Live · waiting for agents';
  }
  if (_liveStream?.state === 'connecting') return 'Connecting live';
  if (_status?.reachable === true) return 'Preview (stream unavailable)';
  return 'Preview';
}

function bindAgentSelection(doc = document, onSelect = selectAgent) {
  if (!doc || boundAgentSelectionDocs.has(doc)) return;
  boundAgentSelectionDocs.add(doc);
  doc.addEventListener('click', (event) => {
    const target = event.target?.closest?.('[data-agent-id]');
    if (!target) return;
    const modal = target.closest?.('#paperclip-modal');
    if (modal?.classList?.contains('hidden')) return;
    if (modal || !doc.getElementById || doc.getElementById('paperclip-modal')?.contains(target)) {
      event.preventDefault?.();
      onSelect(target.dataset.agentId);
    }
  }, true);
}

function selectAgent(agentId) {
  if (!agentId) return;
  if (!_floorState.agents.has(agentId)) ensureAgent(_floorState, agentId, { name: agentId });
  _floorState.selectedAgentId = agentId;
  renderFloor();
}

function seedFloorPreview() {
  if (_floorState.agents.size) return;
  for (const event of DEMO_EVENTS.slice(0, PREVIEW_SEED_COUNT)) applyFloorEvent(_floorState, event);
  _demoIndex = PREVIEW_SEED_COUNT;
}

function advancePreview() {
  if (_view === 'classic') return;
  const event = DEMO_EVENTS[_demoIndex % DEMO_EVENTS.length];
  _demoIndex += 1;
  applyFloorEvent(_floorState, event);
  renderFloor();
}

function startPreviewLoop() {
  seedFloorPreview();
  renderFloor();
  if (_demoTimer) return;
  _demoTimer = window.setInterval(advancePreview, 2600);
}

function stopPreviewLoop() {
  if (_demoTimer) window.clearInterval(_demoTimer);
  _demoTimer = null;
}

function parseLiveEvent(raw) {
  if (!raw) return null;
  try {
    return JSON.parse(raw);
  } catch (_e) {
    return { type: 'paperclip.raw', payload: { text: String(raw) } };
  }
}

function createLiveEventStream({
  EventSource: EventSourceCtor = typeof window !== 'undefined' ? window.EventSource : undefined,
  url = '/api/paperclip/stream',
  onEvent = () => {},
  onOpen = () => {},
  onError = () => {},
} = {}) {
  if (!EventSourceCtor) {
    return { state: 'preview', close() {} };
  }
  const source = new EventSourceCtor(url, { withCredentials: true });
  const stream = {
    state: 'connecting',
    close() {
      source.close?.();
    },
  };
  source.onopen = () => {
    stream.state = 'live';
    onOpen();
  };
  source.onerror = (error) => {
    // Browser EventSource auto-reconnects after transient errors; only a
    // permanently closed source means the stream is gone for good.
    const closed = EventSourceCtor.CLOSED ?? 2;
    if (source.readyState !== undefined && source.readyState !== closed) {
      if (stream.state === 'live') stream.state = 'connecting';
      return;
    }
    stream.state = 'preview';
    onError(error);
  };
  source.onmessage = (message) => {
    const event = parseLiveEvent(message.data);
    if (!event) return;
    if (event.type === 'paperclip.stream.unavailable') {
      stream.state = 'preview';
      onError(event);
      return;
    }
    if (event.type === 'paperclip.stream.waiting') {
      // Connected, but no agent activity has been ingested yet. Stay live
      // without forwarding the placeholder downstream.
      stream.state = 'live';
      return;
    }
    onEvent(event);
  };
  return stream;
}

function handleLiveEvent(event) {
  if (_floorState.source !== 'live') {
    // First real event: swap the demo preview for the live floor.
    stopPreviewLoop();
    _floorState = createFloorState();
    _floorState.source = 'live';
  }
  applyFloorEvent(_floorState, event);
  renderFloor();
}

function startLiveStream() {
  if (_liveStream || _status?.reachable !== true) return false;
  _liveStream = createLiveEventStream({
    onOpen() {
      // Keep the preview running until real events arrive (the stream may
      // be connected but idle); just refresh the "Live" label.
      renderFloor();
    },
    onEvent: handleLiveEvent,
    onError() {
      stopLiveStream();
      if (_floorState.source === 'live') _floorState = createFloorState();
      startPreviewLoop();
      scheduleLiveRetry();
    },
  });
  if (_liveStream.state === 'preview') {
    _liveStream = null;
    return false;
  }
  return true;
}

function stopLiveStream() {
  if (_liveStream) _liveStream.close();
  _liveStream = null;
}

function scheduleLiveRetry() {
  if (_liveRetryTimer || typeof window === 'undefined' || typeof window.setTimeout !== 'function') return;
  _liveRetryTimer = window.setTimeout(() => {
    _liveRetryTimer = null;
    const modal = $('paperclip-modal');
    if (!modal || modal.classList.contains('hidden')) return;
    startLiveStream();
  }, 20000);
}

function cancelLiveRetry() {
  if (_liveRetryTimer && typeof window !== 'undefined') window.clearTimeout(_liveRetryTimer);
  _liveRetryTimer = null;
}

function startFloorUpdates() {
  seedFloorPreview();
  renderFloor();
  if (!startLiveStream()) startPreviewLoop();
}

function stopFloorUpdates() {
  cancelLiveRetry();
  stopLiveStream();
  stopPreviewLoop();
}

function openModal() {
  const modal = $('paperclip-modal');
  // The Floor view works without an iframe URL; only require one of
  // (enabled sidecar, browser URL) so the workspace is reachable.
  if (!modal || (!_frameSrc && !_status?.enabled)) return;
  modal.classList.remove('hidden');
  setView(_view || 'floor');
  startFloorUpdates();
}

function closeModal() {
  const modal = $('paperclip-modal');
  if (modal) modal.classList.add('hidden');
  stopFloorUpdates();
}

function loadClassicFrame() {
  const frame = $('paperclip-frame');
  if (frame && _frameSrc && !frame.getAttribute('src')) frame.setAttribute('src', _frameSrc);
}

function setView(view) {
  _view = view || 'floor';
  const shell = $('paperclip-live-shell');
  const floor = $('paperclip-floor-view');
  const board = $('paperclip-board-view');
  const classic = $('paperclip-classic-view');
  if (shell) shell.dataset.view = _view;
  if (floor) floor.classList.toggle('hidden', _view !== 'floor');
  if (board) board.classList.toggle('hidden', _view !== 'board');
  if (classic) classic.classList.toggle('hidden', _view !== 'classic');
  if (_view === 'classic') loadClassicFrame();
  document.querySelectorAll('[data-paperclip-view]').forEach((btn) => {
    const active = btn.dataset.paperclipView === _view;
    btn.classList.toggle('active', active);
    btn.setAttribute('aria-pressed', String(active));
  });
  renderFloor();
}

function applyStatus(status) {
  _status = status || null;
  const enabled = !!(status && status.enabled);
  _frameSrc = (status && status.browser_url) ? status.browser_url : '';

  // Sidebar tool button — hidden unless the sidecar is enabled.
  const btn = $('tool-paperclip-btn');
  if (btn) btn.style.display = enabled ? '' : 'none';
  const railBtn = $('rail-paperclip');
  if (railBtn) railBtn.style.display = enabled ? '' : 'none';

  // Settings subsection.
  const section = $('set-paperclip-section');
  const stateEl = $('set-paperclipState');
  const endpointEl = $('set-paperclipEndpoint');
  if (stateEl) {
    let label = enabled ? 'Enabled' : 'Disabled';
    if (enabled && status.reachable === false) label = 'Enabled (not reachable)';
    stateEl.textContent = label;
  }
  if (endpointEl && status) {
    const bits = [];
    if (status.model_endpoint) bits.push(`model: ${status.model_endpoint}`);
    if (status.browser_url) bits.push(status.browser_url);
    endpointEl.textContent = bits.join(' · ');
  }
  const openBtn = $('set-paperclipOpen');
  if (openBtn) openBtn.disabled = !enabled || !_frameSrc;
  if (section) section.dataset.enabled = String(enabled);
}

async function refreshStatus() {
  try {
    const res = await fetch('/api/paperclip/status', { credentials: 'same-origin' });
    if (!res.ok) { applyStatus({ enabled: false }); return; }
    applyStatus(await res.json());
  } catch (_e) {
    applyStatus({ enabled: false });
  }
}

function init() {
  const btn = $('tool-paperclip-btn');
  if (btn) btn.addEventListener('click', openModal);

  const railBtn = $('rail-paperclip');
  if (railBtn) railBtn.addEventListener('click', openModal);

  const closeBtn = $('close-paperclip-modal');
  if (closeBtn) closeBtn.addEventListener('click', closeModal);

  const openBtn = $('set-paperclipOpen');
  if (openBtn) openBtn.addEventListener('click', openModal);

  const popoutBtn = $('paperclip-popout-btn');
  if (popoutBtn) popoutBtn.addEventListener('click', () => {
    if (_frameSrc) window.open(_frameSrc, 'paperclip', 'noopener,width=1280,height=900');
  });

  document.querySelectorAll('[data-paperclip-view]').forEach((btn) => {
    btn.addEventListener('click', () => setView(btn.dataset.paperclipView));
  });

  bindAgentSelection();

  document.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape') return;
    const modal = $('paperclip-modal');
    if (!modal || modal.classList.contains('hidden')) return;
    closeModal();
  });

  refreshStatus();
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}

export {
  applyFloorEvent,
  applyStatus,
  bindAgentSelection,
  computeWorkspaceLayout,
  createFloorState,
  createLiveEventStream,
  refreshStatus,
  renderLegoAgentHTML,
  renderWorkspaceHTML,
  zoneForStatus,
};
