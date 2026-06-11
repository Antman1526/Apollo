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
    const agent = ensureAgent(state, payloadAgentId(payload), payload);
    const status = payload.status || payload.state || (type === 'heartbeat.run.queued' ? 'queued' : agent.status);
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
    const agent = ensureAgent(state, payloadAgentId(payload), payload);
    const chunk = payload.chunk || payload.text || payload.message || '';
    if (chunk) pushLimited(agent.transcript, chunk, 32);
    agent.status = 'running';
    agent.zone = 'working';
    agent.thinking = true;
    agent.updatedAt = Date.now();
    return state;
  }

  if (type === 'heartbeat.run.event') {
    const agent = ensureAgent(state, payloadAgentId(payload), payload);
    const tool = payload.tool || payload.name || payload.event || 'tool';
    pushLimited(agent.tools, tool, 8);
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
      // No name override: an unknown agent falls back to its id on creation,
      // and a known agent keeps its proper display name.
      const from = ensureAgent(state, fromId, {});
      const to = ensureAgent(state, toId, {});
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

// Focus-pane header card: the same SVG minifig as the floor, plus name,
// role/zone, and current task.
function renderFocusFigureHTML(agent) {
  const roleKey = normalizeRole(agent.role);
  const zoneKey = normalizeZone(agent.zone);
  const role = ROLE_LABELS[roleKey] || ROLE_LABELS.coding;
  const classes = [
    'paperclip-focus-card',
    agent.thinking ? 'thinking' : '',
    `zone-${zoneKey}`,
    `role-${roleKey}`,
  ].filter(Boolean).join(' ');
  return `
    <div class="${classes}">
      <svg class="paperclip-focus-fig" viewBox="-28 -78 56 84" aria-hidden="true">
        ${minifigSVG()}
      </svg>
      <span class="paperclip-agent-copy">
        <span class="paperclip-agent-name">${escapeHTML(agent.name)}</span>
        <span class="paperclip-agent-role">${escapeHTML(role)} / ${escapeHTML(zoneKey)}</span>
        <span class="paperclip-agent-task">${escapeHTML(agent.task || 'Ready')}</span>
      </span>
    </div>
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
  if (zoneId === 'working' || zoneId === 'backlog') {
    const desk = deskPointFor(state, agentId);
    // Stand/sit at the chair on the near side of the desk, not inside it.
    return { x: desk.x, y: clampY(desk.y + 3) };
  }
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
    // Walk from wherever the last rendered frame left the agent; a fresh
    // agent appears in place. commitWorkspaceLayout() advances lastX/lastY
    // after each render so a move animates exactly once.
    const hasLast = agent.lastX !== undefined && agent.lastY !== undefined;
    return {
      ...agent,
      x: point.x,
      y: point.y,
      fromX: hasLast ? agent.lastX : point.x,
      fromY: hasLast ? agent.lastY : point.y,
      moving: false,
    };
  });
  const byId = new Map(agents.map((agent) => [agent.id, agent]));
  // Old chatter ages out — both the arcs and the speech bubbles.
  const now = Date.now();
  const interactions = state.messages
    .map((message) => ({
      ...message,
      from: byId.get(message.fromId),
      to: byId.get(message.toId),
    }))
    .filter((message) => message.from && message.to)
    .filter((message) => !message.at || now - message.at <= CONVERSATION_WINDOW_MS)
    .slice(0, 6);

  // Recent messages become live conversations: the sender's words plus a
  // reply the receiver derives from their own task.
  const conversations = interactions
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
  }

  // Final positions are settled: anything displaced since the last rendered
  // frame walks there.
  for (const agent of agents) {
    agent.moving = agent.x !== agent.fromX || agent.y !== agent.fromY;
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
      slot: state.deskAssignments?.get(agent.id) ?? 0,
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

// Record where this frame left every agent so the next layout walks from
// there (and only walks when something actually moved).
function commitWorkspaceLayout(state, layout) {
  if (!state?.agents || !layout?.agents) return;
  for (const rendered of layout.agents) {
    const agent = state.agents.get(rendered.id);
    if (agent) {
      agent.lastX = rendered.x;
      agent.lastY = rendered.y;
    }
  }
}

// ── Isometric stage ──────────────────────────────────────────────────────
// Logical floor coords are 0-100 on both axes; the renderer projects them
// onto a fixed 1200x740 stage that renderZones() scales to fit its container.
const STAGE = { w: 1200, h: 740, originX: 600, originY: 96, sx: 5.5, sy: 3.0 };

function isoProject(x, y) {
  return {
    px: STAGE.originX + (x - y) * STAGE.sx,
    py: STAGE.originY + (x + y) * STAGE.sy,
  };
}

function isoPoints(points) {
  return points.map((p) => `${p.px.toFixed(1)},${p.py.toFixed(1)}`).join(' ');
}

// An axis-aligned box in iso projection: footprint (w x d) centred on
// (gx, gy), extruded from hBottom to hTop pixels above the floor.
function isoBoxSVG(gx, gy, w, d, hBottom, hTop, fill, cls = '') {
  const corners = [
    isoProject(gx - w / 2, gy - d / 2),
    isoProject(gx + w / 2, gy - d / 2),
    isoProject(gx + w / 2, gy + d / 2),
    isoProject(gx - w / 2, gy + d / 2),
  ];
  const top = corners.map((p) => ({ px: p.px, py: p.py - hTop }));
  const base = corners.map((p) => ({ px: p.px, py: p.py - hBottom }));
  return `<g${cls ? ` class="${cls}"` : ''}>` +
    `<polygon points="${isoPoints([base[3], top[3], top[2], base[2]])}" fill="${fill.left}"/>` +
    `<polygon points="${isoPoints([base[1], top[1], top[2], base[2]])}" fill="${fill.right}"/>` +
    `<polygon points="${isoPoints(top)}" fill="${fill.top}"/>` +
    '</g>';
}

const PALETTE = {
  floor: '#ece1cf',
  grid: 'rgba(70, 50, 30, 0.08)',
  wallBack: '#7d6a5d',
  wallSide: '#6e5c50',
  wood: { top: '#d9b98c', left: '#bd9a68', right: '#a9854f' },
  woodDark: { top: '#c69a5e', left: '#a87e45', right: '#946c38' },
  dark: { top: '#2c313a', left: '#232830', right: '#1b1f26' },
  metal: { top: '#c4c9d1', left: '#a9aeb8', right: '#92979f' },
  chair: { top: '#a87f9c', left: '#8d6783', right: '#79566f' },
  sofaPink: { top: '#e88aa0', left: '#cf6f87', right: '#b95a72' },
  sofaBlue: { top: '#7f9ee8', left: '#6684cb', right: '#5570b2' },
  plantPot: { top: '#a9683c', left: '#8f5530', right: '#7d4827' },
  pingpong: { top: '#69c7a2', left: '#52a886', right: '#449070' },
  vending: { top: '#8d3038', left: '#75272e', right: '#5f1f25' },
};

function isoLabelSVG(gx, gy, text, dy = 0) {
  const p = isoProject(gx, gy);
  return `<text class="paperclip-iso-label" x="${p.px.toFixed(1)}" y="${(p.py + dy).toFixed(1)}" text-anchor="middle">${escapeHTML(text)}</text>`;
}

function floorSVG() {
  const corners = [isoProject(0, 0), isoProject(100, 0), isoProject(100, 100), isoProject(0, 100)];
  const lines = [];
  for (let i = 10; i <= 90; i += 10) {
    const a = isoProject(i, 0);
    const b = isoProject(i, 100);
    const c = isoProject(0, i);
    const d = isoProject(100, i);
    lines.push(`<line x1="${a.px.toFixed(1)}" y1="${a.py.toFixed(1)}" x2="${b.px.toFixed(1)}" y2="${b.py.toFixed(1)}"/>`);
    lines.push(`<line x1="${c.px.toFixed(1)}" y1="${c.py.toFixed(1)}" x2="${d.px.toFixed(1)}" y2="${d.py.toFixed(1)}"/>`);
  }
  return `<polygon class="paperclip-iso-floor" points="${isoPoints(corners)}" fill="${PALETTE.floor}"/>` +
    `<g stroke="${PALETTE.grid}" stroke-width="1">${lines.join('')}</g>`;
}

function wallsSVG() {
  const H = 96;
  const o = isoProject(0, 0);
  const r = isoProject(100, 0);
  const l = isoProject(0, 100);
  const wall = (a, b, fill) => `<polygon class="paperclip-iso-wall" points="${isoPoints([
    { px: a.px, py: a.py - H }, { px: b.px, py: b.py - H }, b, a,
  ])}" fill="${fill}"/>`;
  return wall(o, r, PALETTE.wallBack) + wall(l, o, PALETTE.wallSide);
}

// A flat panel hung on the back wall between gx1..gx2 — its top edge hTop px
// above the floor line, hSize px tall. stickies: [color, gx, h] kanban notes.
function wallPanelSVG(gx1, gx2, hTop, hSize, fill, stickies = []) {
  const quadAt = (xa, xb, top, size) => {
    const a = isoProject(xa, 0);
    const b = isoProject(xb, 0);
    return isoPoints([
      { px: a.px, py: a.py - top }, { px: b.px, py: b.py - top },
      { px: b.px, py: b.py - top + size }, { px: a.px, py: a.py - top + size },
    ]);
  };
  const notes = stickies.map(([color, gx, h]) =>
    `<polygon points="${quadAt(gx, gx + 1.8, h, 9)}" fill="${color}"/>`).join('');
  return `<polygon points="${quadAt(gx1, gx2, hTop, hSize)}" fill="${fill}"/>${notes}`;
}

// A window on the back wall: frame, sky glass, mullion, and a soft pool of
// daylight spilling onto the floor.
function windowSVG(gx1, gx2, hTop, hSize) {
  const mid = (gx1 + gx2) / 2;
  const a = isoProject(gx1 + 0.4, 0);
  const b = isoProject(gx2 - 0.4, 0);
  const c = isoProject(gx2 + 7, 15);
  const d = isoProject(gx1 + 7, 15);
  return `<g class="paperclip-iso-window">` +
    wallPanelSVG(gx1, gx2, hTop, hSize, '#5b4f45') +
    wallPanelSVG(gx1 + 0.6, gx2 - 0.6, hTop - 2, hSize - 4, '#aac4d4') +
    wallPanelSVG(mid - 0.25, mid + 0.25, hTop - 2, hSize - 4, '#5b4f45') +
    `<polygon class="paperclip-window-light" points="${isoPoints([a, b, c, d])}" fill="rgba(255, 244, 212, 0.1)"/>` +
    '</g>';
}

// Analog clock on the left wall (an ellipse approximates the wall angle).
function wallClockSVG(gy, height) {
  const p = isoProject(0, gy);
  const cy = p.py - height;
  return `<g class="paperclip-wall-clock">` +
    `<ellipse cx="${p.px.toFixed(1)}" cy="${cy.toFixed(1)}" rx="9" ry="10" fill="#f2ede2" stroke="#3a3128" stroke-width="2"/>` +
    `<line x1="${p.px.toFixed(1)}" y1="${cy.toFixed(1)}" x2="${p.px.toFixed(1)}" y2="${(cy - 6).toFixed(1)}" stroke="#3a3128" stroke-width="1.6"/>` +
    `<line x1="${p.px.toFixed(1)}" y1="${cy.toFixed(1)}" x2="${(p.px + 4.5).toFixed(1)}" y2="${(cy + 2).toFixed(1)}" stroke="#3a3128" stroke-width="1.6"/>` +
    '</g>';
}

function wallDecorSVG() {
  return [
    // Kanban board with sticky notes.
    wallPanelSVG(26, 46, 80, 38, '#23272f', [
      ['#e06c75', 28, 76], ['#e5c07b', 31, 64], ['#98c379', 35, 74],
      ['#61afef', 38, 62], ['#c678dd', 42, 72],
    ]),
    // Picture frames.
    wallPanelSVG(8, 15, 72, 22, '#46506b'),
    wallPanelSVG(88, 95, 74, 20, '#3a3f4a'),
    // Daylight.
    windowSVG(52, 60, 78, 34),
    windowSVG(63, 71, 78, 34),
    wallClockSVG(50, 64),
  ].join('');
}

function screenSVG(gx, gy, w, hBottom, hTop, active) {
  const a = isoProject(gx - w / 2 + 0.4, gy + 0.32);
  const b = isoProject(gx + w / 2 - 0.4, gy + 0.32);
  return `<polygon class="paperclip-desk-screen${active ? '' : ' idle'}" points="${isoPoints([
    { px: a.px, py: a.py - hTop + 2 }, { px: b.px, py: b.py - hTop + 2 },
    { px: b.px, py: b.py - hBottom - 2 }, { px: a.px, py: a.py - hBottom - 2 },
  ])}" fill="#7fd4a3"/>`;
}

// A small personal item on the desktop, stable per desk slot: a coffee mug,
// a stack of papers, or a tiny succulent.
function deskPropSVG(gx, gy, slot, active) {
  const kind = slot % 3;
  if (kind === 0) {
    const p = isoProject(gx, gy);
    const steam = active
      ? `<circle cx="${p.px.toFixed(1)}" cy="${(p.py - 42).toFixed(1)}" r="1.4" fill="rgba(255,255,255,0.4)"/>`
      : '';
    return `<g class="paperclip-desk-prop prop-mug">${isoBoxSVG(gx, gy, 1.1, 1.1, 33, 37.5, PALETTE.sofaPink)}${steam}</g>`;
  }
  if (kind === 1) {
    return `<g class="paperclip-desk-prop prop-papers">${isoBoxSVG(gx, gy, 2.4, 1.7, 33, 33.9, { top: '#f4f1e8', left: '#ddd8cb', right: '#cbc6b8' })}</g>`;
  }
  const p = isoProject(gx, gy);
  return `<g class="paperclip-desk-prop prop-plant">` +
    isoBoxSVG(gx, gy, 1.1, 1.1, 33, 36, PALETTE.plantPot) +
    `<circle cx="${p.px.toFixed(1)}" cy="${(p.py - 39).toFixed(1)}" r="3.4" fill="#46b173"/>` +
    '</g>';
}

function deskSVG(desk) {
  const { x, y } = desk;
  const parts = [];
  for (const [lx, ly] of [[-3.6, -1.6], [3.6, -1.6], [-3.6, 1.6], [3.6, 1.6]]) {
    parts.push(isoBoxSVG(x + lx, y + ly, 0.7, 0.7, 0, 26, PALETTE.woodDark));
  }
  parts.push(isoBoxSVG(x, y, 9, 4.6, 26, 33, PALETTE.wood));
  // Chair tucked on the near side; the agent sits here when working.
  parts.push(isoBoxSVG(x, y + 3.8, 0.6, 0.6, 0, 12, PALETTE.dark, 'paperclip-desk-chair'));
  parts.push(isoBoxSVG(x, y + 3.8, 3.2, 3.2, 12, 17, PALETTE.chair));
  parts.push(isoBoxSVG(x, y + 5.2, 3.2, 0.7, 17, 34, PALETTE.chair));
  // Monitor on the far edge, facing the chair.
  parts.push(isoBoxSVG(x + 1, y - 1.2, 1, 0.6, 33, 37, PALETTE.dark));
  parts.push(isoBoxSVG(x + 1, y - 1.3, 4.2, 0.6, 37, 56, PALETTE.dark));
  parts.push(screenSVG(x + 1, y - 1.3, 4.2, 37, 56, desk.active));
  parts.push(isoBoxSVG(x - 0.8, y + 0.6, 3, 1.2, 33, 34, PALETTE.metal));
  parts.push(deskPropSVG(x - 3.4, y - 1.2, desk.slot, desk.active));
  const np = isoProject(x - 2.6, y + 3.4);
  const classes = [
    'paperclip-agent-desk',
    desk.active ? 'active' : '',
    desk.occupied ? 'occupied' : 'empty',
  ].filter(Boolean).join(' ');
  return `<g class="${classes}">${parts.join('')}` +
    `<text class="paperclip-desk-nameplate" x="${np.px.toFixed(1)}" y="${(np.py + 4).toFixed(1)}" text-anchor="middle">${escapeHTML(desk.ownerName)}</text>` +
    '</g>';
}

function meetingTableSVG(station) {
  const c = isoProject(station.x, station.y);
  const parts = [isoBoxSVG(station.x, station.y, 2.6, 2.6, 0, 16, PALETTE.woodDark)];
  parts.push(`<ellipse cx="${c.px.toFixed(1)}" cy="${(c.py - 16).toFixed(1)}" rx="64" ry="30" fill="${PALETTE.woodDark.left}"/>`);
  parts.push(`<ellipse cx="${c.px.toFixed(1)}" cy="${(c.py - 20).toFixed(1)}" rx="64" ry="30" fill="${PALETTE.wood.top}"/>`);
  for (const [dx, dy] of [[-10, 4], [-1, 10], [9, 3]]) {
    parts.push(isoBoxSVG(station.x + dx, station.y + dy, 3, 3, 8, 13, PALETTE.chair));
    parts.push(isoBoxSVG(station.x + dx, station.y + dy + 1.3, 3, 0.7, 13, 26, PALETTE.chair));
  }
  return `<g class="paperclip-iso-station station-review">${parts.join('')}${isoLabelSVG(station.x, station.y, station.label, -64)}</g>`;
}

function kitchenSVG(station) {
  const parts = [
    isoBoxSVG(station.x - 1, station.y, 6, 16, 0, 30, PALETTE.metal),
    isoBoxSVG(station.x - 1.4, station.y - 4.4, 3, 3, 30, 48, PALETTE.dark),
    isoBoxSVG(station.x - 1, station.y + 0.8, 2, 1.4, 30, 33, PALETTE.sofaPink),
    isoBoxSVG(station.x - 1, station.y + 4.8, 2.4, 2, 30, 36, PALETTE.plantPot),
  ];
  return `<g class="paperclip-iso-station station-blocked">${parts.join('')}${isoLabelSVG(station.x + 10, station.y + 2, station.label, -70)}</g>`;
}

function plantSVG(gx, gy) {
  const p = isoProject(gx, gy);
  return isoBoxSVG(gx, gy, 2.2, 2.2, 0, 9, PALETTE.plantPot) +
    `<circle cx="${(p.px - 4).toFixed(1)}" cy="${(p.py - 16).toFixed(1)}" r="7" fill="#3f9d63"/>` +
    `<circle cx="${(p.px + 5).toFixed(1)}" cy="${(p.py - 18).toFixed(1)}" r="6" fill="#2f8a52"/>` +
    `<circle cx="${p.px.toFixed(1)}" cy="${(p.py - 23).toFixed(1)}" r="6" fill="#46b173"/>`;
}

function loungeSVG(station) {
  const rug = [
    isoProject(station.x - 13, station.y), isoProject(station.x, station.y - 9),
    isoProject(station.x + 13, station.y), isoProject(station.x, station.y + 9),
  ];
  const parts = [
    `<polygon points="${isoPoints(rug)}" fill="rgba(172, 138, 106, 0.4)"/>`,
    isoBoxSVG(station.x - 6, station.y - 4, 9, 3.4, 4, 13, PALETTE.sofaPink),
    isoBoxSVG(station.x - 6, station.y - 5.6, 9, 1, 13, 24, PALETTE.sofaPink),
    isoBoxSVG(station.x + 6, station.y + 3, 3.4, 7, 4, 13, PALETTE.sofaBlue),
    isoBoxSVG(station.x + 7.6, station.y + 3, 1, 7, 13, 24, PALETTE.sofaBlue),
    isoBoxSVG(station.x - 1, station.y + 2.6, 5, 2.4, 0, 12, PALETTE.woodDark),
    plantSVG(station.x + 11, station.y - 7),
  ];
  return `<g class="paperclip-iso-station station-done">${parts.join('')}${isoLabelSVG(station.x, station.y, station.label, -58)}</g>`;
}

function stationSVG(station) {
  if (station.id === 'review') return meetingTableSVG(station);
  if (station.id === 'blocked') return kitchenSVG(station);
  if (station.id === 'done') return loungeSVG(station);
  return '';
}

function decorSVG() {
  return [
    { depth: 80, svg: isoBoxSVG(76, 4, 5, 3, 0, 56, PALETTE.vending) },
    { depth: 16, svg: isoBoxSVG(12, 4, 8, 3, 0, 52, PALETTE.wood) },
    { depth: 14, svg: plantSVG(4, 10) },
    { depth: 104, svg: plantSVG(96, 8) },
    {
      depth: 131,
      svg: isoBoxSVG(45, 86, 12, 6, 14, 17, PALETTE.pingpong) +
        isoBoxSVG(45, 86, 12, 0.4, 17, 22, { top: '#f4f7f4', left: '#dfe5df', right: '#cdd4cd' }),
    },
  ];
}

function renderInteractionArcs(layout) {
  if (!layout.interactions.length) return '';
  return `<g class="paperclip-interaction-layer">${layout.interactions.map((interaction) => {
    const a = isoProject(interaction.from.x, interaction.from.y);
    const b = isoProject(interaction.to.x, interaction.to.y);
    const midX = (a.px + b.px) / 2;
    const midY = Math.min(a.py, b.py) - 80;
    return `<path class="paperclip-interaction-arc" d="M ${a.px.toFixed(1)} ${(a.py - 40).toFixed(1)} Q ${midX.toFixed(1)} ${midY.toFixed(1)} ${b.px.toFixed(1)} ${(b.py - 40).toFixed(1)}" />`;
  }).join('')}</g>`;
}

// One minifig, shared by the floor scene and the focus pane. Anchored at its
// feet at the group origin.
function minifigSVG() {
  return `
      <g class="paperclip-fig">
        <g class="paperclip-fig-legs">
          <rect class="paperclip-fig-leg left" x="-9" y="-16" width="8" height="16" rx="2"/>
          <rect class="paperclip-fig-leg right" x="1" y="-16" width="8" height="16" rx="2"/>
          <rect class="paperclip-fig-hip" x="-10" y="-20" width="20" height="5" rx="2"/>
        </g>
        <g class="paperclip-fig-body">
          <rect class="paperclip-fig-torso" x="-13" y="-42" width="26" height="23" rx="4"/>
          <rect class="paperclip-fig-arm" x="-19" y="-40" width="7" height="17" rx="3.5"/>
          <rect class="paperclip-fig-arm" x="12" y="-40" width="7" height="17" rx="3.5"/>
          <circle class="paperclip-fig-hand" cx="-15.5" cy="-21" r="3"/>
          <circle class="paperclip-fig-hand" cx="15.5" cy="-21" r="3"/>
        </g>
        <g class="paperclip-fig-headgroup">
          <rect class="paperclip-fig-stud" x="-5" y="-68" width="10" height="6" rx="2"/>
          <rect class="paperclip-fig-head" x="-10" y="-63" width="20" height="20" rx="6"/>
          <circle class="paperclip-fig-eye" cx="-4.5" cy="-55" r="1.7"/>
          <circle class="paperclip-fig-eye" cx="4.5" cy="-55" r="1.7"/>
          <path class="paperclip-fig-smile" d="M -4.5 -50.5 Q 0 -46.5 4.5 -50.5"/>
        </g>
      </g>`;
}

// The minifig is drawn directly into the scene SVG (anchored at its feet at
// the group origin) so depth-sorted furniture can occlude it correctly.
function renderWorkspaceAgentSVG(agent, selected = false) {
  const roleKey = normalizeRole(agent.role);
  const zoneKey = normalizeZone(agent.zone);
  const role = ROLE_LABELS[roleKey] || ROLE_LABELS.coding;
  const p = isoProject(agent.x, agent.y);
  const f = isoProject(agent.fromX, agent.fromY);
  const classes = [
    'paperclip-roaming-agent',
    selected ? 'selected' : '',
    agent.thinking ? 'thinking' : '',
    agent.moving ? 'walking' : '',
    agent.talking ? 'talking' : '',
    agent.workingAtDesk ? 'working-at-desk' : '',
    `pose-${agent.pose || 'standing'}`,
    `role-${roleKey}`,
    `zone-${zoneKey}`,
  ].filter(Boolean).join(' ');
  const chipWidth = Math.min(140, agent.name.length * 5.8 + 28);
  const trail = agent.moving
    ? `<line class="paperclip-walk-path" x1="${f.px.toFixed(1)}" y1="${f.py.toFixed(1)}" x2="${p.px.toFixed(1)}" y2="${p.py.toFixed(1)}"/>`
    : '';
  return `${trail}
    <g class="${classes}" data-agent-id="${escapeHTML(agent.id)}" role="button" tabindex="0"
      style="--agent-x:${p.px.toFixed(1)}px;--agent-y:${p.py.toFixed(1)}px;--from-x:${f.px.toFixed(1)}px;--from-y:${f.py.toFixed(1)}px;">
      <title>${escapeHTML(`${agent.name} · ${role} · ${agent.task || zoneKey}`)}</title>
      <ellipse class="paperclip-agent-shadow" cx="0" cy="2" rx="19" ry="6"/>
      ${selected ? '<ellipse class="paperclip-select-ring" cx="0" cy="2" rx="24" ry="8"/>' : ''}
      ${minifigSVG()}
      ${agent.talking ? `
        <g class="paperclip-speech-burst">
          <rect x="13" y="-88" width="34" height="16" rx="8"/>
          <circle cx="22" cy="-80" r="2"/>
          <circle cx="30" cy="-80" r="2"/>
          <circle cx="38" cy="-80" r="2"/>
        </g>
      ` : ''}
      ${agent.thinking && !agent.talking ? `
        <g class="paperclip-iso-thinking">
          <circle cx="-8" cy="-76" r="2.4"/>
          <circle cx="0" cy="-79" r="2.4"/>
          <circle cx="8" cy="-76" r="2.4"/>
        </g>
      ` : ''}
      <g class="paperclip-iso-chip">
        <rect x="${(-chipWidth / 2).toFixed(1)}" y="7" width="${chipWidth.toFixed(1)}" height="14" rx="7"/>
        <circle class="paperclip-chip-dot" cx="${(-chipWidth / 2 + 8).toFixed(1)}" cy="14" r="2.6"/>
        <text class="paperclip-iso-name" x="4" y="17" text-anchor="middle">${escapeHTML(agent.name)}</text>
      </g>
    </g>
  `;
}

function renderConversationHTML(layout) {
  const bubbles = [];
  for (const conversation of layout.conversations) {
    const fp = isoProject(conversation.from.x, conversation.from.y);
    const tp = isoProject(conversation.to.x, conversation.to.y);
    bubbles.push(`
      <div class="paperclip-chat-bubble from-bubble" style="--bubble-x:${fp.px.toFixed(1)}px;--bubble-y:${fp.py.toFixed(1)}px;">
        <strong>${escapeHTML(conversation.from.name)}</strong>${escapeHTML(conversation.fromText)}
      </div>
    `);
    bubbles.push(`
      <div class="paperclip-chat-bubble to-bubble" style="--bubble-x:${tp.px.toFixed(1)}px;--bubble-y:${tp.py.toFixed(1)}px;">
        <strong>${escapeHTML(conversation.to.name)}</strong>${escapeHTML(conversation.toText)}
      </div>
    `);
  }
  for (const murmur of layout.murmurs) {
    const p = isoProject(murmur.x, murmur.y);
    bubbles.push(`
      <div class="paperclip-murmur-bubble" style="--bubble-x:${p.px.toFixed(1)}px;--bubble-y:${p.py.toFixed(1)}px;">
        ${escapeHTML(murmur.text)}
      </div>
    `);
  }
  return bubbles.join('');
}

function renderWorkspaceHTML(state = _floorState, layout = computeWorkspaceLayout(state)) {
  // Furniture and agents share one depth-sorted paint list so anything nearer
  // the viewer genuinely occludes what stands behind it. Ties paint agents
  // after furniture (kind 1 > 0) so a seated agent shows in front of their desk.
  const items = [
    ...layout.desks.map((desk) => ({ depth: desk.x + desk.y, kind: 0, svg: deskSVG(desk) })),
    ...layout.stations.map((station) => ({ depth: station.x + station.y, kind: 0, svg: stationSVG(station) })),
    ...decorSVG().map((piece) => ({ ...piece, kind: 0 })),
    ...layout.agents.map((agent) => ({
      depth: agent.x + agent.y,
      kind: 1,
      svg: renderWorkspaceAgentSVG(agent, agent.id === state.selectedAgentId),
    })),
  ].sort((a, b) => (a.depth - b.depth) || (a.kind - b.kind));
  return `
    <div class="paperclip-workspace-map">
      <svg class="paperclip-iso-scene" viewBox="0 0 ${STAGE.w} ${STAGE.h}" preserveAspectRatio="xMidYMid meet">
        ${floorSVG()}
        ${wallsSVG()}
        ${wallDecorSVG()}
        ${items.map((piece) => piece.svg).join('')}
        ${renderInteractionArcs(layout)}
      </svg>
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
      ${renderFocusFigureHTML(agent)}
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

let _lastFloorHTML = '';

function renderZones(state = _floorState, layout = undefined) {
  const zoneGrid = $('paperclip-zone-grid');
  if (!zoneGrid) return;
  const html = renderWorkspaceHTML(state, layout);
  // Rewriting identical markup restarts every CSS animation; skip no-ops.
  if (html !== _lastFloorHTML || !zoneGrid.firstChild) {
    zoneGrid.innerHTML = html;
    _lastFloorHTML = html;
  }
  scaleWorkspaceStage();
}

function scaleWorkspaceStage() {
  const zoneGrid = $('paperclip-zone-grid');
  const stage = zoneGrid?.querySelector?.('.paperclip-workspace-map');
  if (!stage || !zoneGrid.clientWidth) return;
  const scale = Math.min(
    zoneGrid.clientWidth / STAGE.w,
    Math.max(zoneGrid.clientHeight, 420) / STAGE.h,
  );
  const clamped = Math.max(0.35, Math.min(1.5, scale));
  stage.style.transform = `scale(${clamped})`;
  stage.style.marginLeft = `${Math.max(0, (zoneGrid.clientWidth - STAGE.w * clamped) / 2)}px`;
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
            <button type="button" class="paperclip-board-card role-${normalizeRole(agent.role)} ${agent.id === state.selectedAgentId ? 'selected' : ''}" data-agent-id="${escapeHTML(agent.id)}">
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
  const layout = computeWorkspaceLayout(_floorState);
  renderZones(_floorState, layout);
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
  commitWorkspaceLayout(_floorState, layout);
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
  const activate = (event) => {
    const target = event.target?.closest?.('[data-agent-id]');
    if (!target) return;
    const modal = target.closest?.('#paperclip-modal');
    if (modal?.classList?.contains('hidden')) return;
    if (modal || !doc.getElementById || doc.getElementById('paperclip-modal')?.contains(target)) {
      event.preventDefault?.();
      onSelect(target.dataset.agentId);
    }
  };
  doc.addEventListener('click', activate, true);
  // SVG agent figures carry role="button"/tabindex="0"; honor keyboard too.
  doc.addEventListener('keydown', (event) => {
    if (event.key !== 'Enter' && event.key !== ' ') return;
    activate(event);
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
    if (status.collector) {
      const c = status.collector;
      bits.push(`collector: ${c.connected ? 'connected' : c.running ? 'connecting' : 'off'}`);
    }
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

  if (typeof window !== 'undefined' && typeof window.addEventListener === 'function') {
    window.addEventListener('resize', scaleWorkspaceStage);
  }

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
  commitWorkspaceLayout,
  computeWorkspaceLayout,
  createFloorState,
  createLiveEventStream,
  isoProject,
  refreshStatus,
  renderFocusFigureHTML,
  renderWorkspaceHTML,
  zoneForStatus,
};
