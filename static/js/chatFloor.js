// static/js/chatFloor.js
//
// A compact isometric "agent floor" for chat turns. The Paperclip Floor
// (static/js/paperclip.js) renders the full office; this is the pocket
// version that sits above the tool timeline while an agent run streams,
// walking one minifig between the stations the tools belong to.
//
// Pure module: no DOM access at import time, so it is unit-testable under
// plain Node. mount() is the only DOM-touching entry point.

// Isometric projection. This is a copy of paperclip.js's isoProject() math
// re-scaled to a 1200x360 stage. It is copied rather than imported because
// paperclip.js touches `document` at module load, which makes it unusable
// from a headless test (and would drag ~1300 lines of office renderer into
// every chat turn). sx/sy sit close to the big Floor's iso angle so the two rooms
// read as the same building.
const STAGE = { w: 1200, h: 360, originX: 390, originY: 20, sx: 6, sy: 2.6 };

// Logical floor is 100 wide x 30 deep.
const FLOOR_W = 100;
const FLOOR_D = 30;

function isoProject(x, y) {
  return {
    px: STAGE.originX + (x - y) * STAGE.sx,
    py: STAGE.originY + (x + y) * STAGE.sy,
  };
}

function pts(points) {
  return points.map((p) => `${p.px.toFixed(1)},${p.py.toFixed(1)}`).join(' ');
}

// 1-2 path line-art glyphs, drawn in a 24x24 box and scaled onto the desk top.
const GLYPHS = {
  desk: '<rect x="4" y="5" width="16" height="11" rx="1.5"/><path d="M9 19h6M12 16v3"/>',
  web: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c3.2 3.6 3.2 14.4 0 18M12 3c-3.2 3.6-3.2 14.4 0 18"/>',
  shell: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M7.5 9.5l3 2.5-3 2.5M13 15h4"/>',
  files: '<path d="M3 7a2 2 0 012-2h4l2 2h8a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2z"/>',
  browser: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 9h18M6.5 6.5h.01M9.5 6.5h.01"/>',
  memory: '<path d="M12 3a6 6 0 00-3.2 11.1V17h6.4v-2.9A6 6 0 0012 3z"/><path d="M9.6 20h4.8"/>',
};

// Logical coords chosen so the six desks spread across the rhombus instead of
// stacking on one iso row. `desk` is home: centre-left, where the figure idles.
export const STATIONS = [
  { id: 'desk', label: 'Desk', x: 8, y: 9 },
  { id: 'files', label: 'Files', x: 10, y: 25 },
  { id: 'web', label: 'Web', x: 38, y: 4 },
  { id: 'shell', label: 'Shell', x: 46, y: 24 },
  { id: 'browser', label: 'Browser', x: 72, y: 5 },
  { id: 'memory', label: 'Memory', x: 80, y: 24 },
];

const STATION_BY_ID = STATIONS.reduce((acc, s) => { acc[s.id] = s; return acc; }, {});

// Tool -> station. Built from the canonical tool names in src/tool_schemas.py.
// Order matters: recall tools (reference_search, search_chats) must be claimed
// by `memory` before the generic /search/ rule sends them to `web`.
export function stationForTool(name) {
  const t = String(name == null ? '' : name).toLowerCase();
  if (!t) return 'desk';
  if (/memor|skill|reference_search|search_chats/.test(t)) return 'memory';
  if (/^browser/.test(t)) return 'browser';
  if (/^bash$|shell|^python|^run_|^pipeline$/.test(t)) return 'shell';
  if (/file|document|list_dir|glob|grep/.test(t)) return 'files';
  if (/^web_|fetch|search|research/.test(t)) return 'web';
  return 'desk';
}

function stationClass(id, st) {
  let cls = `chat-floor-station chat-floor-station--${id}`;
  if (st.visited.indexOf(id) !== -1) cls += ' chat-floor-station--visited';
  if (st.busy && st.at === id) cls += ' chat-floor-station--active';
  if (st.failed.indexOf(id) !== -1) cls += ' chat-floor-station--failed';
  return cls;
}

// An axis-aligned iso box: footprint w x d centred on (gx, gy), extruded `h`
// pixels above the floor. Fills are theme tokens, never literal colours.
function deskSVG(gx, gy, w, d, h) {
  const base = [
    isoProject(gx - w / 2, gy - d / 2),
    isoProject(gx + w / 2, gy - d / 2),
    isoProject(gx + w / 2, gy + d / 2),
    isoProject(gx - w / 2, gy + d / 2),
  ];
  const top = base.map((p) => ({ px: p.px, py: p.py - h }));
  return `<polygon class="chat-floor-desk-l" points="${pts([base[3], top[3], top[2], base[2]])}"/>`
    + `<polygon class="chat-floor-desk-r" points="${pts([base[1], top[1], top[2], base[2]])}"/>`
    + `<polygon class="chat-floor-desk-t" points="${pts(top)}"/>`;
}

function groundSVG() {
  const corners = [
    isoProject(0, 0), isoProject(FLOOR_W, 0),
    isoProject(FLOOR_W, FLOOR_D), isoProject(0, FLOOR_D),
  ];
  const lines = [];
  for (let i = 10; i < FLOOR_W; i += 10) {
    const a = isoProject(i, 0);
    const b = isoProject(i, FLOOR_D);
    lines.push(`<line x1="${a.px.toFixed(1)}" y1="${a.py.toFixed(1)}" x2="${b.px.toFixed(1)}" y2="${b.py.toFixed(1)}"/>`);
  }
  for (let j = 10; j < FLOOR_D; j += 10) {
    const c = isoProject(0, j);
    const e = isoProject(FLOOR_W, j);
    lines.push(`<line x1="${c.px.toFixed(1)}" y1="${c.py.toFixed(1)}" x2="${e.px.toFixed(1)}" y2="${e.py.toFixed(1)}"/>`);
  }
  return `<polygon class="chat-floor-ground" points="${pts(corners)}"/>`
    + `<g class="chat-floor-grid">${lines.join('')}</g>`;
}

const DESK_H = 16;
const GLYPH_SCALE = 1.5;

function stationSVG(s, st) {
  const p = isoProject(s.x, s.y);
  const gx = p.px - (24 * GLYPH_SCALE) / 2;
  const gy = p.py - DESK_H - 34;
  return `<g class="${stationClass(s.id, st)}" data-station="${s.id}">`
    + deskSVG(s.x, s.y, 11, 7, DESK_H)
    + `<g class="chat-floor-glyph" transform="translate(${gx.toFixed(1)},${gy.toFixed(1)}) scale(${GLYPH_SCALE})">${GLYPHS[s.id] || GLYPHS.desk}</g>`
    + `<text class="chat-floor-label" x="${p.px.toFixed(1)}" y="${(p.py + 38).toFixed(1)}" font-size="22" fill="var(--color-muted)" text-anchor="middle">${s.label}</text>`
    + '</g>';
}

function pathSVG(st) {
  const seq = ['desk'].concat(st.visited);
  const points = seq
    .map((id) => STATION_BY_ID[id])
    .filter(Boolean)
    .map((s) => {
      const p = isoProject(s.x, s.y);
      return `${p.px.toFixed(1)},${(p.py - DESK_H).toFixed(1)}`;
    });
  return `<polyline class="chat-floor-path" points="${points.length > 1 ? points.join(' ') : ''}"/>`;
}

// The figure stands just in front of (i.e. deeper than) the desk it is at.
const FIG_OFFSET = 5;

function figTransform(st) {
  const s = STATION_BY_ID[st.at] || STATION_BY_ID.desk;
  const p = isoProject(s.x, s.y + FIG_OFFSET);
  return `translate(${p.px.toFixed(1)},${p.py.toFixed(1)})`;
}

// One minifig, anchored at its feet at the group origin. Same silhouette as
// paperclip.js's minifigSVG() -- legs, hip block, torso, two arms, and a
// rounded head with the stud on top -- at ~0.75 scale for the smaller stage,
// so the strip reads as the same family of figure as the big Floor.
const FIG_BODY =
  '<ellipse class="chat-floor-shadow" cx="0" cy="0" rx="14" ry="5"/>'
  + '<rect class="chat-floor-leg" x="-7" y="-12" width="6" height="12" rx="2"/>'
  + '<rect class="chat-floor-leg" x="1" y="-12" width="6" height="12" rx="2"/>'
  + '<rect class="chat-floor-hip" x="-8" y="-15" width="16" height="4" rx="2"/>'
  + '<rect class="chat-floor-torso" x="-10" y="-32" width="20" height="18" rx="3.5"/>'
  + '<rect class="chat-floor-arm" x="-14.5" y="-30" width="5" height="13" rx="2.5"/>'
  + '<rect class="chat-floor-arm" x="9.5" y="-30" width="5" height="13" rx="2.5"/>'
  + '<circle class="chat-floor-hand" cx="-12" cy="-16" r="2.4"/>'
  + '<circle class="chat-floor-hand" cx="12" cy="-16" r="2.4"/>'
  + '<rect class="chat-floor-stud" x="-4" y="-51" width="8" height="5" rx="2"/>'
  + '<rect class="chat-floor-head" x="-7.5" y="-47" width="15" height="15" rx="4.5"/>';

const FIG_DOTS =
  '<circle cx="-8" cy="-62" r="2.8"/><circle cx="0" cy="-62" r="2.8"/><circle cx="8" cy="-62" r="2.8"/>';

function dotsClass(st) {
  return 'chat-floor-dots' + (st.busy ? ' chat-floor-dots--on' : '');
}

export function createFloorStrip() {
  const st = { at: 'desk', busy: false, visited: [], failed: [], count: {} };
  let root = null;

  function render() {
    const stations = STATIONS.map((s) => stationSVG(s, st)).join('');
    return `<svg viewBox="0 0 ${STAGE.w} ${STAGE.h}" preserveAspectRatio="xMidYMid meet" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="Agent floor: tool activity map">`
      + groundSVG()
      + pathSVG(st)
      + stations
      + `<g class="chat-floor-fig" transform="${figTransform(st)}">${FIG_BODY}<g class="${dotsClass(st)}">${FIG_DOTS}</g></g>`
      + '</svg>';
  }

  // Patch only what changed so the CSS transform transition animates the walk;
  // fall back to a full re-render if the mounted nodes went missing.
  function sync() {
    if (!root) return;
    const fig = root.querySelector('.chat-floor-fig');
    const path = root.querySelector('.chat-floor-path');
    const dots = root.querySelector('.chat-floor-fig > g');
    if (!fig || !path || !dots) { root.innerHTML = render(); return; }
    fig.setAttribute('transform', figTransform(st));
    dots.setAttribute('class', dotsClass(st));
    const tmp = pathSVG(st).match(/points="([^"]*)"/);
    path.setAttribute('points', tmp ? tmp[1] : '');
    STATIONS.forEach((s) => {
      const el = root.querySelector(`[data-station="${s.id}"]`);
      if (el) el.setAttribute('class', stationClass(s.id, st));
    });
  }

  function onToolStart(tool) {
    const id = stationForTool(tool);
    st.at = id;
    st.busy = true;
    if (st.visited[st.visited.length - 1] !== id) st.visited.push(id);
    st.count[id] = (st.count[id] || 0) + 1;
    sync();
  }

  // A tool_output can arrive for a tool the figure has already walked away
  // from (parallel calls, or a late result after the next tool_start). Only
  // the station the figure is actually standing at may clear `busy`; the
  // failure mark always lands on the station the tool belongs to.
  function onToolEnd(tool, ok) {
    const id = stationForTool(tool);
    if (id === st.at) st.busy = false;
    if (!ok && st.failed.indexOf(id) === -1) st.failed.push(id);
    sync();
  }

  function onTurnEnd() {
    st.busy = false;
    st.at = 'desk';
    sync();
  }

  function mount(el) {
    if (!el) return;
    root = el;
    el.innerHTML = render();
  }

  return {
    state: () => ({
      at: st.at,
      busy: st.busy,
      visited: st.visited.slice(),
      failed: st.failed.slice(),
      count: Object.assign({}, st.count),
    }),
    render,
    onToolStart,
    onToolEnd,
    onTurnEnd,
    mount,
  };
}

export default { STATIONS, stationForTool, createFloorStrip };
