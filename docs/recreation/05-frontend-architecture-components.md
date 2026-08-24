# 05 — Frontend Architecture & Components

Apollo's frontend is a **framework-free, no-build single page application**: one HTML document (`static/index.html`, 2,753 lines), a set of per-feature stylesheets under `static/css/`, and ~172 native browser **ES modules** under `static/js/` orchestrated by `static/app.js`. There is no bundler, no transpiler, no virtual DOM. Every `<script>` in `index.html` is `type="module"`, loaded directly by the browser; state lives in module-scope variables, `localStorage`, and a handful of `window.*` globals used as an escape hatch for cross-module calls that would otherwise create import cycles.

---

## 1. Module loading strategy (`static/index.html`)

### 1.1 Head-level preloads and first-paint theming

Before any module script runs, `index.html` has two inline, CSP-nonced `<script nonce="{{CSP_NONCE}}">` blocks in `<head>` (`{{CSP_NONCE}}` is substituted server-side per request):

- **Lines 17–96**: reads `localStorage.getItem('apollo-theme')`, applies `--bg/--fg/--panel/--border/--red` and derives `--hl-*` syntax-highlight colors via an inlined RGB→HSL→RGB converter (`h2hsl`/`hsl2h`), so the loading screen and first paint already match the saved theme instead of flashing a default and then swapping.
- **Lines 100–192**: per-route favicon/title/PWA-manifest swap — reads `window.location.pathname`, looks up an SVG shape in a `SHAPES` map keyed by route (`/calendar`, `/notes`, `/cookbook`, `/email`, `/memory`, `/gallery`, `/tasks`, `/library`), and rewrites `<link rel="icon">`, `document.title`, and a Blob-URL PWA manifest so bookmarking a sub-route gets its own icon/name.

`modulepreload` hints warm the module graph before the trailing `<script type="module">` tags execute:

```html
<!-- static/index.html:243-248 -->
<link rel="modulepreload" href="/static/app.js">
<link rel="modulepreload" href="/static/js/chat.js">
<link rel="modulepreload" href="/static/js/ui.js">
<link rel="modulepreload" href="/static/js/browserPanel.js">
<link rel="modulepreload" href="/static/js/sessions.js">
<link rel="modulepreload" href="/static/js/markdown.js">
```

CSS is split into ~22 per-feature files loaded in a fixed order (specificity depends on it — `mobile-overrides.css` is deliberately last):

```html
<!-- static/index.html:220-242 -->
<link rel="stylesheet" href="/static/css/variables.css?v=split-20260708">
<link rel="stylesheet" href="/static/css/base.css?v=split-20260708">
...
<link rel="stylesheet" href="/static/css/mobile-overrides.css?v=split-20260708">
```

External libraries (`highlight.min.js`, `katex.min.js`/`.css`, `mermaid.min.js`) are loaded with `defer`/`async` and CDN URLs pinned to a version (`katex@0.16.22`, `mermaid@11`); KaTeX's stylesheet loads with `media="print"` then flips to `all` on its own `load` event so it never blocks first paint.

### 1.2 The module tag order

All feature modules are `<script type="module">` tags at the very end of `<body>` (`index.html:2696-2733`), loaded in this exact order:

```html
<script type="module" src="/static/js/storage.js"></script>
<script type="module" src="/static/js/ui.js"></script>
<script type="module" src="/static/js/markdown.js"></script>
<script type="module" src="/static/js/dragSort.js"></script>
<script type="module" src="/static/js/sessions.js"></script>
<script type="module" src="/static/js/memory.js"></script>
<script type="module" src="/static/js/memoryGraph.js"></script>
<script type="module" src="/static/js/skills.js"></script>
<script type="module" src="/static/js/tourHints.js"></script>
<script type="module" src="/static/js/tourAutoplay.js"></script>
<script type="module" src="/static/js/fileHandler.js"></script>
<script type="module" src="/static/js/voiceRecorder.js"></script>
<script type="module" src="/static/js/voiceCall.js"></script>
<script type="module" src="/static/js/models.js"></script>  <!-- This must come BEFORE app.js -->
<script type="module" src="/static/js/rag.js"></script>
<script type="module" src="/static/js/presets.js"></script>
<script type="module" src="/static/js/search.js"></script>
<script type="module" src="/static/js/spinner.js"></script>
<script type="module" src="/static/js/tts-ai.js"></script>
<script type="module" src="/static/js/review.js"></script>
<script type="module" src="/static/js/document.js"></script>
<script type="module" src="/static/js/gallery.js"></script>
<script type="module" src="/static/js/chatRenderer.js"></script>
<script type="module" src="/static/js/codeRunner.js"></script>
<script type="module" src="/static/js/chatStream.js"></script>
<script type="module" src="/static/js/chat.js?v=20260520m"></script>
<script type="module" src="/static/js/cookbook.js"></script>
<script type="module" src="/static/js/paperclip.js?v=paperclip-floor-20260611d"></script>
<script type="module" src="/static/js/search-chat.js"></script>
<script type="module" src="/static/js/compare/index.js"></script>
<script type="module" src="/static/js/theme.js"></script>
<script type="module" src="/static/js/censor.js"></script>
<script type="module" src="/static/js/settings.js"></script>
<script type="module" src="/static/js/admin.js"></script>
<script type="module" src="/static/js/assistant.js"></script>
<script type="module" src="/static/app.js"></script>  <!-- app.js must be LAST -->
<script type="module" src="/static/js/init.js"></script>
<script type="module" src="/static/js/a11y.js"></script>
<script nonce="{{CSP_NONCE}}">if('serviceWorker' in navigator){navigator.serviceWorker.register('/static/sw.js').catch(()=>{});}</script>
```

Because these are ES modules, the browser executes them in document order but each only runs after its own `import` graph resolves — the HTML comments (`must come BEFORE app.js`, `app.js must be LAST`) encode a real dependency: `app.js` imports the default export of most of the earlier-loaded modules and wires their DOM event listeners, so it must run after they've registered their own top-level state. Cache-busting uses a hand-bumped `?v=` query string on files that change often (date + suffix letter, e.g. `?v=20260520m`, or a shared feature tag like `paperclip-floor-20260611d` used by both the CSS and JS that ship together) — there is no build step to hash content automatically.

### 1.3 `static/app.js` — the orchestrator (4,293 lines)

`app.js` imports the default export of every major feature module plus a few named imports, stashes some on `window` for cross-module access, monkey-patches `window.fetch` for a global 401→redirect, and defines `startApolloApp()` which does the actual DOM wiring:

```js
// static/app.js:5-53 (imports abridged)
import Storage from './js/storage.js';
import uiModule from './js/ui.js';
import fileHandlerModule from './js/fileHandler.js';
import modelsModule from './js/models.js';
import ragModule from './js/rag.js';
import presetsModule from './js/presets.js';
import searchModule from './js/search.js';
import chatModule from './js/chat.js';
import compareModule from './js/compare/index.js';
import documentModule from './js/document.js';
import searchChatModule from './js/search-chat.js';
import markdownModule from './js/markdown.js';
import chatRenderer from './js/chatRenderer.js';
import sessionModule from './js/sessions.js';
import memoryModule from './js/memory.js';
import voiceRecorderModule from './js/voiceRecorder.js';
import censorModule from './js/censor.js';
import galleryModule from './js/gallery.js';
import tasksModule from './js/tasks.js';
import activityModule from './js/activity.js';
import calendarModule from './js/calendar.js';
import notesModule from './js/notes.js';
import adminModule from './js/admin.js';
import settingsModule from './js/settings.js';
import './js/modalManager.js';
import './js/tileManager.js';
import themeModule from './js/theme.js';
import cookbookModule from './js/cookbook.js';
import groupModule from './js/group.js';
import * as researchPanelModule from './js/research/panel.js';
import browserPanelModule from './js/browserPanel.js';
import ttsModule from './js/tts-ai.js';
import spinnerModule from './js/spinner.js';
import { initKeyboardShortcuts } from './js/keyboard-shortcuts.js';
import { initSidebarLayout, syncRailSide } from './js/sidebar-layout.js';
import { initSectionCollapse, initSectionDrag } from './js/section-management.js';

const API_BASE = window.location.origin;
window.themeModule = themeModule;
window.sessionModule = sessionModule;
window.uiModule = uiModule;
window.adminModule = adminModule;
window.cookbookModule = cookbookModule;

// Redirect to login on 401 from any fetch
const _origFetch = window.fetch;
window.fetch = async function(...args) {
  const res = await _origFetch.apply(this, args);
  if (res.status === 401 && !String(args[0]).includes('/api/auth/')) {
    window.location.href = '/login';
  }
  return res;
};
```

`startApolloApp()` (defined at `app.js:3517`, ~800 lines) wires every button click handler, restores sidebar section order/collapse state, and calls into the imported modules. It runs on `DOMContentLoaded` if the document is still loading, or synchronously otherwise:

```js
// static/app.js:4288-4293
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', startApolloApp, { once: true });
} else {
  startApolloApp();
}
```

### 1.4 `static/js/init.js` — post-app initialization

`init.js` runs after `app.js` and handles concerns that need the fully-wired DOM: clearing a stale composer draft on fresh page loads (`clearFreshComposerRestore`), a **defense-in-depth state wipe** — if `/api/auth/status`'s `username` differs from the `apollo-auth-user` cached in `localStorage`, every `localStorage`/`sessionStorage` key except `apollo-last-user`/`apollo-auth-user` is deleted (a second user signing in on the same browser without an explicit logout doesn't inherit the previous user's last session, model choice, or draft input):

```js
// static/js/init.js:27-47
(async () => {
  try {
    const res = await fetch('/api/auth/status', { credentials: 'same-origin' });
    if (!res.ok) return;
    const data = await res.json().catch(() => ({}));
    const liveUser = (data && data.username) || '';
    if (!liveUser) return;
    const KEY = 'apollo-auth-user';
    const cachedUser = localStorage.getItem(KEY);
    if (cachedUser && cachedUser !== liveUser) {
      const _keepKeys = new Set(['apollo-last-user', KEY]);
      const toRemove = [];
      for (let i = 0; i < localStorage.length; i++) {
        const k = localStorage.key(i);
        if (k && !_keepKeys.has(k)) toRemove.push(k);
      }
      toRemove.forEach(k => localStorage.removeItem(k));
      sessionStorage.clear();
      clearFreshComposerRestore();
    }
    localStorage.setItem(KEY, liveUser);
    ...
```

The same block also applies **per-user UI privilege gates** from `/api/auth/status`'s `privileges` object — purely cosmetic (the backend enforces the real restriction), hiding e.g. the document-editor button when `privs.can_use_documents` is false, or forcing Chat mode and hiding the Agent toggle when `privs.can_use_agent === false`. `init.js` also owns the resizable-sidebar drag handlers (`sidebar-resize-handle` / `rail-resize-handle`, min/max/collapse thresholds `MIN_WIDTH=200`, `MAX_WIDTH=700`, `COLLAPSE_THRESHOLD=150`, persisted to `Storage.KEYS.SIDEBAR_WIDTH`), and a `welcome-ready` class release gated on `document.fonts.ready` (with a 1200ms hard-fallback timeout) so the welcome-screen entrance animation never plays mid-layout-shift.

---

## 2. State management pattern

Apollo has **no central store**. State is distributed across three mechanisms, chosen per concern:

### 2.1 `static/js/storage.js` — the localStorage façade

Every module that persists client-side state goes through this module rather than calling `localStorage` directly. It exports a `KEYS` constant map (so key strings exist in exactly one place) plus `get`/`set`/`getJSON`/`setJSON`/`remove`, all wrapped in try/catch so a quota error or corrupt JSON degrades to a fallback instead of throwing:

```js
// static/js/storage.js:1-27
export const KEYS = {
  THEME: 'apollo-theme',
  TOGGLES: 'apollo-toggles',
  SIDEBAR_COLLAPSED: 'sidebar-collapsed',
  SIDEBAR_WIDTH: 'sidebar-width',
  SIDEBAR_SIDE: 'sidebar-side',
  CURRENT_SESSION: 'currentSessionId',
  COMPARE_SAVE: 'compare-save-results',
  COMPARE_CHAT: 'compare-continue-chat',
  COMPARE_BLIND: 'compare-blind',
  COMPARE_RANDOM: 'compare-randomize',
  MODELS_EXPANDED: 'apollo-model-expanded',
  MODEL_ENDPOINTS: 'apollo-model-endpoints',
  MODEL_SELECTED: 'apollo-selected-model',
  SORT_ORDER: 'apollo-sessions-sort',
  CHAT_SEARCH_SCOPE: 'apollo-search-scope',
  INCOGNITO: 'apollo-incognito',
  RAG_ACTIVE: 'apollo-rag-active',
  MCP_ACTIVE: 'apollo-mcp-active',
  SECTION_ORDER: 'sidebar-section-order',
  ADMIN_LAST_TAB: 'admin-last-tab',
  DENSITY: 'apollo-density'
};

export function getJSON(key, fallback) {
  try {
    const raw = localStorage.getItem(key);
    if (raw === null) return fallback !== undefined ? fallback : null;
    return JSON.parse(raw);
  } catch (e) {
    console.warn('[Storage] Failed to parse key "' + key + '":', e.message);
    return fallback !== undefined ? fallback : null;
  }
}
```

`Storage.KEYS.TOGGLES` is the one general-purpose bucket for feature toggles (`web`, `bash`, `rag`, `research`, `incognito`, `mode`) — read/written as a single JSON blob via `loadToggleState()`/`saveToggleState()`/`getToggle(name, fallback)`.

### 2.2 Per-module exported `let` state

Each feature module owns its own in-memory state as module-scope `let`/`const` variables, not exported directly, with accessor functions exported instead (a hand-rolled encapsulation, since ES modules don't give write access to importers). Example from `fileHandler.js`:

```js
// static/js/fileHandler.js:10-17
let pendingFiles = [];
let uploaded = [];
let _lastUploadedMeta = [];
let API_BASE = '';
let _uploadSpinners = [];
const _previewUrls = new WeakMap();
```

`app.js` does not read `pendingFiles` directly; it calls exported functions (`fileHandlerModule.addFiles(...)`, `.renderAttachStrip()`, `.uploadPending()`, etc.). Modules are default-exported as an object bundling their public functions (see `theme`, `session`, `settings`, `admin` etc. all imported as `xModule` and referenced as `xModule.fn()`).

### 2.3 `window.*` globals as the cross-module escape hatch

Where two modules need to call each other but a static `import` would create a cycle (e.g. `chat.js` needing to reach into `theme.js`/`admin.js`, or vice versa), the producing module stashes its public API on `window`:

```js
// static/app.js:50-54
window.themeModule = themeModule;
window.sessionModule = sessionModule;
window.uiModule = uiModule;
window.adminModule = adminModule;
window.cookbookModule = cookbookModule;
```

Other examples found across the codebase: `window._isAdmin` (boolean, set once auth status is fetched, read by `settings.js`'s tab gating), `window.aiTTSManager`, `window._syncRagIndicator`, `window._setWebMode`, `window.sessionModule.selectSession(...)` (called from `chatStream.js`'s notification click handler). This is a deliberate, pervasive pattern rather than an anti-pattern the codebase is trying to avoid — grep for `window\.` across `static/js/*.js` turns up dozens of these bridges.

### 2.4 Custom DOM events for decoupled notification

`modalManager.js` broadcasts modal lifecycle via `CustomEvent` rather than a callback registry, so unrelated modules can react without importing it:

```js
// static/js/modalManager.js — _emitModalOpened
function _emitModalOpened(id, modal) {
  try {
    window.dispatchEvent(new CustomEvent('apollo:modal-opened', {
      detail: { id, modal },
    }));
  } catch (_) {}
}
```

---

## 3. DOM construction / rendering approach

There is **no templating engine**. Two techniques are used side by side, chosen per call site:

1. **Imperative `document.createElement` + property assignment** — the default for anything built repeatedly or needing careful event-listener attachment (avoids re-parsing HTML strings and re-binding listeners on every render). Example, `fileHandler.js`'s attachment chip:

```js
// static/js/fileHandler.js (renderAttachStrip / _createChip)
const badge = document.createElement('div');
badge.className = 'thumb thumb-collapsed';
const label = document.createElement('span');
label.textContent = total + ' file' + (total > 1 ? 's' : '');
label.className = 'thumb-collapsed-label';
badge.appendChild(label);
...
badge.addEventListener('click', (e) => { ... });
```

2. **Template-literal HTML strings assigned to `.innerHTML`** — used for larger, mostly-static blocks (modal bodies, list rows rendered from server JSON) where hand-building every node would be excessive. `chatRenderer.js` (2,105 lines) is the heaviest user of this style for message bodies. All user-controllable text going into `innerHTML` is passed through the shared escaper:

```js
// static/js/ui.js:778-806
const _ESC_MAP = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
export function esc(s) {
  ...
}
```

`ui.js` exports a small set of framework-like helpers used everywhere instead of a component system: `el(id)` (a one-line `document.getElementById` wrapper), `esc()`, `showToast()`, `showError()`, `scrollHistory()`/`scrollHistoryInstant()`, `autoResize()` (textarea auto-grow), `debounce()`, and `styledConfirm()`/`styledPrompt()` (promise-based replacements for `window.confirm`/`prompt` styled to match the theme):

```js
// static/js/ui.js:589
export function el(id) { return document.getElementById(id); }
```

Markdown rendering (`markdown.js`) hand-rolls a fenced-code/math/mermaid preprocessor: it pulls out ` ```mermaid ` blocks and `$$...$$`/`$...$` math spans into placeholder tokens before running the rest of the text through the markdown pass (to keep raw LaTeX/mermaid syntax from being mangled by markdown escaping), then re-substitutes the rendered `katex.renderToString(...)` HTML and `<pre class="mermaid">` blocks back in:

```js
// static/js/markdown.js:488-523 (abridged)
if (window.katex) {
  ...
  mathBlocks.push(katex.renderToString(raw.trim(), { displayMode: true, throwOnError: false }));
  ...
}
```

```js
// static/js/markdown.js:665-671
if (!window.mermaid) return;
const pending = target.querySelectorAll('pre.mermaid:not([data-processed])');
...
window.mermaid.run({ nodes: pending });
```

Syntax highlighting is `highlight.js` (`window.hljs.highlightElement(block)`), invoked both after markdown render and once at the end of `startApolloApp()` for any server-rendered code blocks still missing the `.hljs` class.

---

## 4. Sidebar navigation & view-switching mechanism

### 4.1 Two navigation surfaces: icon rail + sidebar

The layout has an always-visible **icon rail** (`#icon-rail`) with core actions (search, new chat, delete session) plus tool launchers, and a collapsible **sidebar** (`#sidebar`) with expandable sections. The rail buttons follow the id convention `rail-<feature>`; sidebar tool rows follow `tool-<feature>-btn`. The `tools-section` in `index.html` (lines 879–1000) lists every tool as a `.list-item` div with an inline SVG icon and a `.grow` label span:

```html
<!-- static/index.html:879-1000 (structure, abridged) -->
<div class="section" id="tools-section">
  <div class="section-header-flex"><span class="section-title">...Tools</span></div>
  <div class="list-item" id="tool-memory-btn">...<span class="grow">Brain</span></div>
  <div class="list-item" id="tool-calendar-btn">...<span class="grow">Calendar</span></div>
  <div class="list-item" id="tool-browser-btn">...<span class="grow">Browser</span></div>
  <div class="list-item" id="tool-compare-btn">...<span class="grow">Compare</span></div>
  <div class="list-item" id="tool-cookbook-btn">...<span class="grow">Cookbook</span>
    <span id="cookbook-bg-status" ...></span>
    <span class="cookbook-notif-dot" id="cookbook-notif-dot" ...></span>
  </div>
  <div class="list-item" id="tool-paperclip-btn" style="display:none;">...<span class="grow">Paperclip</span></div>
  <div class="list-item" id="tool-research-btn">...<span class="grow">Deep Research</span></div>
  <div class="list-item" id="tool-gallery-btn">...<span class="grow">Gallery</span></div>
  <div class="list-item" id="tool-library-btn">...<span class="grow">Library</span>
    <button id="library-new-doc-btn">new</button>
  </div>
  <div class="list-item" id="tool-notes-btn">...<span class="grow">Notes</span></div>
  <div class="list-item" id="tool-tasks-btn">...<span class="grow">Tasks</span>
    <span id="assistant-notif-dot" class="sidebar-notif-dot" style="display:none"></span>
  </div>
  <div class="list-item" id="tool-activity-btn" style="display:none">...<span class="grow">Agent History</span></div>
  <div class="list-item" id="tool-theme-btn">...</div>
</div>
```

Chat is not a "tool" — it is the default main view (`#chat-container` / `#chat-history`), always present; sessions are switched via the `sessions-section` list, not a tool button. Email is its own `.section#email-section` above `tools-section`, with its own unread-dot badge. There is no route table; "views" are DOM subtrees toggled visible/hidden, and the browser URL is largely decorative (only used by the per-route favicon script, not by an SPA router — no `pushState`/`popstate` navigation was found wiring tool switches).

### 4.2 Rail → sidebar-button delegation

Rail buttons don't implement their own open/close logic — they simply `.click()` the corresponding sidebar button, so all real behavior lives in one place:

```js
// static/app.js:3586-3609
const _railToolMap = {
  'rail-browser':   'tool-browser-btn',
  'rail-compare':   'tool-compare-btn',
  'rail-research':  'tool-research-btn',
  'rail-cookbook':   'tool-cookbook-btn',
  'rail-archive':   'tool-library-btn',
  'rail-gallery':   'tool-gallery-btn',
  'rail-tasks':     'tool-tasks-btn',
  'rail-calendar':  'tool-calendar-btn',
  'rail-notes':     'tool-notes-btn',
  'rail-memory':    'tool-memory-btn',
  'rail-theme':     'tool-theme-btn',
  'rail-email':     'email-section-title',
};
Object.entries(_railToolMap).forEach(([railId, toolId]) => {
  const railBtn = el(railId);
  if (railBtn) {
    railBtn.addEventListener('click', () => {
      const toolBtn = el(toolId);
      if (toolBtn) toolBtn.click();
    });
  }
});
```

### 4.3 `modalManager.js` — the actual open/minimize/close state machine

Each `tool-*-btn`'s own click handler (defined inside that feature's module, e.g. `calendar.js:558`, `notes.js:1110`) builds/shows its modal or fullscreen panel and calls `Modals.register(id, {...})`. `modalManager.js` (1,550 lines) is the shared engine behind every one of these tool surfaces, documented in its own header comment:

```js
// static/js/modalManager.js:1-24
/**
 * ModalManager — unified open/minimize/close behavior for tool modals.
 *
 * Goals:
 *  - Tab-down (swipe) and the `_` button MINIMIZE: modal hidden, JS state preserved.
 *  - The ✕ button CLOSES: tears down via the registered closeFn.
 *  - Sidebar/rail click handler: closed → open, minimized → restore, open → minimize.
 *  - Rail icon shows a "minimized" badge when state is held.
 *
 * Usage from a tool module:
 *
 *   import * as Modals from './modalManager.js';
 *
 *   Modals.register('gallery-modal', {
 *     railBtnId: 'tool-gallery-btn',
 *     restoreFn: () => { ... },
 *     closeFn:   () => { ... },
 *   });
 *
 *   if (!Modals.toggle('gallery-modal')) {
 *     openGallery();
 *   }
 */
```

For tool modals that never call `register()` explicitly (e.g. because they only need minimize behavior after a swipe-dismiss), `_AUTO_WIRE` provides a fallback registry mapping modal id → `{rail, sidebar}` button ids, auto-registered on first minimize:

```js
// static/js/modalManager.js:29-52
const _AUTO_WIRE = {
  'cookbook-modal':       { rail: 'rail-cookbook',  sidebar: 'tool-cookbook-btn' },
  'calendar-modal':       { rail: 'rail-calendar',  sidebar: 'tool-calendar-btn' },
  'gallery-modal':        { rail: 'rail-gallery',   sidebar: 'tool-gallery-btn' },
  'tasks-modal':          { rail: 'rail-tasks',     sidebar: 'tool-tasks-btn' },
  'doclib-modal':         { rail: 'rail-archive',   sidebar: 'tool-library-btn' },
  'memory-modal':         { rail: null,             sidebar: 'tool-memory-btn' },
  'notes-panel':          { rail: 'rail-notes',     sidebar: 'tool-notes-btn' },
  'email-lib-modal':      { rail: null,             sidebar: null },
  'research-overlay':     { rail: 'rail-research',  sidebar: 'tool-research-btn' },
  'theme-modal':          { rail: null,             sidebar: 'tool-theme-btn' },
  'settings-modal':       { rail: null,             sidebar: 'tool-settings-btn' },
  'compare-model-overlay':{ rail: 'rail-compare',   sidebar: 'tool-compare-btn' },
  'ge-shortcuts-modal':   { rail: null,             sidebar: null },
  'custom-preset-modal':  { rail: null,             sidebar: null },
};
```

Basic show/hide is a `hidden` class toggle plus, for dockable tool windows, `applyEdgeDock`/`clearRightDock` from `modalSnap.js` and drag-to-tile-zone logic from `tileManager.js` (`previewZoneAt`, `snapModalToZone`). Z-index ordering for "bring to front" is a simple monotonic counter starting above the static CSS z-indexes:

```js
// static/js/modalManager.js:63-67
let _modalTopZ = 300;
function _bringToFront(modal) {
  if (modal) modal.style.setProperty('z-index', String(++_modalTopZ), 'important');
}
```

---

## 5. Chat UI: message rendering and SSE stream consumption

### 5.1 Sending a message — request shape

`chat.js` (4,574 lines) builds a `FormData` payload (not JSON — this is a multipart POST, presumably to allow file blobs on the same request path in some code paths) and streams the response:

```js
// static/js/chat.js:736-784 (abridged)
const fd = new FormData();
fd.append('message', _finalMsgWithInject);
fd.append('session', streamSessionId);
if (ids.length) fd.append('attachments', JSON.stringify(ids));
if (documentModule.getCurrentDocId()) fd.append('active_doc_id', documentModule.getCurrentDocId());
...
fd.append('mode', isAgentMode ? 'agent' : 'chat');
...
fd.append('web_access', _webMode);
if (isAgentMode) fd.append('allow_web_search', 'true'); else fd.append('use_web', 'true');
...
fd.append('use_research', 'true');
...
fd.append('allow_bash', 'true');
...
fd.append('use_rag', 'false');
...
fd.append('incognito', 'true');
...
fd.append('preset_id', presetsModule.getSelectedPreset());
```

The request itself — **not `EventSource`**, but `fetch()` with a `ReadableStream` reader, so it can be a POST with a body and can be cancelled via `AbortController`:

```js
// static/js/chat.js:929-935
const _tzOffsetMin = -new Date().getTimezoneOffset();
const res = await fetch(`${API_BASE}/api/chat_stream`, {
  method: 'POST',
  body: fd,
  headers: { 'X-Tz-Offset': String(_tzOffsetMin) },
  signal: abortCtrl.signal
});
```

Non-OK responses are handled specially for a deleted session (`404` → reload session list, drop back to welcome screen) and for tool-incompatible models (heuristically detected by scanning the error text for `"tool"`/`"auto"`, then auto-switching the mode toggle from Agent to Chat).

### 5.2 The client-side streaming protocol — verbatim

The response body is consumed as raw SSE-formatted text via `getReader()`/`TextDecoder`, buffered and split on `\n`, with the last (possibly incomplete) line kept in the buffer for the next chunk:

```js
// static/js/chat.js:981-983, 1246-1263
const reader = res.body.getReader();
const decoder = new TextDecoder();
let buffer = '';
...
while (true) {
  const { done, value } = await reader.read();
  _lastReaderActivity = Date.now();
  if (done) break;

  buffer += decoder.decode(value, { stream: true });
  const lines = buffer.split('\n');
  buffer = lines.pop() || '';

  for (const line of lines) {
    // Log SSE event types (e.g. "event: error") for debugging
    if (line.startsWith('event: ')) {
      const evtType = line.slice(7).trim();
      if (evtType === 'error') _nextIsError = true;
      continue;
    }
    if (line.startsWith('data: ')) {
      const data = line.slice(6);
      ...
      if (data === '[DONE]') {
        _streamSawDone = true;
        ...
        break;
      }
      try {
        const json = JSON.parse(data);
        if (_nextIsError || json.status >= 400) {
          const errMsg = json.text || json.error?.message || `Error ${json.status || 'unknown'}`;
          ...
          break;
        }
        ...
```

This is standard **text/event-stream framing hand-parsed with string methods** rather than the browser's `EventSource` — `event: <type>` lines flip an `_nextIsError` flag for the next `data:` line, `data: <json or [DONE]>` lines carry the payload, and a blank line (implicit, from the trailing `\n\n` per SSE event) is not specially handled since the parser only cares about non-empty prefixed lines. `[DONE]` is the stream-termination sentinel (mirrors OpenAI's SSE convention).

Each parsed `data:` JSON object carries a `type` discriminator dispatched via a long `if/else if` chain (`json.type === '...'`) — the full observed vocabulary, in source order:

```
tool_start, tool_progress, tool_output, doc_stream_open, doc_stream_delta,
doc_update, doc_suggestions, ui_control, agent_step, budget_exceeded,
teacher_takeover, skill_saved, escalation_failed / skill_save_failed,
research_progress, research_sources, research_findings, research_done,
web_sources, web_search_failed, model_fallback, model_info, fallback,
attachments, rag_sources, memories_used, compacted, metrics, message_saved
```

Plain incremental text has **no `type` field** — it arrives as `{"delta": "...", "thinking": bool}` and is appended directly to the accumulator:

```js
// static/js/chat.js:1367-1385 (abridged)
if (json.delta) {
  _cancelThinkingTimer();
  _removeThinkingSpinner();
  let _delta = json.delta;
  if (json.thinking) {
    if (!_thinkOpen) { _delta = '<think>' + _delta; _thinkOpen = true; }
  } else if (_thinkOpen) {
    _delta = '</think>' + _delta; _thinkOpen = false;
  }
  const wasEmpty = !accumulated;
  accumulated += _delta;
  roundText += _delta;
  currentAccumulated = accumulated;
  ...
```

`json.thinking === true` deltas are wrapped in synthetic `<think>...</think>` tags client-side (there is no native `<think>` tag from the model — Apollo's markdown/renderer layer recognizes this tag to build the collapsible "thinking" UI). Reasoning-model output is therefore reconstructed purely from a boolean flag per delta, with explicit open/close-tag bookkeeping (`_thinkOpen`) so a multi-round agent response gets one `<think>` pair per round instead of leaking round-2+ reasoning into the visible answer.

### 5.3 Background / multi-session streaming

If the user navigates away from the session that's streaming, the loop detects it (`sessionModule.getCurrentSessionId() !== streamSessionId`) and switches to updating an in-memory `_backgroundStreams` Map instead of the DOM, later reconciled via `chatStream.js`'s `notifyStreamComplete()` (native `Notification` API, only fires when `document.hidden` or viewing another session) and `insertStreamDoneToast()` (an in-chat clickable toast in the *other* session that jumps back via `sessionModule.selectSession(sessionId)`).

### 5.4 `chatStream.js` — the `ui_control` event handler

Separated out of `chat.js` for size, this module owns AI-driven UI manipulation events (`type === 'ui_control'`, dispatched into `handleUIControl(uiData)`), covering: `toggle` (flips web/bash/rag/research/incognito checkboxes + persists via `Storage.KEYS.TOGGLES`), `set_mode` (chat↔agent), `switch_model` (updates the model-name display), `set_theme`/`create_theme` (delegates to `themeModule.applyColors`/`.save`/`.saveCustomTheme`), `highlight`/`clear_highlight` (adds `.apollo-highlight` + a floating label to a CSS-selector target, used for guided UI tours the agent can trigger), `research_started`, and `open_panel` (dynamic `import()` of the target feature module, e.g. `import('./browserPanel.js').then(mod => mod.open())`). This lets the backend agent drive the frontend UI as a tool-call side effect during a stream.

### 5.5 Rendering — markdown, code, math, mermaid

`chatRenderer.js` (2,105 lines) renders the accumulated message body via `markdownModule`'s pass (see §3), then re-highlights any un-highlighted `<pre><code>` blocks:

```js
// static/js/chat.js:1792, 2037 (post-render highlight, same pattern in chatRenderer.js)
box.querySelectorAll('pre code:not(.hljs)').forEach(b => window.hljs.highlightElement(b));
```

`modelMeta.js` was deliberately split out of `chatRenderer.js` as a **zero-import, zero-DOM-access module** purely so its pricing/model-metadata tables could be unit-tested under plain Node (importing the full UI chain crashes outside a browser because it touches `HTMLInputElement` at import time via `theme.js` → `colorPicker.js`):

```js
// static/js/modelMeta.js:1-12
// Pure model-metadata + text helpers extracted from chatRenderer.js so they
// can be unit-tested under Node (chatRenderer.js itself imports the whole UI
// chain — ui.js → theme.js → colorPicker.js — which touches HTMLInputElement
// at import time and crashes outside a browser). This module has ZERO imports
// and no DOM/window access at module scope, mirroring vad.js / graphLayout.js.
//
// chatRenderer.js imports and re-exports every name here, so its public API is
// unchanged.
export const MODEL_INFO = {
  'claude-sonnet-4-5':    { input: 3.00,  output: 15.00, ctx: 200000 },
  ...
```

`MODEL_INFO` is a large hand-maintained table of `{input, output, ctx}` (USD per 1M tokens, context window) keyed by model id, covering Anthropic, OpenAI, DeepSeek, Google, Mistral, etc. — used to compute/display estimated cost in the chat UI.

---

## 6. Model picker

There are two related-but-distinct model UIs. `static/js/models.js` renders the sidebar's `models-section` — a browsable list of every endpoint's models with favorite-star/drag-reorder support, fetched from `/api/models` and cached client-side with a TTL, where each row's `+ Chat`/`+ Image`/`Offline` button or the row itself calls `_startChat(url, mid, endpointId)` → `sessionModule.createDirectChat(...)`. The actual **chatbox model-selector dropdown** — the "model picker" a user opens from the composer to switch models mid-conversation — is a separate, dedicated module: `static/js/modelPicker.js` (707 lines), explicitly extracted out of `sessions.js`:

```js
// static/js/modelPicker.js:1-19
// Model Picker — chatbox model selector dropdown
// Extracted from sessions.js

import { providerLogo } from './providers.js';
import uiModule from './ui.js';
import settingsModule from './settings.js';
import { sortModelObjects } from './modelSort.js';

const API_BASE = window.location.origin;

// ── Recent + Favorites persistence ──
// Recent is auto-tracked (last 5 picks, most-recent-first) and lives in its
// own key. Favorites is the SAME key the sidebar Models section uses, so a
// favorite toggled here shows up there and vice-versa.
const RECENT_KEY = 'apollo-model-recent';
const FAVORITES_KEY = 'apollo-model-favorites';
const RECENT_MAX = 5;
const BROWSE_ALL_LIMIT = 12;
```

Its public API is intentionally tiny — `initModelPicker(deps)` (wires the dropdown once) and `updateModelPicker()` (re-syncs the composer's model-name label, called after any model change from *either* UI). The candidate list is built by `_getAllModels()`, which reads from `window.modelsModule.getCachedItems()` — i.e. it reuses `models.js`'s already-fetched/cached endpoint data rather than issuing its own `/api/models` call — dedupes by model id across endpoints, and filters out non-chat-capable models via `window.modelsModule.isChatCapable(item, mid)`:

```js
// static/js/modelPicker.js:177-215 (abridged)
function _getAllModels() {
  const items = (window.modelsModule && window.modelsModule.getCachedItems) ? window.modelsModule.getCachedItems() : [];
  const result = [];
  const seen = new Set();
  items.forEach(item => {
    if (item.offline) return;
    const allModels = (item.models || []).concat(item.models_extra || []);
    ...
    allModels.forEach((mid, i) => {
      if (seen.has(mid)) return;
      const _icc = window.modelsModule && typeof window.modelsModule.isChatCapable === 'function'
        ? window.modelsModule.isChatCapable(item, mid)
        : true;
      if (!_icc) return;
      seen.add(mid);
      result.push({ mid, display: ..., url: item.url, endpointId: item.endpoint_id, ... });
    });
  });
  return sortModelObjects(result);
}
```

**Selecting a model** (`_pick(m)`, `modelPicker.js:470-513`) is the concrete state-transition: it fires a `CustomEvent('apollo:model-picked', { detail: m })` for any listener that cares, closes the dropdown, then does one of three things depending on whether a session already exists — stash a pending choice (`_deps.setPendingChat(...)`) if there's no current session and no pending chat yet, create a brand-new direct chat (`_deps.createDirectChat(...)`), or `PATCH` the live session's model:

```js
// static/js/modelPicker.js:470-513 (abridged)
try { document.dispatchEvent(new CustomEvent('apollo:model-picked', { detail: m })); } catch {}
...
_close();
if (!currentSessionId && _pendingChat) {
  _deps.setPendingChat({ url: m.url, modelId: m.mid, endpointId: m.endpointId });
  updateModelPicker();
  uiModule.showToast(`Using ${m.display}`);
  return;
} else if (!currentSessionId) {
  await _deps.createDirectChat(m.url, m.mid, m.endpointId);
} else {
  const fd = new FormData();
  fd.append('model', m.mid);
  fd.append('endpoint_url', m.url);
  if (m.endpointId) fd.append('endpoint_id', m.endpointId);
  const res = await fetch(`${API_BASE}/api/session/${currentSessionId}`, { method: 'PATCH', body: fd });
  if (!res.ok) { uiModule.showError('Failed to set model'); return; }
  const sessions = _deps.getSessions();
  const s = sessions.find(x => x.id === currentSessionId);
  if (s) { s.model = m.mid; s.endpoint_url = m.url; }
}
updateModelPicker();
uiModule.showToast(`Using ${m.display}`);
```

The picked model is **not** re-sent on every `/api/chat_stream` FormData payload (§5.1's field list has no `model` key) — it's persisted server-side on the session record via this `PATCH /api/session/{id}` (or baked in at `createDirectChat` time), and `chat.js` reads it back for display purposes via `sessionModule.getCurrentModel()`. `modelPicker.js` receives its cross-module dependencies (`setPendingChat`, `createDirectChat`, `getSessions`, …) through the same `deps`-object injection pattern used by `settingsAiExtras.js` (§7.2) rather than importing `sessions.js` directly — avoiding a `sessions.js` ⇄ `modelPicker.js` import cycle. `app.js` also maintains a `_defaultChat` cache (`_refreshDefaultChat()`, hitting `/api/default-chat`) so "New chat" without an explicit model pick still resolves to the user's configured default endpoint/model, refreshed on every new-chat action rather than cached once at page load (so a settings change to the default model takes effect immediately).

---

## 7. Settings modal architecture

### 7.1 Tab structure

`settings.js` owns the `#settings-modal` and its tabs, toggled via `[data-settings-tab]` buttons whose `data-settings-tab` value maps to a `[data-settings-panel]` content div:

```js
// static/js/settings.js:34-58
const ADMIN_TABS = new Set(['services', 'integrations', 'tools', 'users', 'system']);

function initTabs() {
  modalEl.querySelectorAll('[data-settings-tab]').forEach(btn => {
    btn.addEventListener('click', () => {
      const tab = btn.dataset.settingsTab;
      if (ADMIN_TABS.has(tab) && tab !== 'integrations' && window._isAdmin && window.adminModule && typeof window.adminModule.open === 'function') {
        window.adminModule.open(tab);
        return;
      }
      modalEl.querySelectorAll('[data-settings-tab]').forEach(b => b.classList.toggle('active', b.dataset.settingsTab === tab));
      modalEl.querySelectorAll('[data-settings-panel]').forEach(p => p.classList.toggle('hidden', p.dataset.settingsPanel !== tab));
      document.body.classList.toggle('settings-appearance-open', tab === 'appearance');
      syncAppearanceOpacity(tab === 'appearance');
      if (tab === 'ai') { refreshAiModelEndpoints(); refreshLocalModels(); }
    });
  });
}
```

Admin-gated tab ids (`services`, `integrations` [partially — see below], `tools`, `users`, `system`) delegate entirely to `adminModule.open(tab)` instead of showing a `settings-modal` panel — the Settings modal is user-preferences-only; the wider admin console is a separate surface `admin.js` renders. `integrations` is a hybrid: the tab always opens locally (Agent Workbench + personal integrations are for every user), but API-service *management* inside that panel is gated internally. `window._isAdmin` is populated once from `/api/auth/status`'s `is_admin` field (see §7.3 and doc 06).

### 7.2 `settingsAiExtras.js` — how it extends `settings.js`

Rather than growing `settings.js` further (the repo enforces a module-size ratchet via `scripts/check_module_sizes.py`), AI-settings-adjacent functionality that doesn't fit the size budget was extracted into `settingsAiExtras.js` and wired back in via **named imports plus dependency injection** (not a plugin/registration API — `settingsAiExtras.js` cannot see `settings.js`'s private module-scope helpers, so `settings.js` passes them explicitly as a `deps` object to avoid a circular import):

```js
// static/js/settingsAiExtras.js:1-6
// static/js/settingsAiExtras.js
//
// Settings → AI additions kept out of settings.js for the module-size
// ratchet (scripts/check_module_sizes.py): the llama-server binary field on
// the Local Models card, and the Fast Lane (mixture routing) model role.
// settings.js-private helpers are injected via a deps object.
```

```js
// static/js/settings.js:12
import { refreshLlamaBinary, wireLlamaBinaryField, initLightModel, initModelHub, stopGgufPolling } from './settingsAiExtras.js';
```

`initLightModel(deps)` is a representative example of the injection pattern — it receives `deps.el`, `deps.fetchModelEndpoints`, `deps.fillEndpointSelect`, `deps.fillModelSelect` from `settings.js` rather than importing them:

```js
// static/js/settingsAiExtras.js (initLightModel, abridged)
export async function initLightModel(deps) {
  var el = deps.el;
  var toggle = el('set-mixtureRoutingToggle');
  var epSel = el('set-lightEpSelect');
  var modelSel = el('set-lightModelSelect');
  ...
  try {
    _endpoints = await deps.fetchModelEndpoints();
    deps.fillEndpointSelect(epSel, _endpoints, epSel.value, true);
  } catch (e) { console.warn('Failed to load endpoints for fast lane', e); }
  ...
  var res = await fetch('/api/auth/settings', { credentials: 'same-origin' });
  var settings = await res.json();
  if (toggle) toggle.checked = !!settings.mixture_routing_enabled;
  ...
```

`refreshLlamaBinary`/`wireLlamaBinaryField` render and save the local `llama-server` binary path (`GET`/`PUT /api/local-models/binary`) for the Local Models card. This same deps-object pattern recurs for `ecosystemHub.js` (`initEcosystemHub`) and `systemStatusCard.js`/`systemStatusActions.js` — Apollo's general strategy for splitting an oversized module without a plugin framework: extract pure functions, pass in whatever DOM/state accessors they need as parameters.

---

## 8. File upload flow

`fileHandler.js` (295 lines) owns picking, previewing, and uploading attachments.

**Selection**: `openPicker()` clicks a hidden `<input type="file" id="file-input">`; drag-and-drop and paste handlers (wired in `app.js`) funnel into `addFiles(files)` (capped at `MAX_FILES = 10`).

**Preview**: each pending `File` gets an object-URL preview cached in a `WeakMap` (`_previewUrls`) so the same `File` object never leaks a duplicate URL, with `_revokePreviewUrl()` called on removal. The attachment strip collapses into a single "N files" badge above `MAX_VISIBLE = 3`, capped from expanding at all above `MAX_EXPAND = 6`:

```js
// static/js/fileHandler.js:49-56
const MAX_VISIBLE = 3;
const MAX_EXPAND = 6;   // beyond this, the badge stays collapsed (too many chips to preview)
let _expanded = false;
```

**Upload** (`uploadPending()`): builds a `FormData` with one `files` entry per pending file and POSTs to `/api/upload`:

```js
// static/js/fileHandler.js:167-192 (abridged)
const fd = new FormData();
pendingFiles.forEach(f => fd.append('files', f, f.name || 'paste.png'));

try {
  const res = await fetch(`${API_BASE}/api/upload`, {
    method: 'POST',
    body: fd
  });
  if (!res.ok) {
    let detail = '';
    try { const e = await res.json(); detail = e.detail || e.error || ''; } catch (_) {}
    _showToast('Upload failed' + (detail ? ': ' + detail : ` (HTTP ${res.status})`));
    return [];
  }
  const data = await res.json();
  uploaded = (data.files || []);
  pendingFiles = [];          // clear only on success
  _lastUploadedMeta = uploaded;
  return uploaded.map(x => x.id);
} finally {
  _uploadSpinners.forEach(sp => { try { sp.stop && sp.stop(); } catch (_) {} });
  _uploadSpinners = [];
  if (strip) strip.classList.remove('attach-uploading');
  renderAttachStrip();
}
```

On failure, `pendingFiles` is deliberately **not cleared**, so the strip re-renders with the same chips for a retry — a fix for an earlier bug (referenced in-code as issue #1346) where a non-OK upload response (429 rate-limit, 413 too-large) silently dropped the attachments and the chat sent with no files attached at all, with the model never seeing them. Returned attachment `id`s are stitched into the chat request as `fd.append('attachments', JSON.stringify(ids))` (see §5.1) — the upload always happens as its own round-trip *before* `/api/chat_stream` is called, not inline with the streaming request.

---

## 9. Theming system

### 9.1 Persistence and CSS custom properties

Themes are plain JS objects of hex color strings plus optional `advanced` overrides, persisted to `localStorage['apollo-theme']` (via `Storage.KEYS.THEME`) and mirrored server-side for cross-device sync:

```js
// static/js/theme.js:471-483
export function save(name, colors, opts) {
  const obj = { name, colors };
  if (opts) {
    if (opts.font && opts.font !== DEFAULT_FONT) obj.font = opts.font;
    if (opts.density && opts.density !== DEFAULT_DENSITY) obj.density = opts.density;
    if (opts.bgPattern && opts.bgPattern !== 'none') obj.bgPattern = opts.bgPattern;
    ...
  }
  Storage.setJSON(LS_KEY, obj);
  _syncToServer(obj);
}

function _syncToServer(obj) {
  try {
    fetch('/api/prefs/theme', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
      body: JSON.stringify({ value: obj }),
    }).catch(e => console.warn('Theme sync failed:', e));
  } catch (e) { console.warn('Theme sync error:', e); }
}
```

Applying a theme sets CSS custom properties directly on `document.documentElement.style` — there is no `.light`/`.dark` class toggle; every themed rule reads a `--*` variable:

```js
// static/js/theme.js:241-266 (abridged)
export function applyColors(colors) {
  const s = document.documentElement.style;
  s.setProperty('--bg', colors.bg);
  s.setProperty('--fg', colors.fg);
  s.setProperty('--panel', colors.panel);
  s.setProperty('--border', colors.border);
  if (colors.red) s.setProperty('--red', colors.red);

  // Match native form controls, scrollbars, and date/color pickers to the
  // theme's brightness ... The theme system swaps CSS variables but never adds a
  // `.light` class, so the `:root.light` overrides ... never fired
  try {
    const bgL = hexToHSL(colors.bg)[2];
    s.colorScheme = bgL < 50 ? 'dark' : 'light';
  } catch (_e) { /* keep whatever's set if bg is unparseable */ }

  const _mtc = document.querySelector('meta[name="theme-color"]');
  if (_mtc && colors.bg) _mtc.setAttribute('content', colors.bg);

  const syn = deriveSyntaxColors(colors);
  s.setProperty('--hl-bg', syn.bg);
  ...
  const adv = colors.advanced || {};
  const defaults = computeAdvancedDefaults(colors);
  for (const { key, css } of ADV_KEYS) {
    s.setProperty(css, adv[key] || defaults[key]);
  }
  _updateFavicon(colors.red || '#e06c75');
}
```

Native form-control chrome (scrollbars, `<select>` popups, date/color pickers) is matched to the theme's *derived brightness* via the CSS `color-scheme` property, computed from the background color's HSL lightness (`bgL < 50` ⇒ dark) rather than a fixed light/dark flag — the code comment explicitly notes this was a fix for a bug where a `:root.light` class-based override was dead code because the theme system never added that class.

### 9.2 Syntax-highlight color derivation

`--hl-*` variables (used by `highlight.js`'s CSS theme) are **derived**, not stored — `deriveSyntaxColors(colors)` computes keyword/string/comment/function/number/builtin/variable/param colors from the theme's `fg`/`bg`/`red` via HSL rotation, matching the inline bootstrap script in `index.html` (§1.1) so first paint and post-boot application agree exactly.

### 9.3 First-paint bootstrap (duplicated logic)

Because `theme.js` as an ES module can't run before the browser parses and paints the initial DOM, the *exact same* color-application logic is duplicated as plain inline JS in `index.html`'s first `<script nonce>` block (§1.1) — this is a deliberate, documented duplication to avoid a flash of unstyled/wrong-themed content, not an oversight; comments in both places cross-reference the duplication.

### 9.4 Theme popup UI

`#theme-modal` / `#theme-popup` (index.html ~lines 485–650) has two tabs — **Themes** (browse built-in `#themeGrid` + user-saved `#themeUserGrid` swatches) and **Customize** (native `<input type="color">` pickers for `bg`/`fg`/`panel`/`sidebar`/`border`/`red`, an expandable "More Colors" section for `advanced` overrides — chat bubbles, sidebar, input bar, code blocks, toggles — each with a per-field reset button, plus a Color Harmony generator card). Every advanced color field has a matching `data-reset-adv="<key>"` reset button that removes the override and reapplies the computed default. Server round-trips (`GET`/`PUT /api/prefs/theme`) keep the active theme in sync across devices/sessions for a logged-in user; anonymous/desktop-mode users still get full theming purely from `localStorage`.

---

## 10. The editor build-directory naming — verified against the actual files

The task brief for this document described `static/js/editor/build/*.js` as "prebuilt artifacts" imported by `galleryEditor.js`. **This does not match what is on disk.** Inspecting the files directly:

```
static/js/editor/build/controls.js         366 lines
static/js/editor/build/popups.js           112 lines
static/js/editor/build/right-panel.js      200 lines
static/js/editor/build/toolbar.js           73 lines
static/js/editor/build/topbar.js           131 lines
static/js/editor/build/transform-popup.js  109 lines
```

None of these are minified, none carry bundler banners (no webpack/rollup/esbuild headers, no `!function(){...}` IIFE wrapping, no source-map comment), and every file has ordinary hand-written `import`/`export` statements and JSDoc comments, e.g.:

```js
// static/js/editor/build/toolbar.js:1-6
/**
 * Build the editor's left-side tool palette.
 *
 * Pure DOM construction — no module state. The big tool-switch logic
 * (cursor swap, control-section toggle, transform entry, inpaint
 * ...
```

`galleryEditor.js` imports named "build" functions from this directory alongside dozens of other `editor/*` submodules (canvas math, tools, filters, snapping) using the exact same plain ES-module `import` syntax as everywhere else in the codebase:

```js
// static/js/galleryEditor.js:57-59
import { buildToolbar as _buildToolbar } from './editor/build/toolbar.js';
import { buildTopbar as _buildTopbar } from './editor/build/topbar.js';
import { ... } from './editor/build/right-panel.js';
```

**UNCERTAIN → resolved by inspection**: the directory name `editor/build/` refers to functions that *build UI* (`buildToolbar`, `buildTopbar`, `buildRightPanel`, `buildPopups`, `buildControls`, `buildTransformPopup` — i.e. "build" as a verb, DOM-construction helpers for the image editor's chrome), not a bundler output directory. There is no separate unbuilt source for these files and no build step that generates them — they are first-class, hand-maintained ES modules like every other file in `static/js/`, organized into `editor/build/` purely as a naming/grouping convention alongside sibling folders like `editor/tools/`, `editor/fx/`, and `editor/filters/`. A recreation effort should treat every file under `static/js/`, including `editor/build/`, identically: plain source, no compilation step, load-order governed only by ES module `import` resolution.
