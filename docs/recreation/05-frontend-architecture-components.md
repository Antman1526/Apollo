# Apollo Frontend — Architecture & Components

> Scope: the browser-side UI of Apollo, a **framework-free, build-step-free** vanilla-JS application served as static files by FastAPI. There is no bundler, no transpiler, and no `node_modules` runtime dependency — the browser loads ES modules directly. This document describes the module system, the major modules, client-side state management, the tri-state web toggle, and the SSE event protocol that drives chat.

All file/line references are of the form `path:line` against the repository at `/Users/Antman/Apollo`.

---

## 1. Delivery model: static files, no build

The entire frontend lives under `static/`:

```
static/
  index.html        # app shell (Jinja-templated by FastAPI: {{CSP_NONCE}})
  app.js            # bootstrap / orchestrator (~4200 lines, no exports)
  style.css
  manifest.json, sw.js, icons, fonts, lib/
  js/               # ~90 ES modules (see `ls static/js` below)
```

`ls static/js` (top-level entries):

```
a11y.js          chatRenderer.js   document.js        memory.js        sessions.js
admin.js         chatStream.js     documentLibrary.js modalManager.js  settings.js
assistant.js     codeRunner.js     dragSort.js        modalSnap.js     signature.js
browserPanel.js  cookbook*.js      editor/            modelPicker.js   skills.js
calendar/        document*.js      emailInbox.js      modelSort.js     slashCommands.js
calendar.js      ...               emailLibrary*.js   models.js        spinner.js
censor.js        compare/          fileHandler.js     notes.js         storage.js
chat.js          color/            gallery*.js        presets.js       systemStatus*.js
                 colorPicker.js    group.js           providers.js     tasks.js
                 cookbook*.js      init.js            rag.js           theme.js
                 markdown/         research/          ...              ui.js / util/ ...
```

(Sub-bundles `calendar/`, `compare/`, `editor/`, `markdown/`, `research/`, `color/`, `util/` are folders of cooperating modules with their own `index.js`.)

The browser is pointed at the entry module by the shell. `index.html` declares a module preload and the page calls `startApolloApp()` on `DOMContentLoaded`:

- `static/index.html:217` — `<link rel="modulepreload" href="/static/app.js">`
- `static/app.js:4207` — `document.addEventListener('DOMContentLoaded', startApolloApp, { once: true })` (falls through to an immediate call if the document is already parsed).

Because there is no bundler, **import specifiers are real URLs**. `app.js` imports modules with plain relative paths (`./js/chat.js`), and a recurring footgun is documented inline at `static/app.js:33-37`: a `?v=` cache-busting query on an import string makes the browser treat it as a *different* module and load the file twice (two separate module instances with two separate private states). The rule the codebase enforces is: keep a given module's specifier identical at every import site.

---

## 2. The module system

### 2.1 Two export shapes

Apollo modules use one of two patterns, and many use both:

**(a) Default-export "module object".** A module gathers its public functions into a frozen-ish object and `export default`s it. The importer treats it like a namespace. Examples:

- `static/js/storage.js:127-141` — builds `const Storage = { KEYS, getJSON, ... getWebMode }` and `export default Storage`.
- `static/js/browserPanel.js:560-566` — `export default { init, open, close, navigate, detectLocalhost }`.
- `static/js/chatStream.js:277-284` — `export default chatStream` (object of the four named functions).
- `static/js/settings.js:4891-4894` — `const settingsModule = { open, close, ... }; export default settingsModule`.
- `static/js/compare/index.js:1468` — default object exposing `toggleMode`, `isActive`, `deactivate`, etc.

`app.js` imports these as a single binding and dispatches through it, e.g. `import browserPanelModule from './js/browserPanel.js'` then `browserPanelModule.open()` (`static/app.js:41`, `static/app.js:839`).

**(b) Named exports.** Some modules expose individual functions, especially the newer panel-style ones:

- `static/js/research/panel.js` — `export function init/isOpen/toggle/openPanel/closePanel` (`panel.js:228,237,238,254,345`). `app.js` imports it as a namespace: `import * as researchPanelModule from './js/research/panel.js'` (`static/app.js:40`) and calls `researchPanelModule.toggle()`.
- `static/js/modelPicker.js` — `export function initModelPicker(deps)` / `export function updateModelPicker()` (`modelPicker.js:103,616`).
- `static/js/storage.js` re-exports its helpers as **both** named (`export function getWebMode`) and as members of the default object, so callers can pick either style.

Both shapes coexist deliberately: the default-object form keeps call sites self-documenting (`Storage.getWebMode(...)`), and the named form supports tree-friendly direct imports and dynamic `import()`.

### 2.2 Dynamic imports for lazy panels

Panels that aren't needed at first paint are pulled in on demand via `import()`. The SSE `open_panel` handler is the clearest example — it maps a server-named panel to a lazy module load (`static/js/chatStream.js:148-191`):

```js
} else if (uiEvent === 'open_panel' || uiData.ui_event === 'open_panel') {
  var panel = uiData.panel;
  if (panel === 'browser') {
    import('./browserPanel.js').then(function(mod) {
      var fn = mod.open || (mod.default && mod.default.open);
      if (fn) fn();
    }).catch(function(){});
  } else if (panel === 'documents') {
    import('./documentLibrary.js').then(...);
  } ...
}
```

Note the defensive `mod.open || (mod.default && mod.default.open)` pattern — it tolerates either export shape, which is exactly why both shapes can safely coexist across the codebase.

### 2.3 `window.*` globals as the cross-module bus

Because there is no central store and modules are loaded independently, Apollo uses a small set of `window` globals as a deliberate, documented escape hatch for cross-cutting state and for callbacks the server needs to trigger via SSE. They fall into a few groups:

**Identity / capability flags** (set once after `/api/auth/status`):
- `window._isAdmin` — `static/app.js:1142` sets `window._isAdmin = !!d.is_admin`; consumed across admin-gated UI.
- `window._userPrivileges` — `static/app.js:1159`, drives per-user feature hiding (agent mode, bash, documents, research, image gen) at `app.js:1158-1192`.

**Server-drivable UI mutators** (exposed by `app.js`, called from SSE handlers in `chatStream.js`):
- `window._setWebMode(value, uiMode)` — `static/app.js:1626-1631`. Lets the model toggle the tri-state web mode. Invoked from the `ui_control`/`toggle` handler at `static/js/chatStream.js:42-44`.
- `window._syncRagIndicator`, `window._syncResearchIndicator`, `window._syncGroupIndicator` — `static/app.js:1820-1826`.
- `window._showToolSplash` — `static/app.js:1625`, the first-use explainer; called from the `web_sources` SSE branch (`chat.js:1763`).

**Module handles** that other files reach for without importing:
- `window.themeModule`, `window.sessionModule`, `window.uiModule`, `window.adminModule`, `window.cookbookModule` — `static/app.js:49-53`.
- `window.modelsModule` (read by the model picker to find `isChatCapable`, see §4).
- `window.documentModule`, `window.memoryModule`, `window.compareModule` — referenced guardedly elsewhere.

This is the app's "state management glue": rather than a framework's dependency-injection or context, Apollo wires modules together at bootstrap and publishes a handful of well-known globals. Every consumer guards them (`if (window.X) ...` / `typeof window.X === 'function'`) so partial loads degrade instead of throwing.

---

## 3. Bootstrap: `app.js`

`app.js` has **no exports** (`static/app.js:2-3` comment: "entry point, no exports — wires all modules together"). Its job is orchestration:

1. **Static imports** of every top-level module (`static/app.js:5-46`), including side-effecting ones that just need to run (`import './js/modalManager.js'` at line 29, `import './js/tileManager.js'` at line 31).
2. **A global `fetch` wrapper** that redirects to `/login` on any `401` (except `/api/auth/*`) — `static/app.js:56-63`. Every module's network call inherits this auth guard for free.
3. **`initializeEventListeners()`** (`static/app.js:125`) — the bulk of the file: wiring for the chat form, file/paste handling, the export dropdown, the unified Escape-to-close stack (`app.js:484-595`, closes exactly one overlay per press in a defined priority order), click-outside-to-close (`app.js:643-649`), and every tool button (Compare, Research, Browser, Cookbook, Doc Library, Gallery, Tasks, Calendar, Notes — `app.js:809-924`).
4. **URL-based panel routing** (`static/app.js:931-1062`): bookmarking `/calendar`, `/notes`, `/browser`, `/email`, `/gallery`, `/tasks`, etc. auto-opens the matching tool on load. The opener is deferred onto `window._apolloRouteOpener` and fired after sessions finish loading.
5. **`startApolloApp()`** (`static/app.js:3468`) — the actual entry: sets CSS vars, calls `initializeEventListeners()`, then `init(API_BASE)`s each module (`fileHandlerModule.init`, `modelsModule.init`, `ragModule.init`, ... `app.js:3501-3505+`). Guarded by `window.__apolloAppStarted` so a double DOMContentLoaded can't re-init.

### Toggle wiring lives here

The chat-input tool toggles (mode, web, bash, RAG, research, group) are all wired inside `initializeEventListeners`. The mode pill (`static/app.js:1656-1686`) persists `state.mode` to localStorage and slides a CSS pill. The web toggle (§5) and the `MODE_TOOLS` table that re-applies per-mode tool state (`app.js:1644-1653`, `MODE_TOOLS` at `app.js:1579`) live here too.

---

## 4. Chat & streaming

### 4.1 `chat.js` — request assembly + SSE consumer

`chat.js` (the largest UI module, ~4570 lines) owns `handleChatSubmit`. It assembles a `FormData` body from the current toggle state and streams the response. The request-building block (`static/js/chat.js:743-783`) reads toggles and translates them into form fields:

```js
const toggleState = Storage.loadToggleState();
let isAgentMode = (toggleState.mode || 'chat') === 'agent';
// Auto-escalate to agent when a document is open (AI needs edit tools)
if (!isAgentMode && documentModule?.isPanelOpen() && documentModule.getCurrentDocId())
  isAgentMode = true;
fd.append('mode', isAgentMode ? 'agent' : 'chat');

let _webMode = Storage.getWebMode(isAgentMode ? 'agent' : 'chat');     // 'off'|'auto'|'always'
if (_webMode === 'off' && el('web-toggle').checked) _webMode = 'always'; // transient override
if (_incog?.checked) _webMode = 'off';                                   // incognito wins
fd.append('web_access', _webMode);                                       // tri-state to server
if (_webMode === 'always') { /* also append legacy use_web / allow_web_search */ }
```

Key points:
- The **tri-state web mode is resolved per UI mode** (`chat` vs `agent`) through `Storage.getWebMode(...)` and sent as a single `web_access` field. Legacy boolean flags (`use_web`, `allow_web_search`) are still appended when `always`, so older server paths keep working (`chat.js:760-764`).
- A `holder` element is created for the streaming reply and stashes context for later SSE handlers: `holder._webMode = _webMode` (`chat.js:810`) is read in the `web_sources` branch to show auto-search feedback.
- Timeout is 6 min for research/agent, 3 min otherwise, enforced via an `AbortController` (`chat.js:786-801`).

### 4.2 The SSE event protocol

The server streams newline-delimited JSON events; `chat.js` parses each line and switches on `json.type`. The web-relevant and panel-relevant branches:

- **`web_sources`** (`static/js/chat.js:1744-1766`): the server performed a web search and returns the result set. The handler stores `holder._webSources`, builds a sources box (`_buildSourcesBox(json.data, 'web')`), and — crucially for the *auto* mode — surfaces that a search silently happened:
  ```js
  if (holder._webMode === 'auto' && spinner?.updateMessage) spinner.updateMessage('Searched the web');
  if (holder._webMode === 'auto' && window._showToolSplash) window._showToolSplash('web');
  ```
  This is the feedback loop that tells the user "the model decided to search" even though they never flipped the toggle to `always`.

- **`web_search_failed`** (`static/js/chat.js:1767-1778`): web was requested but returned nothing. The spinner flips to "Web search unavailable" and a toast warns "answering without live results" — the answer may be stale.

- **`research_done` / `research_findings` / `research` sources** (`chat.js:1704-1743`): deep-research lifecycle; clears the research timer, persists sources, reloads the session to show the saved report, and (if backgrounded) notifies via `notifyResearchComplete`.

- **`model_fallback` / `fallback`** (`chat.js:1779-1829`): a selected model went offline and another answered; shown as a toast and reflected in the role label so a misconfigured provider is never silently masked.

- **`model_info`** (`chat.js:1791-1812`): updates the role label with the real model name / character name as soon as it's known.

### 4.3 `chatStream.js` — `ui_control` and background-stream helpers

The AI-driven UI events and background-stream notifications were factored out of `chat.js` into `chatStream.js`. Its centerpiece is `handleUIControl(uiData)` (`static/js/chatStream.js:15-204`), which interprets a `ui_control` SSE event so the model can manipulate the UI:

- **`toggle`** (`chatStream.js:20-47`): flips a named tool toggle (`web`/`bash`/`rag`/`research`/`incognito`), syncs the checkbox + button class, persists to the `apollo-toggles` blob, and — for `web` — calls `window._setWebMode(state ? 'always' : 'off')` so the tri-state button reflects it.
- **`set_mode`** (`chatStream.js:49-62`): switches chat/agent.
- **`set_theme` / `create_theme`** (`chatStream.js:68-109`): apply + persist a theme, including animated-background / frosted-glass effects.
- **`highlight` / `clear_highlight`** (`chatStream.js:111-129`): the model can spotlight a DOM selector with a label.
- **`research_started`** (`chatStream.js:131-146`): adopts a research session into the sidebar immediately (lazy-loads `research/jobs.js`).
- **`open_panel`** (`chatStream.js:148-191`) and **`open_email_reply`** (`chatStream.js:193-200`): lazy-load and open the named panel (see §2.2).

The remaining exports — `notifyStreamComplete`, `insertStreamDoneToast`, `notifyResearchComplete` (`chatStream.js:209-275`) — handle the "a background stream you weren't looking at just finished" case: a desktop `Notification` when the tab is hidden or you're in another session, plus a clickable in-chat toast that jumps you to the finished session.

---

## 5. State management

### 5.1 `storage.js` — the only localStorage gateway

All persistence goes through `storage.js`, which centralizes key names and adds JSON parse safety. Every key is a constant in `KEYS` (`static/js/storage.js:5-27`): `THEME: 'apollo-theme'`, `TOGGLES: 'apollo-toggles'`, plus sidebar/compare/model/session keys. `getJSON`/`setJSON` swallow quota and parse errors and fall back gracefully (`storage.js:33-53`) so private-mode browsers and corrupted values never crash the app.

The **toggle blob** is the app's main piece of UI state. It is a single JSON object under `apollo-toggles`, read/written via `loadToggleState()` / `saveToggleState()` (`storage.js:91-97`) and field-accessed via `getToggle`/`setToggle` (`storage.js:99-108`). It holds `mode`, the per-tool booleans (`rag`, `research`, `group`, `bash`, ...), and the tri-state web keys (`webmode_chat`, `webmode_agent`).

### 5.2 The tri-state web toggle (off / auto / always)

Unlike the other tools (plain on/off checkboxes), web access is a **three-position toggle persisted per UI mode**. The single source of truth for resolving it is `getWebMode(uiMode)` (`static/js/storage.js:118-125`):

```js
export function getWebMode(uiMode) {
  const state = loadToggleState();
  const key = 'webmode_' + uiMode;                     // webmode_chat / webmode_agent
  if (['off', 'auto', 'always'].includes(state[key])) return state[key];
  const legacy = state['web_' + uiMode];               // migrate old boolean keys
  if (legacy !== undefined) return legacy ? 'always' : 'off';
  return 'auto';                                        // default for fresh browsers
}
```

Semantics:
- **off** — never search.
- **auto** — the *server* decides whether the query needs the web (the "decider"); the client shows after-the-fact feedback via the `web_sources` SSE branch (§4.2).
- **always** — force a pre-search every message.

The button cycles `off → auto → always → off` (`static/app.js:1599` `WEB_MODES = ['off','auto','always']`; cycle handler `app.js:1748-1767`). Persistence and visual sync are three small functions in `app.js`:

- `saveWebMode(mode, value)` writes `state['webmode_' + mode] = value` (`static/app.js:1605-1609`).
- `applyWebModeToButton(webMode)` (`static/app.js:1611-1621`) toggles `.active`/`.web-auto` classes, sets `aria-pressed`, `aria-label`/`title` to `Web search: <mode>`, and keeps the hidden `#web-toggle` checkbox in sync (for compare mode and slash commands that read the checkbox directly).
- `window._setWebMode(value, uiMode)` (`static/app.js:1626-1631`) is the server/slash-command entry point.

Because the value is keyed by `webmode_chat` vs `webmode_agent`, switching between Chat and Agent mode re-applies the correct stored web mode (`applyModeToToggles → applyWebModeToButton(loadWebMode(mode))`, `app.js:1652`). Settings also exposes a server-side default via `web-access-mode` (`static/js/settings.js:1782-1792`, `PATCH` of `web_access_mode`), and on a fresh browser `app.js` seeds the local `webmode_*` keys from that server default (`app.js:1383-1395`).

Mutual-exclusion rules are enforced where toggles interact: Group chat forces web off (`app.js:734-741`), incognito forces `web_access=off` at send time (`chat.js:757-758`), and turning web on disables research (`app.js:1762-1765`).

---

## 6. The model picker & `isChatCapable`

The chatbox model dropdown is `modelPicker.js`, initialized with a dependency bag: `initModelPicker(deps)` (`static/js/modelPicker.js:103`). It keeps two small localStorage lists — recent picks (`apollo-model-recent`, capped at 5, `modelPicker.js:15-37`) and favorites (`apollo-model-favorites`, the *same* key the sidebar Models section uses so favorites stay in sync, `modelPicker.js:16,38`).

`models.js` owns the catalog (`/api/models`, 30 s client cache, `models.js:17-19`) and the new capability filter `isChatCapable(endpointItem, modelName)` (`static/js/models.js:37-42`):

```js
export function isChatCapable(endpointItem, modelName) {
  const meta = endpointItem && endpointItem.model_meta;
  if (!meta || !meta[modelName]) return true;      // no metadata → assume chat-capable
  const kind = meta[modelName].kind;
  return kind === 'chat' || !kind;                 // exclude embedding / non-chat kinds
}
```

This filter is applied in two places so embedding/diffusion/unsupported models never reach a chat selector:
- In `models.js` itself when rendering the sidebar list (`models.js:227,239,549`), always paired with an `epModelType !== 'image'` guard.
- In `modelPicker.js`, which reaches `isChatCapable` through the global `window.modelsModule` rather than a static import (avoiding a circular dependency): `window.modelsModule && typeof window.modelsModule.isChatCapable === 'function' ? window.modelsModule.isChatCapable(item, mid) : true` (`static/js/modelPicker.js:192-195`, and the bound variants at `modelPicker.js:537-538,667-668,678-679`). The `: true` fallback means if the helper hasn't loaded yet the model is *included* rather than wrongly hidden.

`modelPicker.js` also uses the filter when auto-selecting a default model, picking the first chat-capable id rather than blindly the first entry (`modelPicker.js` tail, `models.find(m => _iccFn3(first, m)) || models[0]`).

---

## 7. Panels

Apollo's tool surfaces are modal/overlay "panels" opened from the sidebar tool buttons (wired in `app.js`) or by the server via the `open_panel` SSE event (§2.2). Three representative ones:

### 7.1 Research panel — `research/panel.js`

Named-export module (`init`, `isOpen`, `toggle`, `openPanel`, `closePanel`). The sidebar button just calls `researchPanelModule.toggle()` (`static/app.js:831-833`). `toggle()` (`panel.js:238-252`) restores a minimized overlay if one exists, otherwise opens/closes. State is a module-private `_open` boolean plus `document.body.classList` markers (`research-panel-view`) — no global store.

### 7.2 Browser panel — `browserPanel.js` (canvas screencast client)

The most involved panel: a live **canvas screencast** of the agent's server-side Chromium, with full input forwarding. It is opened via `browserPanelModule.open()` (`static/app.js:838-840`). Architecture:

- **WebSocket transport.** `open()` (`browserPanel.js:433-442`) shows the modal, binds canvas input, calls `connectWs()` (`browserPanel.js:124-166`) to `/api/browser/ws` (`wss://` under HTTPS, `browserPanel.js:109-112`), and starts a 2 s console-event poll. A stale-socket guard (`if (socket !== ws) return`) is applied in every handler so a reconnect can't have its old socket's callbacks clobber the new one.
- **Frame rendering (latest-wins).** Incoming `{type:'frame'}` messages carry base64 JPEG + device `w/h`. `drawFrame`/`paint` (`browserPanel.js:194-237`) decode into a single reused `Image`; if a frame arrives mid-decode it is held in `pendingFrame` and only the *most recent* one is drawn next (`frameBusy` gate) — this caps memory and keeps the stream real-time under load. `{type:'url'}` updates the address bar without recording history; `{type:'error'}` is shown non-fatally (`handleWsMessage`, `browserPanel.js:178-190`).
- **Input forwarding.** `bindCanvasInput` (`browserPanel.js:263-321`) attaches mouse/wheel/key listeners that translate canvas coordinates back into device pixels (`canvasCoords`, `browserPanel.js:245-255`) and `wsSend` them as `{type:'mouse'|'key'...}`. `mousemove` is throttled to ~30/s (`MOVE_THROTTLE_MS = 33`, `browserPanel.js:29,268-275`). A `CAPTURE_KEYS` set (`browserPanel.js:258-261`) prevents space/arrows/Tab from scrolling Apollo instead of the page; `Cmd`-combos pass through to the host browser; `Esc` blurs the canvas (releases keyboard focus) rather than being forwarded.
- **POST fallback.** When the WS is *not* connected, the panel drives the agent browser over `POST /api/browser/navigate` and maintains a **local** history stack (`historyStack`/`historyIndex`, `browserPanel.js:14-17,93-99`). `navigate`/`goBack`/`goForward`/`reload` (`browserPanel.js:345-397`) branch on `wsConnected`: live → send a WS message and let the *server* own history; fallback → POST and walk the local stack. `syncButtons` (`browserPanel.js:79-91`) keeps back/forward enabled when live (server owns it) and stack-driven otherwise.
- **URL safety.** `normalizeUrl` (`browserPanel.js:49-70`) blocks dangerous schemes (`BLOCKED_SCHEMES`: `javascript:`, `file:`, `data:`, `chrome:`, ...) and refuses to load Apollo's own origin inside the frame.
- **Localhost auto-detect.** A `MutationObserver` over terminal/output panes (`initLocalhostObserver`, `browserPanel.js:473-537`) scans for `localhost`/`127.0.0.1` URLs in code-runner / cookbook / agent-tool output and toasts "Dev server detected" so the user can open it in the panel.

### 7.3 Compare — `compare/`

A sub-bundle (`compare/index.js` + `state.js`, `panes.js`, `stream.js`, `vote.js`, `scoreboard.js`, `selector.js`, `probe.js`, `models.js`, `icons.js`) exposing a default object with `toggleMode`, `isActive`, `deactivate` (`compare/index.js:1468`). The Compare tool button (`app.js:809-827`) closes other exclusive tools, starts a fresh chat, then `compareModule.toggleMode()`. Compare-specific persistence (`compare-save-results`, `compare-continue-chat`, `compare-blind`, `compare-randomize`) is namespaced in `storage.js`'s `KEYS` (`storage.js:12-15`).

### How panels open (summary)

| Path | Trigger | Code |
|------|---------|------|
| Sidebar tool button | direct `module.open()/toggle()` | `app.js:809-924` |
| URL route (`/browser`, `/notes`, ...) | deferred `window._apolloRouteOpener` | `app.js:993-1062`, fired post-`loadSessions` |
| Server SSE `open_panel` | lazy `import()` + `mod.open` | `chatStream.js:148-191` |
| Escape key | one overlay per press, priority-ordered | `app.js:484-595` |

---

## 8. Component-interaction summary

```
DOMContentLoaded
   └─ startApolloApp()                       app.js:3468
        ├─ initializeEventListeners()        app.js:125   (toggles, tool btns, Esc stack, routing)
        └─ modules.init(API_BASE)            app.js:3501+

Send a message
   chat.js handleChatSubmit
     ├─ Storage.loadToggleState() / getWebMode(mode)     → web_access tri-state   storage.js:118
     ├─ FormData → POST (stream)                          chat.js:743-801
     └─ for each SSE line, switch(json.type):
            web_sources       → sources box + auto-search feedback   chat.js:1744
            web_search_failed → "no live results" toast              chat.js:1767
            model_fallback    → toast + relabel                      chat.js:1779
            ui_control        → chatStream.handleUIControl()         chatStream.js:15
                                   ├─ toggle web → window._setWebMode → applyWebModeToButton  app.js:1611
                                   └─ open_panel → import('./browserPanel.js').open()          chatStream.js:148

Globals bus:  window._isAdmin, _userPrivileges, _setWebMode,
              _syncRagIndicator, _showToolSplash, modelsModule, sessionModule, ...   app.js:49-53,1142,1626,1820
```

The throughline: **no framework, no store, no build** — coordination is done with ES-module imports, a default-export-object convention, a single localStorage gateway (`storage.js`), a handful of guarded `window.*` globals, and a line-delimited-JSON SSE protocol that lets the server reach back into the UI.
