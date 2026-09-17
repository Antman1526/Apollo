# WOW Features + Design System — Design + Plan

> REQUIRED SUB-SKILL for execution: superpowers:subagent-driven-development. Checkbox (`- [ ]`) steps. One commit per task, in order. Design tasks (1–7) must be mergeable without the feature tasks (8–14).

**Goal:** Make Apollo feel like one deliberate product instead of fifteen bolted-on panels, then surface its most distinctive capabilities (isometric agent floor, memory graph, multi-model compare + reviewer, voice call, local models, personal data) as first-class moments in the chat screen.

**Verified facts this plan relies on (2026-09-16):**

- Quality gate: `PYTHON=<main checkout>/venv/bin/python bash scripts/check.sh` → 2,076 passed / 3 skipped Python, 136 JS. Worktree has no `venv`; the main checkout at `../../..` does.
- `static/css/variables.css` has color tokens only. No radius/space/shadow/motion tokens exist. Literal `border-radius` counts across `static/css/*.css`: 6px×242, 4px×204, 8px×118, 10px×36, 3px×34, 2px×30, 12px×25, 999px×23. `!important` ×1,310. `@keyframes` ×149, `prefers-reduced-motion` ×19.
- `static/js/theme.js:18` `DEFAULT_FONT = 'sans'` (Inter). `static/css/base.css:13` falls back to `'Fira Code'` until `applyFontDensity()` runs → visible monospace flash on first paint. The early inline script in `index.html` only sets `--font-family` when a saved theme has `.font`.
- Sidebar (`index.html:742–989`): sections `sessions-section`, `email-section`, `models-section`, `tools-section`. Tools holds 11 flat `.list-item`s (Brain, Calendar, Browser, Compare, Cookbook, Paperclip[hidden], Deep Research, Gallery, Library, Notes, Tasks, Activity[hidden]). Section markup: `.section` > `.section-header-flex` > `.section-title`.
- Welcome (`index.html:1032–1051`): `#welcome-screen` is `pointer-events:none` (`layout-chat.css:70–265`). `test_welcome_setup_action.py` asserts `class="welcome-setup-action setup-trigger-link"` stays in index.html and models.js. `chatRenderer.js:1014/1025` toggles `.welcome-active` on `#chat-container`.
- Search (`static/js/search-chat.js`, 202 lines): exports `openSearch/closeSearch/isOpen/init`; DOM `#search-overlay > .search-popup > #search-input + #search-results`; fetches `/api/search?q=&limit=20`. Ctrl+K dispatch in `keyboard-shortcuts.js:147–153` calls these exports.
- Sessions: `sessions.js` exports `getSessions()`, `selectSession(id)`, `createDirectChat(url, modelId, endpointId)`, `getCurrentSessionId()`. Models: `models.js` exports `getCachedItems()`.
- Chat SSE (`static/js/chat.js`): `tool_start` `{tool, command}` (line ~1961/1997), `tool_output` `{tool, exit_code, output}` (line ~2061), `metrics` event (1 site), `model_info` event. Agent thread nodes: `.agent-thread-node` in `agent-thread.css`.
- Paperclip floor (`static/js/paperclip.js`, 1,347 lines, size-ratcheted): exports `isoProject(gx, gy)` (logical 0–100 → px on a 1200×740 stage) and `zoneForStatus`. `renderWorkspaceHTML` is bound to `#paperclip-floor` state; NOT reused.
- Memory graph: `GET /api/memory/graph` → `{nodes:[{id,label,text,category,pinned,size}], edges:[{source,target,weight}]}`; `graphLayout.js` exports pure `seedPositions(nodes,w,h,seed)`, `stepLayout(nodes,edges,opts)`.
- Voice: `#voice-call-overlay[data-state=idle|listening|capturing|transcribing|thinking|speaking]` (`index.html:2736`), `.vc-orb/.vc-orb-inner` styled in `mobile-overrides.css:271–286` with hardcoded hex. `vad.js` `createMicVad()` computes RMS from an AnalyserNode; `createCallMachine(effects)` in `voiceCall.js` with `effects.onState`.
- System status: `GET /api/system/status` → `{ok, ready_count, total, components:{storage,auth,memory,email,documents,models,search,tool_servers,terminal,background → {label, ready, state, summary, metrics, next_step, actions}}, timestamp}`. Only fetched from `settings.js:3708` today. `systemStatusCard.js` exports `renderStatePillHTML(state)`.
- Backend LLM: `src/llm_core.py` `async llm_call_async(url, model, messages, headers=None, temperature=1.0, max_tokens=0, timeout=30, tools=None) -> str`. Role resolution: `resolve_endpoint("reviewer", owner=owner) -> (url, model, headers)` as used in `routes/chat_routes.py:1457–1480`. Compare (`routes/compare_routes.py`) resolves a model's URL/key from `endpoint_a`/`endpoint_b` + `normalize_base()`.
- Routers: `def setup_<x>_routes(deps...) -> APIRouter` registered in `app.py` via `RouterSpec(...)` + `register_router_specs` (services/app_startup.py). Auth via `core/middleware.py`; read user with `require_user(request)`.
- Data endpoints for the briefing: `GET /api/email/list` (uid, from, subject, snippet, date, flag_read, flag_star), `GET /api/calendar/events` (uid, summary, dtstart, dtend, all_day), `GET /api/notes` (id, title, note_type, items, due_date, pinned), `GET /api/tasks` (id, name, next_run, status).
- CSP: new inline `<script>` in index.html needs `nonce="{{CSP_NONCE}}"`. Inline `<style>` is allowed. New JS modules ≤ 1,500 lines (`scripts/check_module_sizes.py`). No `data/` relative paths in Python (`scripts/check_runtime_paths.py`).
- JS tests: `node:test` + `node:assert/strict`, pattern in `tests/test_system_status_card.mjs` (pure import) and `tests/test_paperclip_floor_ui.mjs` (DOM shim). New test files must be appended to `package.json` `test:js`.

**Design decisions (approved 2026-09-16, "Do all"):**

- Tokens live in `variables.css`; every migrated literal becomes `var(--token)`. No theme changes, no new themes.
- Global reduced-motion guard in `base.css`; motion tokens migrated only in `layout-chat.css`, `layout-sidebar.css`, `overlays.css`, `chat-components.css`.
- Sidebar: `tools-section` stays (id preserved, retitled **Work**); a new `know-section` (**Know**) is added after it. No item ids change.
- Command palette is an extension of the existing `#search-overlay` and `search-chat.js` exports so Ctrl+K wiring is untouched; item building is a pure module with tests.
- Agent Floor in chat is a new, standalone `chatFloor.js` renderer (≤ 400 lines) reusing only `isoProject`. It renders one strip per agent-mode assistant turn above `.agent-thread`.
- Council, Briefing, and Cockpit are additive: new routes under `/api/council` and `/api/briefing`; the cockpit is frontend-only reading the existing `metrics` SSE event.
- All new UI reads only theme tokens (`--fg`, `--bg`, `--panel`, `--border`, `--red`, `--accent`, semantic colors) — no hardcoded hex.

**Tests:** Python `PYTHON=$(cd ../../.. && pwd)/venv/bin/python; $PYTHON -m pytest -q`; JS `npm run test:js`. Full gate before final commit: `PYTHON=... bash scripts/check.sh`.

---

## Task 1: Design tokens (radius, space, shadow, surface)

**Files:** Modify `static/css/variables.css`; sed-migrate `static/css/*.css`; Test `tests/test_design_tokens.py` (new).

- [ ] **Step 1: Failing test** `tests/test_design_tokens.py`:

```python
import re
from pathlib import Path

CSS_DIR = Path(__file__).resolve().parents[1] / "static" / "css"
VARS = (CSS_DIR / "variables.css").read_text()

REQUIRED = [
    "--radius-xs", "--radius-sm", "--radius-md", "--radius-lg", "--radius-xl", "--radius-pill",
    "--space-1", "--space-2", "--space-3", "--space-4", "--space-5", "--space-6",
    "--shadow-1", "--shadow-2", "--shadow-3",
    "--surface-1", "--surface-2", "--surface-3",
    "--dur-fast", "--dur", "--dur-slow", "--ease-out", "--ease-in-out",
]


def test_tokens_defined_on_root():
    for name in REQUIRED:
        assert re.search(rf"^\s*{re.escape(name)}\s*:", VARS, re.M), name


def test_single_value_radius_literals_migrated():
    # After migration, single-value 4/6/8/12/999px radii must use tokens.
    pat = re.compile(r"border-radius:\s*(4|6|8|12|999)px\s*;")
    hits = []
    for f in CSS_DIR.glob("*.css"):
        if f.name == "variables.css":
            continue
        for i, line in enumerate(f.read_text().splitlines(), 1):
            if pat.search(line):
                hits.append(f"{f.name}:{i}")
    assert len(hits) < 20, hits[:40]
```

- [ ] **Step 2: Add tokens** to `:root` in `variables.css` (after the semantic colors):

```css
  /* Shape */
  --radius-xs: 3px; --radius-sm: 4px; --radius-md: 6px; --radius-lg: 8px;
  --radius-xl: 12px; --radius-pill: 999px;
  /* Space (4pt grid) */
  --space-1: 4px; --space-2: 8px; --space-3: 12px; --space-4: 16px;
  --space-5: 24px; --space-6: 32px;
  /* Elevation */
  --shadow-1: 0 1px 2px rgba(0,0,0,.25);
  --shadow-2: 0 4px 12px rgba(0,0,0,.30);
  --shadow-3: 0 12px 32px rgba(0,0,0,.40);
  /* Surfaces: 1 = page, 2 = card, 3 = elevated (popover/composer) */
  --surface-1: var(--bg);
  --surface-2: color-mix(in srgb, var(--panel) 55%, var(--bg));
  --surface-3: color-mix(in srgb, var(--panel) 80%, var(--bg));
  /* Motion */
  --dur-fast: 120ms; --dur: 200ms; --dur-slow: 400ms;
  --ease-out: cubic-bezier(.2,.7,.2,1);
  --ease-in-out: cubic-bezier(.4,0,.2,1);
```

Light theme: in `:root.light` set `--shadow-1/2/3` with alpha .08/.12/.18.

- [ ] **Step 3: Migrate literals** with sed over `static/css/*.css` except `variables.css`, single-value only (regex anchored on `border-radius:\s*Npx;`): 4px→`var(--radius-sm)`, 6px→`var(--radius-md)`, 8px→`var(--radius-lg)`, 12px→`var(--radius-xl)`, 999px→`var(--radius-pill)`. Do NOT touch multi-value radii or `border-radius: 50%`.
- [ ] **Step 4: Run** `pytest tests/test_design_tokens.py tests/test_select_dropdown_theme_css.py tests/test_email_split_border_css.py tests/test_modal_dock_composer_clearance.py tests/test_prompt_bar_manual_resize.py tests/test_calendar_event_contrast.py -q` → green. `npm run test:js` → green.
- [ ] **Step 5: Commit** `feat(design): radius, space, shadow, surface and motion tokens; migrate single-value radii`.

## Task 2: Kill the first-paint monospace flash

**Files:** Modify `static/css/base.css:13`, `static/index.html` early theme script.

- [ ] **Step 1: Failing test** append to `tests/test_design_tokens.py`:

```python
def test_base_font_fallback_is_inter():
    base = (CSS_DIR / "base.css").read_text()
    assert re.search(r"html\s*\{[^}]*font-family:\s*var\(--font-family,\s*'Inter'", base)
```

- [ ] **Step 2:** In `base.css` change the `html` rule fallback to `var(--font-family, 'Inter', system-ui, -apple-system, 'Segoe UI', sans-serif)`. In the early inline script in `index.html`, after the `if (t && t.font)` block, add an `else` that sets `--font-family` to the same Inter stack so a saved theme without a font key also gets Inter on first paint. Keep `code, pre` on `--font-mono`.
- [ ] **Step 3:** Run the test + `npm run test:js`. Commit `fix(design): first paint uses Inter, matching the default font`.

## Task 3: Sidebar — split Tools into Work and Know

**Files:** Modify `static/index.html:879–989`; Test `tests/test_sidebar_groups.py` (new).

- [ ] **Step 1: Failing test**:

```python
from pathlib import Path
import re

HTML = (Path(__file__).resolve().parents[1] / "static" / "index.html").read_text()


def _section(section_id):
    m = re.search(rf'<div class="section[^"]*" id="{section_id}".*?(?=<div class="section[^"]*" id=|<div class="sidebar-user-bar)', HTML, re.S)
    assert m, section_id
    return m.group(0)


def test_work_and_know_sections_exist_with_expected_tools():
    work = _section("tools-section")
    know = _section("know-section")
    assert ">Work<" in work and ">Know<" in know
    for tid in ("tool-calendar-btn", "tool-notes-btn", "tool-tasks-btn", "tool-browser-btn", "tool-gallery-btn", "tool-paperclip-btn"):
        assert f'id="{tid}"' in work, tid
    for tid in ("tool-memory-btn", "tool-research-btn", "tool-library-btn", "tool-cookbook-btn", "tool-compare-btn", "tool-activity-btn"):
        assert f'id="{tid}"' in know, tid
```

Adjust the regex to the real section markup once read; the assertion intent (ids under the right heading) must hold.

- [ ] **Step 2:** Read `index.html:879–989` and `static/js/section-management.js` (verify how section collapse state is keyed; if keyed by section id, add `know-section` to any list). Retitle the `tools-section` title to **Work**, keep its id and header actions. Move Brain, Deep Research, Library, Cookbook, Compare, Activity into a new `<div class="section" id="know-section">` with title **Know**, copying the exact header markup pattern. Order inside Work: Calendar, Notes, Tasks, Browser, Gallery, Paperclip. Inside Know: Brain, Deep Research, Library, Cookbook, Compare, Activity.
- [ ] **Step 3:** Run `pytest tests/test_sidebar_groups.py tests/test_new_chat_model_preference.py tests/test_deleted_session_sidebar_regression.py tests/test_dialog_aria.py -q` and `npm run test:js`. Commit `feat(sidebar): group tools into Work and Know`.

## Task 4: Third surface level

**Files:** Modify `static/css/layout-chat.css` (composer), `static/css/overlays.css` (search popup, dropdown menus, modal panels), `static/css/layout-sidebar.css` (sidebar bg stays; user bar).

- [ ] **Step 1:** grep the selectors for the composer (`.prompt-bar` / `#prompt-bar` — verify), `.search-popup`, `.export-dropdown-menu`, `.modal-content` (verify names). Set their `background` to `var(--surface-3)` and `box-shadow: var(--shadow-2)` (modals `--shadow-3`). Set chat message cards / sidebar sections that currently use `var(--panel)` directly to `var(--surface-2)` only where they are cards (do not touch the sidebar root background).
- [ ] **Step 2:** Add to `tests/test_design_tokens.py`:

```python
def test_surface_tokens_used_by_composer_and_popovers():
    allcss = "\n".join(f.read_text() for f in CSS_DIR.glob("*.css"))
    assert allcss.count("var(--surface-3)") >= 3
    assert allcss.count("var(--surface-2)") >= 2
```

- [ ] **Step 3:** tests green; commit `feat(design): three-level surface hierarchy for composer, popovers, modals`.

## Task 5: Motion language + global reduced-motion guard

**Files:** Modify `static/css/base.css`; migrate `transition:` durations/easings in `layout-chat.css`, `layout-sidebar.css`, `overlays.css`, `chat-components.css`.

- [ ] **Step 1: Failing test** add to `tests/test_design_tokens.py`:

```python
def test_global_reduced_motion_guard():
    base = (CSS_DIR / "base.css").read_text()
    assert "@media (prefers-reduced-motion: reduce)" in base
    assert "animation-duration: .01ms !important" in base or "animation-duration: 0.01ms !important" in base


def test_motion_tokens_adopted():
    for name in ("layout-chat.css", "layout-sidebar.css", "overlays.css", "chat-components.css"):
        css = (CSS_DIR / name).read_text()
        assert "var(--dur" in css and "var(--ease-out)" in css, name
```

- [ ] **Step 2:** Append to `base.css`:

```css
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: .01ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: .01ms !important;
    scroll-behavior: auto !important;
  }
}
```

In the four files, replace `transition: <props> 0.15s|.15s|0.2s|.2s ease` → `var(--dur) var(--ease-out)`, `0.1s|.12s` → `var(--dur-fast)`, `0.3s|.3s|0.4s|.4s` → `var(--dur-slow)`. Use sed with explicit patterns; do not change `transition: none`.
- [ ] **Step 3:** tests green; commit `feat(design): motion tokens and a global reduced-motion guard`.

## Task 6: Empty state — recent sessions + suggested prompts

**Files:** Modify `static/index.html` welcome markup; new `static/js/welcomeState.js` (≤ 200 lines, pure builders + DOM mount); `static/css/layout-chat.css`; Test `tests/test_welcome_state.mjs`.

- [ ] **Step 1: Failing test** `tests/test_welcome_state.mjs`:

```js
import test from 'node:test';
import assert from 'node:assert/strict';
import { pickRecentSessions, buildSuggestedPrompts } from '../static/js/welcomeState.js';

test('pickRecentSessions returns 4 most recent non-archived by updated_at', () => {
  const s = [
    { id: 'a', name: 'A', updated_at: '2026-09-01T00:00:00Z', archived: false },
    { id: 'b', name: 'B', updated_at: '2026-09-05T00:00:00Z', archived: true },
    { id: 'c', name: 'C', updated_at: '2026-09-04T00:00:00Z', archived: false },
    { id: 'd', name: 'D', updated_at: '2026-09-03T00:00:00Z', archived: false },
    { id: 'e', name: 'E', updated_at: '2026-09-02T00:00:00Z', archived: false },
    { id: 'f', name: 'F', updated_at: '2026-08-30T00:00:00Z', archived: false },
  ];
  assert.deepEqual(pickRecentSessions(s).map(x => x.id), ['c', 'd', 'e', 'a']);
});

test('buildSuggestedPrompts uses memory categories and falls back to defaults', () => {
  const p = buildSuggestedPrompts([{ category: 'project', text: 'Working on Apollo' }]);
  assert.equal(p.length, 3);
  assert.ok(p.some(x => /Apollo/.test(x)));
  assert.equal(buildSuggestedPrompts([]).length, 3);
});
```

- [ ] **Step 2:** Implement `welcomeState.js`: `pickRecentSessions(sessions, n=4)`, `buildSuggestedPrompts(memories)` (templates: project → "Give me a status summary of <text>", goal → "What's the next step toward <text>?", preference/fact → "Draft something in my usual style about …"; fallback three generic prompts), and `mountWelcomeState({sessions, memories, onOpenSession, onUsePrompt})` that renders into `#welcome-recent` and `#welcome-prompts`. Fetch memories via `GET /api/memory?limit=20` (verify path in `routes/memory_routes.py`, else pass `[]`).
- [ ] **Step 3:** In `index.html` add inside `#welcome-screen`, after `#welcome-tip`: `<div id="welcome-recent" class="welcome-recent"></div><div id="welcome-prompts" class="welcome-prompts"></div>`. Keep the existing `welcome-setup-action setup-trigger-link` button untouched. CSS: `.welcome-recent, .welcome-prompts { pointer-events: auto; }`, chips use `--surface-2`, `--radius-pill`, `--space-2/3`.
- [ ] **Step 4:** Wire in `static/app.js` where `sessions` load completes (grep `loadSessions()` call sites): call `mountWelcomeState` with `getSessions()`, `onOpenSession: selectSession`, `onUsePrompt: (t) => { const el = document.getElementById('message'); el.value = t; el.focus(); }`. Re-mount when `.welcome-active` is re-added (hook in `chatRenderer.js:1025` via a `window.dispatchEvent(new CustomEvent('apollo:welcome'))`; listener in app.js).
- [ ] **Step 5:** Add `tests/test_welcome_state.mjs` to `package.json` `test:js`. Run `npm run test:js`, `pytest tests/test_welcome_setup_action.py -q`. Commit `feat(welcome): recent sessions and memory-derived suggested prompts`.

## Task 7: System pulse strip

**Files:** New `static/js/systemPulse.js` (≤ 150 lines); `static/index.html` (mount in sidebar above `#sidebar-user-bar`); `static/css/layout-sidebar.css`; Test `tests/test_system_pulse.mjs`.

- [ ] **Step 1: Failing test**:

```js
import test from 'node:test';
import assert from 'node:assert/strict';
import { renderPulseHTML } from '../static/js/systemPulse.js';

test('all ready collapses to one dot', () => {
  const html = renderPulseHTML({ ok: true, ready_count: 3, total: 3, components: {
    storage: { label: 'Storage', ready: true, state: 'ready', summary: '' },
    memory: { label: 'Memory', ready: true, state: 'ready', summary: '' },
    search: { label: 'Search', ready: true, state: 'ready', summary: '' } } });
  assert.match(html, /pulse-dot--ready/);
  assert.doesNotMatch(html, /pulse-chip/);
});

test('degraded components render as labelled chips', () => {
  const html = renderPulseHTML({ ok: false, ready_count: 1, total: 2, components: {
    storage: { label: 'Storage', ready: true, state: 'ready', summary: '' },
    search: { label: 'Search', ready: false, state: 'degraded', summary: 'SearXNG down' } } });
  assert.match(html, /pulse-chip--degraded/);
  assert.match(html, /Search/);
  assert.match(html, /SearXNG down/);
});

test('null status renders nothing', () => { assert.equal(renderPulseHTML(null), ''); });
```

- [ ] **Step 2:** Implement `renderPulseHTML(status)` (escape text; reuse `renderStatePillHTML` state classes naming: `pulse-chip--degraded|blocked|error|limited|unavailable|stopped|idle`) and `initSystemPulse({ mountId:'system-pulse', intervalMs:60000, onOpen })` that fetches `/api/system/status` on load, every interval while `document.visibilityState === 'visible'`, and on `visibilitychange`. Click → `onOpen()` opens Settings → **Integrations** tab, where the existing status card renders (`settingsModule.open('integrations')`; the System tab only holds backup/danger-zone).
- [ ] **Step 3:** Markup: `<div id="system-pulse" class="system-pulse" role="status" aria-live="polite"></div>` before `#sidebar-user-bar`. CSS: thin strip, chips with `--radius-pill`, colors from `--color-success/--color-warning/--color-error`, `--surface-2` bg.
- [ ] **Step 4:** Wire `initSystemPulse` in `app.js` init. Add test to `package.json`. `npm run test:js` green. Commit `feat(status): always-visible system pulse strip in the sidebar`.

## Task 8: Command palette on Ctrl+K

**Files:** New `static/js/paletteItems.js` (pure, ≤ 200 lines); modify `static/js/search-chat.js` (stays < 600 lines); `static/css/overlays.css`; Test `tests/test_palette_items.mjs`.

- [ ] **Step 1: Failing test**:

```js
import test from 'node:test';
import assert from 'node:assert/strict';
import { buildPaletteItems, fuzzyScore } from '../static/js/paletteItems.js';

const ctx = {
  actions: [{ id: 'new_session', label: 'New chat' }, { id: 'open_calendar', label: 'Open Calendar' }, { id: 'council', label: 'Ask the Council' }],
  sessions: [{ id: 's1', name: 'Rust borrow checker' }, { id: 's2', name: 'Trip to Berlin' }],
  models: [{ id: 'm1', label: 'qwen3-8b', endpoint: 'local' }, { id: 'm2', label: 'claude-sonnet-5', endpoint: 'anthropic' }],
};

test('empty query lists actions first, then sessions, then models', () => {
  const items = buildPaletteItems('', ctx);
  assert.equal(items[0].group, 'Actions');
  assert.ok(items.some(i => i.group === 'Sessions'));
  assert.ok(items.some(i => i.group === 'Models'));
});

test('query filters across groups with fuzzy match', () => {
  const items = buildPaletteItems('brl', ctx);
  assert.ok(items.some(i => i.label === 'Trip to Berlin'));
  assert.ok(!items.some(i => i.label === 'Rust borrow checker'));
});

test('fuzzyScore is 0 for non-subsequence', () => {
  assert.equal(fuzzyScore('xyz', 'calendar'), 0);
  assert.ok(fuzzyScore('cal', 'Open Calendar') > 0);
});
```

- [ ] **Step 2:** Implement `fuzzyScore(query, text)` (subsequence with bonus for word starts), `buildPaletteItems(query, {actions, sessions, models, messages})` → `[{group, id, label, hint, run}]` capped at 8 per group.
- [ ] **Step 3:** In `search-chat.js`: on open, render palette items from `buildPaletteItems` using `sessionModule.getSessions()`, `modelsModule.getCachedItems()` (chat-capable only via `isChatCapable`), and the action list: `new_session`, every `open_*` from `keyboard-shortcuts.js` defaults, `council` (Task 10), `briefing` (Task 12), `toggle_sidebar`, `settings`. Keep the existing `/api/search` results as a **Messages** group when the query length ≥ 3. Keep exports `openSearch/closeSearch/isOpen/init` unchanged. Selecting a Session → `selectSession(id)`; a Model → `createDirectChat(url, modelId, endpointId)`; an Action → dispatch the same function keyboard-shortcuts uses (import or pass via `init(apiBase, {runAction})`).
- [ ] **Step 4:** CSS: group headers (`.palette-group`), rows with `--surface-3`, kbd hints. Placeholder becomes "Search chats, jump to a tool, switch model…".
- [ ] **Step 5:** Add test to `package.json`. `npm run test:js` green. Commit `feat(palette): Ctrl+K becomes a universal command palette`.

## Task 9: Agent Floor strip in chat

**Files:** New `static/js/chatFloor.js` (≤ 400 lines); modify `static/js/chat.js` at `tool_start`/`tool_output` handlers (+ ≤ 30 lines; chat.js baseline 4584 must not grow past it — if it would, extract the hook into `static/js/chat/floorHook.js`); `static/css/agent-thread.css`; Test `tests/test_chat_floor.mjs`.

- [ ] **Step 1: Failing test**:

```js
import test from 'node:test';
import assert from 'node:assert/strict';
import { stationForTool, createFloorStrip } from '../static/js/chatFloor.js';

test('tools map to stations', () => {
  assert.equal(stationForTool('web_search'), 'web');
  assert.equal(stationForTool('bash'), 'shell');
  assert.equal(stationForTool('python'), 'shell');
  assert.equal(stationForTool('browser'), 'browser');
  assert.equal(stationForTool('manage_memory'), 'memory');
  assert.equal(stationForTool('read_file'), 'files');
  assert.equal(stationForTool('unknown_tool'), 'desk');
});

test('strip renders stations and moves the figure on tool events', () => {
  const strip = createFloorStrip();
  const html0 = strip.render();
  assert.match(html0, /chat-floor-station--web/);
  strip.onToolStart('web_search');
  assert.equal(strip.state().at, 'web');
  assert.equal(strip.state().busy, true);
  strip.onToolEnd('web_search', true);
  assert.equal(strip.state().busy, false);
  assert.deepEqual(strip.state().visited, ['web']);
});
```

- [ ] **Step 2:** Implement: stations `desk, web, shell, files, browser, memory` at logical coords on a 100×40 floor; `isoProject` imported from `paperclip.js` (verify it is a pure export; if importing paperclip.js pulls DOM at module load, copy the 6-line projection into chatFloor.js and note it). `render()` returns an SVG string: floor polygon, six station glyphs (simple iso boxes with a label), one minifig (head/torso/legs rects, theme colors), a dashed path for visited stations. `onToolStart/onToolEnd` update state; `mount(el)` re-renders with a CSS transition on the figure's `transform`.
- [ ] **Step 3:** In chat.js, when the first `tool_start` of an assistant turn arrives, create the strip once above the `.agent-thread` container (`<div class="chat-floor"></div>`), then forward `tool_start`/`tool_output` to it. Respect `_isBg`. Add a Settings → Appearance toggle `show-agent-floor` (default on) stored via the existing `data-ui-key` visibility switch pattern (`index.html:2209`).
- [ ] **Step 4:** CSS in `agent-thread.css`: `.chat-floor { height: 120px; }` responsive, figure transition `var(--dur-slow) var(--ease-out)`, reduced-motion respected by the global guard.
- [ ] **Step 5:** Add test to `package.json`. `npm run test:js`; `scripts/check_module_sizes.py` passes. Commit `feat(agent): isometric tool floor strip for agent runs`.

## Task 10: The Council (multi-model answer + reviewer synthesis)

**Files:** New `routes/council_routes.py`, `services/council.py`; register in `app.py`; new `static/js/council.js` (≤ 300 lines); slash command `/council`; Tests `tests/test_council_service.py`, `tests/test_council_routes.py`.

- [ ] **Step 1: Failing service test**:

```python
import asyncio
from services.council import run_council


def test_run_council_collects_answers_and_synthesis():
    calls = []

    async def fake_call(url, model, messages, **kw):
        calls.append(model)
        if model == "reviewer-model":
            return "CONSENSUS: 2 of 3 agree the answer is 4.\nDISAGREEMENTS: model-c said 5."
        return f"answer from {model}"

    members = [
        {"model": "model-a", "url": "http://a", "headers": {}},
        {"model": "model-b", "url": "http://b", "headers": {}},
        {"model": "model-c", "url": "http://c", "headers": {}},
    ]
    reviewer = {"model": "reviewer-model", "url": "http://r", "headers": {}}
    out = asyncio.run(run_council("2+2?", members, reviewer, call=fake_call))
    assert [a["model"] for a in out["answers"]] == ["model-a", "model-b", "model-c"]
    assert out["answers"][0]["text"] == "answer from model-a"
    assert "CONSENSUS" in out["synthesis"]["text"]
    assert out["synthesis"]["model"] == "reviewer-model"


def test_run_council_tolerates_one_failure():
    async def fake_call(url, model, messages, **kw):
        if model == "model-b":
            raise RuntimeError("boom")
        return "ok"
    members = [{"model": m, "url": "u", "headers": {}} for m in ("model-a", "model-b", "model-c")]
    reviewer = {"model": "rev", "url": "u", "headers": {}}
    out = asyncio.run(run_council("q", members, reviewer, call=fake_call))
    b = next(a for a in out["answers"] if a["model"] == "model-b")
    assert b["error"] and b["text"] == ""
```

- [ ] **Step 2:** Implement `services/council.py`: `async run_council(question, members, reviewer, *, call=llm_call_async, timeout=90)` → gather answers concurrently (`asyncio.gather(..., return_exceptions=True)`), then build a synthesis prompt listing answers labelled A/B/C asking for `CONSENSUS:` / `DISAGREEMENTS:` / `RECOMMENDED ANSWER:` sections, call the reviewer at temperature 0.2. Return `{"question", "answers":[{model,text,error}], "synthesis":{model,text}}`.
- [ ] **Step 3:** `routes/council_routes.py`: `setup_council_routes(session_manager) -> APIRouter(prefix="/api/council")`; `POST /ask` body `{question: str, members: [{model, endpoint_url}] (2–4)}`. Resolve each member's url/headers exactly the way `compare_routes.py` resolves `endpoint_a` (reuse its helper; extract to `routes/compare_helpers.py` if it is inline). Reviewer via `resolve_endpoint("reviewer", owner=owner)`. Register with `RouterSpec("Council", ...)` next to Compare in `app.py`. Route test with FastAPI `TestClient` and monkeypatched `run_council` asserting 400 on <2 members and 200 shape.
- [ ] **Step 4:** Frontend `council.js`: `askCouncil(question, members)` → renders an assistant-style message block `.council-block` with three collapsible answer cards and a highlighted synthesis card (sections parsed by the `CONSENSUS:/DISAGREEMENTS:/RECOMMENDED ANSWER:` labels). Members = the current model + up to two more chat-capable models from `getCachedItems()` (prefer different endpoints). Entry points: slash command `/council <question>` (register in `slashCommands.js` following an existing simple command; slashCommands.js baseline 5940 must not grow — put the handler in `council.js` and add only the registration line) and palette action `council` (prompts for the question via the existing styled-prompt dialog).
- [ ] **Step 5:** `pytest tests/test_council_service.py tests/test_council_routes.py -q`, `npm run test:js`, `check_module_sizes.py`. Commit `feat(council): ask three models, let the reviewer synthesize`.

## Task 11: Hardware cockpit gauge

**Files:** New `static/js/cockpit.js` (≤ 200 lines); modify chat.js `metrics` and `model_info` handlers (≤ 10 lines, or via `chat/cockpitHook.js`); `static/index.html` composer; `static/css/layout-chat.css`; Test `tests/test_cockpit.mjs`.

- [ ] **Step 1:** Read the `metrics` and `model_info` event emit sites in `src/chat_handler.py` / `src/chat_processor.py` (grep `"type": "metrics"`) and list the exact fields (expected: `tokens_per_second`, `total_tokens`, `prompt_tokens`, `completion_tokens`, `context_length`/`context_window` if present). Record them in the test.
- [ ] **Step 2: Failing test**:

```js
import test from 'node:test';
import assert from 'node:assert/strict';
import { reduceCockpit, renderCockpitHTML } from '../static/js/cockpit.js';

test('reduceCockpit folds metrics and model_info', () => {
  let s = reduceCockpit(undefined, { type: 'model_info', model: 'qwen3-8b', context_length: 32768, local: true });
  s = reduceCockpit(s, { type: 'metrics', tokens_per_second: 41.2, prompt_tokens: 1200, completion_tokens: 300 });
  assert.equal(s.model, 'qwen3-8b');
  assert.equal(s.tps, 41.2);
  assert.equal(s.used, 1500);
  assert.equal(s.window, 32768);
});

test('renderCockpitHTML hides unknown fields', () => {
  const html = renderCockpitHTML({ model: 'x', tps: null, used: null, window: null });
  assert.doesNotMatch(html, /tok\/s/);
  const html2 = renderCockpitHTML({ model: 'x', tps: 12.5, used: 800, window: 8192 });
  assert.match(html2, /12\.5 tok\/s/);
  assert.match(html2, /cockpit-fill/);
});
```

Adjust field names to what Step 1 found.

- [ ] **Step 3:** Implement reducer + renderer (context fill bar with `--color-success` → `--color-warning` past 75% → `--color-error` past 90%; tok/s text; model name). Mount `<div id="cockpit" class="cockpit"></div>` in the composer's meta row next to the model picker (verify element). Show only when a `model_info` or `metrics` event has arrived for the current session; clear on session switch.
- [ ] **Step 4:** Add test to `package.json`; `npm run test:js`. Commit `feat(cockpit): live tokens/sec and context fill gauge in the composer`.

## Task 12: Today briefing

**Files:** New `services/briefing.py`, `routes/briefing_routes.py`; register in `app.py`; new `static/js/briefing.js` (≤ 250 lines); welcome card + palette action; Tests `tests/test_briefing_service.py`, `tests/test_briefing_routes.py`, `tests/test_briefing_render.mjs`.

- [ ] **Step 1: Failing service test**:

```python
import asyncio
from datetime import datetime, timezone
from services.briefing import compose_briefing

NOW = datetime(2026, 9, 16, 9, 0, tzinfo=timezone.utc)


def test_compose_briefing_selects_today_and_unread():
    emails = [
        {"uid": "1", "from": "a@x", "subject": "Urgent", "snippet": "…", "date": "2026-09-16T07:00:00+00:00", "flag_read": False, "flag_star": True},
        {"uid": "2", "from": "b@x", "subject": "Old", "snippet": "…", "date": "2026-09-10T07:00:00+00:00", "flag_read": True, "flag_star": False},
    ]
    events = [
        {"uid": "e1", "summary": "Standup", "dtstart": "2026-09-16T10:00:00+00:00", "dtend": "2026-09-16T10:15:00+00:00", "all_day": False},
        {"uid": "e2", "summary": "Tomorrow", "dtstart": "2026-09-17T10:00:00+00:00", "dtend": "2026-09-17T11:00:00+00:00", "all_day": False},
    ]
    notes = [
        {"id": "n1", "title": "Ship v1", "note_type": "checklist", "items": [{"text": "Write changelog", "done": False}], "due_date": "2026-09-16", "pinned": True},
        {"id": "n2", "title": "Someday", "note_type": "note", "items": [], "due_date": None, "pinned": False},
    ]
    tasks = [{"id": "t1", "name": "Nightly backup", "next_run": "2026-09-16T23:00:00+00:00", "status": "active"}]
    b = compose_briefing(now=NOW, emails=emails, events=events, notes=notes, tasks=tasks)
    assert [e["uid"] for e in b["emails"]] == ["1"]
    assert [e["uid"] for e in b["events"]] == ["e1"]
    assert [n["id"] for n in b["notes"]] == ["n1"]
    assert [t["id"] for t in b["tasks"]] == ["t1"]
    assert b["date"] == "2026-09-16"
```

- [ ] **Step 2:** Implement pure `compose_briefing(now, emails, events, notes, tasks)` (unread or starred emails from the last 48h, max 5; events whose dtstart falls on `now.date()` in local tz; notes pinned or due today; tasks with next_run today) and `async summarize_briefing(briefing, call, endpoint)` producing 3–5 sentences via the utility/reviewer role (`resolve_endpoint("reviewer")`; skip with `summary=None` when no endpoint).
- [ ] **Step 3:** `routes/briefing_routes.py`: `setup_briefing_routes(deps...) -> APIRouter(prefix="/api/briefing")`, `GET /today?summary=1`. Pull data through the same service objects the email/calendar/note/task routes use (read those route files for the managers they take; inject the same via `RouterSpec`). Every source is optional: a failing source yields `[]` and a `warnings` entry, never a 500. Route test with monkeypatched sources.
- [ ] **Step 4:** Frontend `briefing.js`: `renderBriefingHTML(b)` (test in `.mjs`: renders sections only when non-empty, escapes text) and `mountBriefingCard()` into `#welcome-briefing` (add to welcome markup after `#welcome-prompts`), fetched lazily on welcome show, collapsed by default with a "Today" header, "Read aloud" button → `POST /api/tts` (verify request shape in `routes/tts_routes.py`) and play the returned audio. Palette action `briefing` opens/expands it.
- [ ] **Step 5:** Tests green; add `.mjs` to `package.json`. Commit `feat(briefing): Today card composed from email, calendar, notes and tasks`.

## Task 13: Voice orb — theme tokens, live level ring, speaking bars

**Files:** Modify `static/js/vad.js` (add `onLevel(rms)` callback to `createMicVad`), `static/js/voiceCall.js` (forward level to the overlay as `--vc-level`), `static/index.html` voice overlay (add `.vc-bars` with 5 spans), move `.vc-*` rules from `mobile-overrides.css` into new `static/css/voice.css` (include in index.html after overlays.css) using tokens; Test extend `tests/test_voice_vad.mjs`.

- [ ] **Step 1: Failing test** in `tests/test_voice_vad.mjs` (read the file's existing pattern first):

```js
test('createVadGate forwards level via onLevel when provided', () => {
  const levels = [];
  const gate = createVadGate({ threshold: 0.02, onLevel: (v) => levels.push(v) });
  gate.push(0.5, 0);
  gate.push(0.01, 100);
  assert.deepEqual(levels, [0.5, 0.01]);
});
```

(If `createVadGate` does not take options this way, adapt to its real signature; the intent is a level tap that does not change event semantics.)

- [ ] **Step 2:** In `voiceCall.js`, set `overlay.style.setProperty('--vc-level', clamp(rms*8, 0, 1))` at most every animation frame. CSS: `.vc-orb { box-shadow: 0 0 0 calc(4px + 18px * var(--vc-level, 0)) color-mix(in srgb, var(--accent, var(--red)) 25%, transparent); }`; colors per state from tokens: listening `--accent`, thinking `--hl-keyword`, speaking `--color-success`, capturing `--color-warning`. `.vc-bars` animate only in `data-state="speaking"` (5 bars, staggered `vc-bar` keyframes, `var(--dur-slow)`). Keep the existing caption/transcript.
- [ ] **Step 3:** `npm run test:js` green. Commit `feat(voice): token-colored orb with live level ring and speaking bars`.

## Task 14: Memory constellation on the welcome screen

**Files:** New `static/js/welcomeConstellation.js` (≤ 250 lines) reusing `graphLayout.js`; welcome markup (`<svg id="welcome-constellation" class="welcome-constellation" aria-hidden="true"></svg>` as the first child of `#welcome-screen`); CSS in `layout-chat.css`; Test `tests/test_welcome_constellation.mjs`.

- [ ] **Step 1: Failing test**:

```js
import test from 'node:test';
import assert from 'node:assert/strict';
import { relatedNodeIds, layoutConstellation } from '../static/js/welcomeConstellation.js';

test('relatedNodeIds matches on word overlap, case-insensitive, min 3 chars', () => {
  const nodes = [{ id: 'a', label: 'Apollo release plan' }, { id: 'b', label: 'Berlin trip' }, { id: 'c', label: 'Rust borrow checker' }];
  assert.deepEqual(relatedNodeIds('plan the apollo demo', nodes), ['a']);
  assert.deepEqual(relatedNodeIds('to', nodes), []);
});

test('layoutConstellation is deterministic and bounded', () => {
  const g = { nodes: [{ id: 'a' }, { id: 'b' }, { id: 'c' }], edges: [{ source: 'a', target: 'b' }] };
  const p1 = layoutConstellation(g, 800, 400);
  const p2 = layoutConstellation(g, 800, 400);
  assert.deepEqual(p1, p2);
  for (const n of p1.nodes) { assert.ok(n.x >= 0 && n.x <= 800 && n.y >= 0 && n.y <= 400); }
});
```

- [ ] **Step 2:** Implement `layoutConstellation(graph, w, h)` (seedPositions + 120 stepLayout ticks, clamp), `relatedNodeIds(text, nodes)`, `renderConstellationSVG(layout, relatedIds)` (edges at 12% opacity, nodes r=3 at 35% opacity, related nodes r=5 at 100% with accent color and a soft glow), and `mountConstellation({svgId, inputId})` that fetches `/api/memory/graph` once when the welcome screen shows, caps to 120 nodes, re-renders related nodes on input `input` events (debounced 120ms). Hide entirely when zero nodes.
- [ ] **Step 3:** CSS: absolutely positioned behind the welcome content, `pointer-events:none`, fades in with `var(--dur-slow)`; respects the reduced-motion guard. Wire in `app.js` on the `apollo:welcome` event from Task 6.
- [ ] **Step 4:** Add test to `package.json`; `npm run test:js`. Commit `feat(welcome): live memory constellation behind the empty state`.

## Task 15: Final gate + visual check

- [ ] `PYTHON=$(cd ../../.. && pwd)/venv/bin/python bash scripts/check.sh` → all green (Python ≥ 2,076 passed, JS ≥ 136 + new tests, module sizes, runtime paths).
- [ ] Launch the app with an isolated `APOLLO_DATA_DIR`, screenshot: welcome screen (constellation, recent, prompts, Today card), sidebar (Work/Know, pulse strip), Ctrl+K palette, an agent run with the floor strip, the voice overlay. Fix visual regressions found; re-run the gate.
- [ ] Update `README.md` "What it actually does" with one line each for Council, Today briefing, Cockpit, Agent Floor, Command palette. Commit `docs: describe the new chat-screen features`.

---

## Execution notes (2026-09-17)

Deviations recorded during implementation and review, all deliberate:

- Task 2: the default font was already Inter; the fix was the first-paint fallback in `base.css` and the inline theme script (including the legacy `font: 'sans'` map).
- Task 4: surfaces step tonally toward `--panel`; because presets differ in whether panel is lighter or darker than bg, elevation is carried by `--shadow-*` and borders. Shadow strength follows bg lightness in `applyColors` since the theme system never sets `:root.light`.
- Task 5: every raw duration in `transition` declarations across the four files is migrated (0.26s/0.45s rounded to tokens); the test scans mid-line declarations.
- Task 7: the pulse strip always initializes; real non-admins get a 401/403 and the strip hides itself. The no-login desktop mode reports `is_admin=false` yet can read the status endpoint.
- Task 9: `isoProject` is copied (paperclip.js touches the DOM at import); stage 1200×360 with `sx: 6, sy: 2.6`, one strip per turn (adopted across narration bubbles), turn-end only from the non-background branches.
- Task 10: `resolve_ad_hoc_endpoint` is shared with Compare and now owner-scoped + enabled-only; client endpoint URLs pass `check_outbound_url`; total request capped at 240s.
- Task 11: fill uses the backend's `context_percent` (input tokens accumulate across agent rounds) with bands 70/85 to match the footer ring; `compacted` is reduced but not wired (chat.js is at its size ratchet).
- Task 12: no injectable managers exist; the route reimplements each list route's owner scoping, with a 60s server cache that is skipped when any source failed.
- Task 14: the svg lives in `#chat-container` (not inside the shrink-wrapped welcome block), with a radial mask, stopword/word-boundary matching capped at 12, and an Appearance toggle.
- Task 15 visual pass: the welcome block outgrew its 40%/30vh budget on windows under ~900px tall; height-responsive rules lower the composer float and trim the block. Service-worker registration fails inside the desktop app's browser pane (environmental; `sw.js` serves correctly). The Council, floor strip, cockpit values, and voice orb were verified by unit tests and reviewer harnesses, not against a live model, because the isolated test data directory has no configured model.
