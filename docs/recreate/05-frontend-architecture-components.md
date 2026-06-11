# 05 — Frontend Architecture & Components

Apollo's frontend is a **framework-free, single-page application**: one HTML document (`static/index.html`, ~2,400 lines), one consolidated stylesheet (`static/style.css`, ~36,000 lines), and a flat collection of native **ES modules** under `static/js/` orchestrated by `static/app.js`. There is no build step, no bundler, and no virtual DOM — components render by writing template-literal HTML into containers via `innerHTML`, and state lives in module-scope variables plus `localStorage`.

---

## 1. Single-Page Layout (`static/index.html`)

The page is structured as: an inline first-paint theme script, a left **icon rail**, a resizable **sidebar**, the main **chat container**, and a long tail of hidden **modal** overlays (memory, theme, cookbook, Paperclip, browser, settings, etc.) toggled by adding/removing the `hidden` class.

Key landmarks (all real ids from `static/index.html`):

```html
<!-- static/index.html -->
<div class="icon-rail" id="icon-rail">
  <div class="rail-resize-handle" id="rail-resize-handle"></div>
  <!-- Static: core actions -->
  <button class="icon-rail-btn" id="rail-search-btn" title="Search conversations (Ctrl+K)">…</button>
  <button class="icon-rail-btn rail-new-chat" id="rail-new-session" title="New chat">…</button>
  <button class="icon-rail-btn" id="rail-delete-session" title="Delete session">✕</button>
  <!-- Dynamic contextual indicators (shown only while active) -->
  <button class="icon-rail-btn rail-dynamic" id="rail-chats" style="display:none">…</button>
  <!-- Tool launchers — always visible, alphabetical -->
  <button class="icon-rail-btn" id="rail-calendar" title="Calendar">…</button>
  <button class="icon-rail-btn" id="rail-paperclip" title="Paperclip" style="display:none">…</button>
  <button class="icon-rail-btn" id="rail-settings" title="Settings">…</button>
</div>

<nav class="sidebar" id="sidebar" role="navigation" aria-label="Sidebar">
  <div class="sidebar-resize-handle" id="sidebar-resize-handle"></div>
  <div class="sidebar-brand" id="sidebar-brand-btn"><span class="sidebar-brand-title">Apollo</span></div>
  …
</nav>

<main class="chat-container welcome-active" id="chat-container" aria-label="Chat area" aria-busy="false">
  <div id="chat-history" class="chat-history" role="log" aria-live="polite"></div>
  …
</main>
```

Rail buttons follow the convention `rail-<feature>` (`rail-browser`, `rail-compare`, `rail-cookbook`, `rail-research`, `rail-email`, `rail-gallery`, `rail-archive`, `rail-memory`, `rail-notes`, `rail-tasks`, `rail-theme`). Modals follow `<feature>-modal` + `close-<feature>-modal` (e.g. `#paperclip-modal` / `#close-paperclip-modal`).

An inline `<script nonce="{{CSP_NONCE}}">` block at the top of `<head>` applies the saved theme **before first paint**: it reads `localStorage.getItem('apollo-theme')`, sets `--bg/--fg/--panel/--border/--red`, derives the `--hl-*` syntax colors with an inlined HSL converter, swaps the favicon to the accent color, and updates `<meta name="theme-color">` so the mobile toolbar matches from frame one. The `{{CSP_NONCE}}` placeholder is substituted server-side (inline `onload=` attributes are blocked by `script-src-attr`).

### Cache-busting `?v=` convention

Static assets that change frequently are loaded with a manual version query so browsers refetch after a deploy:

```html
<!-- static/index.html -->
<link rel="stylesheet" href="/static/style.css?v=paperclip-floor-20260611d">
…
<script type="module" src="/static/js/chat.js?v=20260520m"></script>
<script type="module" src="/static/js/paperclip.js?v=paperclip-floor-20260611d"></script>
```

The token is bumped by hand when the file changes (date + suffix letter, or a feature tag like `paperclip-floor-20260611d` shared by CSS and JS that ship together).

---

## 2. ES-Module Structure (`static/js/` + `static/app.js`)

All modules are loaded as `<script type="module">` at the bottom of `index.html` in a deliberate order — `storage.js` first, `app.js` second-to-last ("app.js must be LAST"), then `init.js` and `a11y.js`. A trailing inline script registers the service worker (`/static/sw.js`).

`static/app.js` (~4,100 lines) is the orchestrator. It imports every feature module's default export and wires global events:

```js
// static/app.js
import Storage from './js/storage.js';
import uiModule from './js/ui.js';
import fileHandlerModule from './js/fileHandler.js';
import modelsModule from './js/models.js';
import chatModule from './js/chat.js';
import sessionModule from './js/sessions.js';
import memoryModule from './js/memory.js';
import themeModule from './js/theme.js';
import settingsModule from './js/settings.js';
import './js/modalManager.js';
import './js/tileManager.js';
import { initKeyboardShortcuts } from './js/keyboard-shortcuts.js';
import { initSidebarLayout, syncRailSide } from './js/sidebar-layout.js';
import { initSectionCollapse, initSectionDrag } from './js/section-management.js';

const API_BASE = window.location.origin;
```

Notable modules (sizes give a feel for weight): `chat.js` (~221 KB, streaming + send pipeline), `chatRenderer.js`, `chatStream.js`, `sessions.js`, `settings.js` (~240 KB), `admin.js`, `document.js` (~428 KB editor), `notes.js`, `tasks.js`, `calendar.js`, `gallery.js`, `cookbook*.js` (local-model cookbook suite), `compare/index.js`, `theme.js`, `paperclip.js`, plus small utilities (`storage.js`, `spinner.js`, `dragSort.js`, `escMenuStack.js`, `platform.js`).

`static/js/storage.js` centralizes `localStorage` with key constants and parse safety:

```js
// static/js/storage.js
export const KEYS = {
  THEME: 'apollo-theme',
  TOGGLES: 'apollo-toggles',
  SIDEBAR_COLLAPSED: 'sidebar-collapsed',
  CURRENT_SESSION: 'currentSessionId',
  MODEL_ENDPOINTS: 'apollo-model-endpoints',
  MODEL_SELECTED: 'apollo-selected-model',
  INCOGNITO: 'apollo-incognito',
  …
};
export function getJSON(key, fallback) {
  try {
    const raw = localStorage.getItem(key);
    if (raw === null) return fallback !== undefined ? fallback : null;
    return JSON.parse(raw);
  } catch (e) { … return fallback; }
}
```

**Render pattern.** There are no components in the framework sense. A module owns a container element, builds an HTML string with template literals (escaping user data through a local `escapeHTML`), assigns `container.innerHTML = html`, then binds events — frequently via a single delegated listener on `document` rather than per-node handlers. `ui.js` supplies shared helpers (`el()`, `showToast`, `showError`, clipboard, auto-scroll, debounce).

---

## 3. Theme System (`static/js/theme.js`)

### 3.1 Preset table

`THEMES` is a flat map of 24 presets, each defining five base colors (and optionally an `advanced` override block, as `gpt` does):

```js
// static/js/theme.js
export const THEMES = {
  dark:       { bg:'#282c34', fg:'#9cdef2', panel:'#111111', border:'#355a66', red:'#e06c75' },
  light:      { bg:'#f0ebe3', fg:'#5a5248', panel:'#faf6f0', border:'#d4cdc2', red:'#c47d5a' },
  midnight:   { bg:'#0d1117', fg:'#c9d1d9', panel:'#161b22', border:'#30363d', red:'#f85149' },
  paper:      { bg:'#faf8f5', fg:'#3b3836', panel:'#ffffff', border:'#d5d0c8', red:'#c5ac4a' },
  // Spicy / fun themes
  cyberpunk:  { bg:'#0a0a0f', fg:'#0ff0fc', panel:'#12101a', border:'#9b30ff', red:'#e040fb' },
  retrowave:  { bg:'#1a1a2e', fg:'#e94560', panel:'#16213e', border:'#533483', red:'#e94560' },
  forest:     { bg:'#1b2a1b', fg:'#a8d5a2', panel:'#142414', border:'#3d6b3d', red:'#7cb871' },
  ocean:      { bg:'#0b1a2c', fg:'#64d2ff', panel:'#091422', border:'#1e5074', red:'#4facfe' },
  ume:        { bg:'#2b1b2e', fg:'#f5c2e7', panel:'#1e1420', border:'#6c4675', red:'#f5a0c0' },
  copper:     { bg:'#1c1410', fg:'#e8c39e', panel:'#140f0a', border:'#7a5533', red:'#d4764e' },
  terminal:   { bg:'#000000', fg:'#00ff41', panel:'#0a0a0a', border:'#003b00', red:'#00ff41' },
  organs:     { bg:'#0a0406', fg:'#efe1c8', panel:'#15080a', border:'#3a1519', red:'#c83240' },
  lavender:   { bg:'#f3eef8', fg:'#3d3551', panel:'#faf7ff', border:'#cec3de', red:'#9b6dcc' },
  gpt:        { bg:'#212121', fg:'#ececec', panel:'#171717', border:'#424242', red:'#949494',
                advanced: { sendBtnBg: '#949494', sendBtnHover: '#7f7f7f',
                            userBubbleBg: '#2f2f2f', aiBubbleBg: '#171717', inputBg: '#2f2f2f' } },
  claude:     { bg:'#262624', fg:'#f5f4f0', panel:'#30302e', border:'#4a4a47', red:'#c6613f' },
  cute:       { bg:'#fff0f5', fg:'#d4608a', panel:'#fff8fa', border:'#f0c0d0', red:'#ff6b9d' },
  // Classic editor palettes
  nord:       { bg:'#2e3440', fg:'#d8dee9', panel:'#3b4252', border:'#4c566a', red:'#bf616a' },
  dracula:    { bg:'#282a36', fg:'#f8f8f2', panel:'#1e1f29', border:'#6272a4', red:'#ff5555' },
  gruvbox:    { bg:'#282828', fg:'#ebdbb2', panel:'#1d2021', border:'#665c54', red:'#fb4934' },
  rosepine:   { bg:'#191724', fg:'#e0def4', panel:'#1f1d2e', border:'#524f67', red:'#eb6f92' },
  sunset:     { bg:'#251521', fg:'#ffd9a0', panel:'#1a0e17', border:'#7d4a5a', red:'#ff8c5a' },
  // Light modes
  solarized:  { bg:'#fdf6e3', fg:'#586e75', panel:'#eee8d5', border:'#c9c0a3', red:'#cb4b16' },
  mint:       { bg:'#eef7f1', fg:'#29473b', panel:'#ffffff', border:'#b7d8c6', red:'#2f9e6b' },
  contrast:   { bg:'#ffffff', fg:'#111111', panel:'#f4f4f4', border:'#666666', red:'#b00020' },
};
```

Per-theme default decorations live in sibling tables: `THEME_DEFAULT_PATTERN` (e.g. `cyberpunk: 'synapse'`, `ocean: 'constellations'`, `terminal: 'perlin-flow'`, `cute: 'sparkles'`), `THEME_DEFAULT_EFFECT_COLOR` (`midnight: '#ffffff'`, `organs: '#451616'`…), `THEME_DEFAULT_INTENSITY` (`midnight: 0.5`, `terminal: 0.8`…), and `THEME_DEFAULT_FROSTED` (`lavender: true`).

### 3.2 `applyColors` — derivation pipeline

`applyColors(colors)` sets the five base CSS variables, syncs `<meta name="theme-color">`, then **derives** ten syntax-highlight variables via `deriveSyntaxColors` (HSL math off `fg`/`bg`/`red`), fills thirteen "advanced" variables from `ADV_KEYS` (each maps a key like `userBubbleBg` to a CSS var like `--user-bubble-bg`, defaulted by `computeAdvancedDefaults` unless overridden), and finally repaints the favicon:

```js
// static/js/theme.js
export function applyColors(colors) {
  const s = document.documentElement.style;
  s.setProperty('--bg', colors.bg);
  s.setProperty('--fg', colors.fg);
  s.setProperty('--panel', colors.panel);
  s.setProperty('--border', colors.border);
  if (colors.red) s.setProperty('--red', colors.red);
  …
  const syn = deriveSyntaxColors(colors);
  s.setProperty('--hl-keyword', syn.keyword);   // hue-rotated from red
  s.setProperty('--hl-string', syn.string);     // warm 40° hue
  s.setProperty('--hl-comment', syn.comment);   // fg/bg midpoint lightness
  s.setProperty('--hl-function', syn.function); // 210° blue
  …
  const adv = colors.advanced || {};
  const defaults = computeAdvancedDefaults(colors);
  for (const { key, css } of ADV_KEYS) s.setProperty(css, adv[key] || defaults[key]);
  _updateFavicon(colors.red || '#e06c75');
}
```

`generateHarmonyColors(accentHex, harmonyType, mode)` builds an entire palette from one accent using `complementary` / `analogous` / `triadic` / `monochromatic` rules. `_updateFavicon` consults `_ROUTE_FAVICON_SHAPES` so bookmarks of `/calendar`, `/notes`, `/cookbook`, `/email`, `/memory`, `/gallery`, `/tasks`, `/library` keep route-specific icons tinted in the accent color.

### 3.3 Persistence, custom themes, server sync

- Active theme: `localStorage['apollo-theme']` (`LS_KEY`), shape `{ name, colors, font?, density?, bgPattern?, bgEffectColor?, bgEffectIntensity?, bgEffectSize?, frosted? }` — `save()` only stores non-default options. `getSaved()` migrates renamed presets (`chatgpt`→`gpt`, `sakura`→`ume`).
- Custom themes: `localStorage['apollo-custom-themes']`, capped at `MAX_CUSTOM_THEMES = 8`; `saveCustomTheme()` returns `'limit'` when full.
- Server sync: both are mirrored with `PUT /api/prefs/theme` and `PUT /api/prefs/custom-themes` (`_syncToServer` / `_syncCustomThemesToServer`); on boot `_initWithSync()` pulls the server copy when local storage is empty and merges server custom themes that are missing locally.

### 3.4 Backgrounds, fonts, density

Background patterns are body classes (`bg-pattern-dots`, …) plus canvas animations registered in `_CANVAS_PATTERNS = { synapse, rain, constellations, 'perlin-flow', petals, sparkles, embers }`. `applyBgPattern` removes all `_BG_CLASSES`, deletes any old `#…-canvas`, starts the requested canvas loop, and hides the intensity/size sliders for `_STATIC_PATTERNS = new Set(['none','dots'])`. Effect knobs are pure CSS variables: `--bg-effect-color`, `--bg-effect-intensity` (0–1), `--bg-effect-size` (clamped 0.2–3). `applyFrostedGlass(on)` toggles `body.theme-frosted`. Fonts come from `FONT_MAP` (`mono` = Fira Code default, `sans`, `serif`) with user fonts injected via `@font-face` in `_injectFontFace`; density adds `density-compact` / `density-spacious` on `<html>`.

---

## 4. The Paperclip Floor (`static/js/paperclip.js`)

This module renders Paperclip agents as an animated **isometric office**. Three views inside `#paperclip-modal`: **Floor** (the scene), **Board** (kanban lanes), **Classic** (an `<iframe id="paperclip-frame">` of Paperclip's own origin). Buttons carry `data-paperclip-view="floor|board|classic"`; `setView()` toggles `.hidden` and `aria-pressed`.

### 4.1 Event → state engine

State is a plain object from `createFloorState()`: `{ agents: Map, deskAssignments: Map, activity: [], messages: [], selectedAgentId, source: 'preview', lastUpdated }`. `applyFloorEvent(state, event)` is a pure-ish reducer over five event types:

- `agent.status` / `heartbeat.run.queued` / `heartbeat.run.status` → `ensureAgent()` (assigns the next free desk slot on first sight), maps the status through `zoneForStatus()` (`running/working/in_progress/active/thinking`→`working`; `review/...`→`review`; `blocked/error/failed/crashed`→`blocked`; `done/complete/success`→`done`; else `backlog`), stamps `doneAt` on entering `done`, and logs `"${name} -> ${zone}"` to the activity rail.
- `heartbeat.run.log` → pushes the chunk onto `agent.transcript` (cap 32) and forces the agent into `working`/thinking.
- `heartbeat.run.event` → pushes a tool chip onto `agent.tools` (cap 8), forces `working`.
- `activity.logged` → records a from→to message in `state.messages` (cap 12) and on both agents' `messages` (cap 8).
- Anything else → a generic activity line.

### 4.2 Logical layout (`computeWorkspaceLayout`)

Coordinates are logical 0–100 on both axes. Fixed anchors:

```js
// static/js/paperclip.js
const SHARED_STATIONS = {
  review:  { id: 'review',  label: 'Review Table', x: 76, y: 18 },
  blocked: { id: 'blocked', label: 'Help Bar',     x: 14, y: 74 },
  done:    { id: 'done',    label: 'Done Dock',    x: 76, y: 70 },
};
const EXIT_SPOT = { x: 7, y: 84 };
const EXIT_LINGER_MS = 20000;
const OFFICE_DESKS = [
  { x: 16, y: 32 }, { x: 36, y: 32 }, { x: 56, y: 32 },
  { x: 16, y: 56 }, { x: 36, y: 56 }, { x: 56, y: 56 },
];
const CONVERSATION_WINDOW_MS = 45000;
```

`workspacePoint()` places `working`/`backlog` agents at their own desk chair (`y + 3`), `done` agents at `EXIT_SPOT` fanned by index, and everyone else at the matching shared station with a 5-entry spread table. Desk overflow wraps with a lap offset (`deskPointFor`: `slot.x + lap*5`). The layout pass then:

1. Filters out done agents whose `doneAt` is older than `EXIT_LINGER_MS` — they "leave the office" (their desk stays; the Board still lists them).
2. Builds `interactions` from `state.messages` younger than `CONVERSATION_WINDOW_MS` (max 6); the first two become `conversations` with `fromText` (the message) and `toText` from `conversationLineFor(to)` (task-aware lines like `` `Stuck on ${task}, could use a hand.` ``). For the newest conversation the sender **walks over**: `from.x = clampX(to.x + side*13)`.
3. Marks `agent.moving` when final position differs from `fromX/fromY`, computes `pose` = `talking | walking | sitting | standing`.
4. Picks up to 2 `murmurs` (working, non-talking agents mumble `transcript[0]` or `Working on ${task}`) and up to 3 `callouts` (review/blocked/done agents narrate themselves; departing agents append `"Heading out!"`).

**Walk-once movement.** `computeWorkspaceLayout` sets `fromX/fromY` from the agent's persisted `lastX/lastY`; after each render `commitWorkspaceLayout(state, layout)` writes the rendered `x/y` back to `lastX/lastY`. A CSS transition animates from `--from-x/--from-y` to `--agent-x/--agent-y` exactly once per actual move.

### 4.3 Isometric projection & SVG scene

```js
// static/js/paperclip.js
const STAGE = { w: 1200, h: 740, originX: 600, originY: 96, sx: 5.5, sy: 3.0 };
function isoProject(x, y) {
  return { px: STAGE.originX + (x - y) * STAGE.sx,
           py: STAGE.originY + (x + y) * STAGE.sy };
}
```

`isoBoxSVG(gx, gy, w, d, hBottom, hTop, fill)` extrudes an axis-aligned box into three polygons (left/right/top faces) — the building block for desks, chairs, monitors, the meeting table, kitchen, lounge, vending machine, and ping-pong table (`PALETTE` holds all face colors). The room itself is `floorSVG()` (grid every 10 units), `wallsSVG()` (96 px walls), and `wallDecorSVG()` — a kanban board with sticky notes, picture frames, two `windowSVG` daylight windows, `wallClockSVG`, and `exitDoorSVG()` (ajar door at wall y≈80–90 with a glowing `EXIT` sign that matches `EXIT_SPOT`).

`renderWorkspaceHTML` paints everything through a single **depth-sorted paint list** so nearer objects genuinely occlude farther ones; ties paint agents after furniture so a seated agent shows in front of their desk:

```js
// static/js/paperclip.js
const items = [
  ...layout.desks.map((desk) => ({ depth: desk.x + desk.y, kind: 0, svg: deskSVG(desk) })),
  ...layout.stations.map((s) => ({ depth: s.x + s.y, kind: 0, svg: stationSVG(s) })),
  ...decorSVG().map((piece) => ({ ...piece, kind: 0 })),
  ...layout.agents.map((agent) => ({ depth: agent.x + agent.y, kind: 1,
    svg: renderWorkspaceAgentSVG(agent, agent.id === state.selectedAgentId) })),
].sort((a, b) => (a.depth - b.depth) || (a.kind - b.kind));
```

Agents are Lego-style minifigs (`minifigSVG()`: legs/torso/arms/head groups, eyes, smile) drawn directly into the scene SVG with classes like `pose-walking`, `role-coding`, `zone-review`, plus a walk-trail line, a name chip, a speech burst when talking and thinking dots when working. Speech/murmur/callout bubbles are HTML `<div>`s positioned by projected `--bubble-x/--bubble-y` over the SVG. `renderFocusFigureHTML(agent)` reuses the same minifig in the focus pane. `scaleWorkspaceStage()` fits the fixed 1200×740 stage into `#paperclip-zone-grid` with `transform: scale()` clamped to 0.35–1.5.

### 4.4 Live stream, demo loop, render skip

`createLiveEventStream()` wraps an `EventSource` on `/api/paperclip/stream` (`withCredentials: true`). It tolerates auto-reconnect (`onerror` only downgrades to `preview` once `readyState === CLOSED`), treats `paperclip.stream.waiting` as "stay live, don't forward", and `paperclip.stream.unavailable` as a terminal fallback to preview. `handleLiveEvent` swaps the demo state for a fresh live state on the **first real event**. On stream failure, `scheduleLiveRetry()` retries after 20 s while the modal is open.

Until live events arrive, `startPreviewLoop()` seeds `PREVIEW_SEED_COUNT = 6` of the 16 scripted `DEMO_EVENTS` and advances one every 2.6 s. `startFloorUpdates()` additionally runs an **ambient 10-second tick** (`window.setInterval(renderFloor, 10000)`) so time-driven states (exit-door departures, conversation aging) refresh without events — cheap because `renderZones()` skips identical HTML:

```js
// static/js/paperclip.js
let _lastFloorHTML = '';
function renderZones(state = _floorState, layout = undefined) {
  const html = renderWorkspaceHTML(state, layout);
  // Rewriting identical markup restarts every CSS animation; skip no-ops.
  if (html !== _lastFloorHTML || !zoneGrid.firstChild) {
    zoneGrid.innerHTML = html;
    _lastFloorHTML = html;
  }
  scaleWorkspaceStage();
}
```

`applyStatus()` (fed by `GET /api/paperclip/status`) shows/hides `#tool-paperclip-btn` and `#rail-paperclip`, fills the settings card (`set-paperclipState`, `set-paperclipEndpoint`, collector state), and stores `browser_url` as the Classic iframe `src` (loaded lazily on first Classic view). Agent selection is a delegated capture-phase click/keydown listener on `[data-agent-id]` (`bindAgentSelection`, deduped per document via a `WeakSet`).

---

## 5. CSS Architecture (`static/style.css`)

A single stylesheet organized as a token layer plus feature sections. The `:root` block documents the public contract:

```css
/* static/style.css */
:root {
  /* Core palette */
  --bg: #282c34;  --fg: #9cdef2;  --panel: #111;
  --border: #355a66;  --red: #e06c75;
  /* Syntax highlighting */
  --hl-bg: #1e2228; --hl-keyword: #c678dd; --hl-string: #e5c07b; …
  /* Semantic colors */
  --color-error: #ff4444; --color-success: #4caf50; --color-warning: #f0ad4e; …
  --select-option-bg: color-mix(in srgb, var(--panel) 74%, var(--bg));
  --select-option-active-bg: color-mix(in srgb, var(--accent, var(--red)) 24%, var(--panel));
}
```

Derived tints are built with **`color-mix()`** rather than hardcoded shades (~1,370 usages), so every preset and custom theme automatically produces consistent hovers, overlays, and active states from the five base variables. Light-mode fallback values live under `:root.light`.

Motion is gated for accessibility: five `@media (prefers-reduced-motion: reduce)` blocks disable the Paperclip walk/pose/typing animations, the memory-modal synapse pulse, and other ambient effects, e.g.:

```css
/* static/style.css */
@media (prefers-reduced-motion: reduce) {
  #paperclip-modal .paperclip-roaming-agent,
  #paperclip-modal .paperclip-fig,
  #paperclip-modal .paperclip-speech-burst circle,
  #paperclip-modal .paperclip-desk-screen, … { /* animations off */ }
}
```

Density variants (`html.density-compact`, `html.density-spacious`) and the frosted-glass treatment (`body.theme-frosted`, translucent panels + `backdrop-filter` blur) are class-scoped so theme.js can flip them without touching individual rules.
