# Apollo — AI Review Context Pack, Part 3: Design Decisions and Areas for Review

Continuation of Parts 1-2. This document covers deliberate architectural
trade-offs (with the project's own stated rationale where it exists, and
explicit "inferred, not documented" flags where it doesn't), then closes with
pointed questions for the reviewing AI.

## 1. Architecture Decision Records

`docs/adr/` contains one ADR at the time of this scan:
`2026-07-17-runtime-data-and-identity.md`. Quoted in full for context since
it directly documents a cross-cutting decision:

> **Context:** Apollo runs as a native application, in containers, and from
> source checkouts. Persisting state relative to the current working
> directory caused data to move between launch modes. Feature routes also
> needed a consistent interpretation of cookie, token, local-bypass, and
> internal-tool callers.
>
> **Decision:**
> - `src.runtime_paths.data_root()` is the canonical resolver for application
>   state. `APOLLO_DATA_DIR` takes precedence, then `DATA_DIR`; otherwise
>   Apollo uses an activated platform location or preserves an existing
>   legacy `data/` directory.
> - Authentication state uses that resolver as well. A configured data root
>   therefore contains the session and account state for the same runtime.
> - Legacy migration is copy-verify-activate. It records a receipt, verifies
>   SQLite copies, and keeps the legacy source untouched for rollback.
> - Routes resolve ownership through the shared request-identity helpers.
>
> **Consequences:** Native launchers, Docker, and E2E runners must set an
> explicit data root whenever they require isolation. Backups must cover the
> resolved data root... Operators can roll back a data-root migration by
> stopping Apollo, retaining the activated target as evidence, and restarting
> against the intact legacy source or an explicit backup root.
>
> **Verification:** Run `python scripts/check_runtime_paths.py --root .` and
> the data-migration tests before shipping a storage or identity change.

This ADR exists because a real problem was hit — the same install running
from different launch modes (double-clicked macOS bundle vs. Docker vs. `python
app.py` from a checkout) was writing state to different working-directory-
relative locations. The copy-verify-activate migration strategy (not a
destructive move) is a deliberate safety choice for a desktop app where data
loss on an upgrade is unacceptable.

Only one ADR exists for a project of this size — most of the other
architecture decisions below are reconstructed from code/README/THREAT_MODEL,
not from a dedicated decision record. That absence is itself worth flagging:
decisions like SQLite-over-server-DB, single-warm-model, and PyInstaller
packaging have no ADR, so their rationale lives only in scattered comments
and this reviewer's inference.

## 2. No-framework frontend — zero build step

`static/index.html` loads ~40+ feature modules as native ES modules directly
(Part 1 §10):

```html
<script type="module" src="/static/js/storage.js"></script>
<script type="module" src="/static/js/ui.js"></script>
...
```

`package.json` confirms there is genuinely no bundler in the toolchain — its
only `scripts` entries are `check` (shells out to `scripts/check.sh`) and
`test`/`test:js` (runs `.mjs` test files directly via `node --test`, no
Jest/Vitest/webpack/esbuild/vite):

```json
"scripts": {
  "check": "bash scripts/check.sh",
  "test": "npm run test:js",
  "test:js": "node --test tests/test_paperclip_floor_ui.mjs ... tests/test_settings_modules.mjs"
},
"devDependencies": { "@antithesishq/bombadil": "^0.3.2" },
"dependencies": { "@anthropic-ai/sdk": "^0.98.0" }
```

**Trade-off, as evidenced by the code itself:**
- *Wins:* zero build step means the app runs from a plain source checkout or
  a `file://`-served static directory with no `npm install && npm run build`
  gate; every `.js` file is directly what ships, which is favorable for an
  AI coding agent editing the app (no source-map indirection, no bundler
  cache invalidation, no "did my edit actually get compiled" uncertainty);
  matches the "local-first, operator-controlled" philosophy stated in
  README.md.
- *Costs, visible in the size data from Part 2 §10:* no code-splitting means
  `document.js` ships as one 9,453-line file loaded on every page view
  regardless of whether the document editor is used that session; no
  tree-shaking of unused code paths; no TypeScript/Babel transform pipeline,
  so the codebase is plain browser-runnable JS with `var`-heavy, pre-ES6-class
  idioms sitting next to `import`/`export` syntax (Part 1 §10) with no
  linter/formatter enforcing one style; the `check_module_sizes.py` ratchet
  (Part 2 §10) is a workaround for the lack of build-time bundling
  discipline (splitting a module normally happens naturally when a bundler
  makes it free — here it requires a manual refactor).

## 3. Single-process FastAPI monolith

Confirmed: one `uvicorn` process serves all ~49 router-factory functions
(Part 1 §2) plus static assets, SSE streams, and WebSocket endpoints (browser
automation screencast — `tests/test_browser_ws.py` implies a websocket route
exists). `docker-compose.yml` topology confirms this — `apollo` is one
`build: .` service; `searxng`, `ntfy`, `paperclip-db` (Postgres), and
`paperclip` are the only other services, and they're external tools/sidecars,
not a decomposition of Apollo itself into microservices.

**Rationale (inferred, not explicitly documented as an ADR):** a desktop-
first, single-operator app has no obvious benefit from horizontal scaling or
service decomposition — the entire point is "one process you can start and
stop, on your own machine, with your own model." The PyInstaller packaging
strategy (§6 below) is only tractable because there's one process to bundle;
a microservice split would multiply packaging complexity for no benefit in
this deployment model. This is consistent with, but not proven by, the code —
a reviewer should treat "single-process is the right call for this product"
as the project's evident stance rather than a stated ADR conclusion.

## 4. SQLite over a server database

`core/database.py:33-45`:

```python
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{data_path('app.db')}")
...
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {}
)
```

`requirements.txt` carries no `psycopg2`/`asyncpg`/`pymysql` — Postgres/MySQL
support is not wired in at all for Apollo's own data (Paperclip's sidecar
Postgres in `docker-compose.yml` is a *different* system's database, not
Apollo's). `DATABASE_URL` is overridable via env var, so the code path
technically tolerates a non-SQLite URL, but `check_same_thread=False` is a
SQLite-specific knob and nothing else in the codebase suggests any other
backend has been exercised.

**Rationale (inferred):** consistent with the local-first, single-process,
zero-external-dependency posture — SQLite means "the app's data is a file,"
which matches the git-archive packaging philosophy (§7) and the desktop
distribution model (no separate DB server to install/configure/secure). The
cost, not discussed anywhere in the docs reviewed: SQLite's single-writer
lock model is in tension with the app's own concurrency profile — background
bash/python tool tasks, a detached per-session chat stream, and an
independent cron-style task scheduler can all be writing to `app.db`
concurrently, and **no `PRAGMA journal_mode=WAL` is configured** (verified
absent by grep across the tree — Part 2 §6/§13). Default SQLite rollback-
journal mode holds a stronger lock during writes than WAL would; under real
concurrent write load this is a plausible source of `database is locked`
errors that the current error-handling style (broad `except Exception` +
`db.rollback()` + `logger.error`, seen throughout `session_manager.py`)
would silently swallow and retry-never rather than surface.

## 5. Tracked-files-only zip packaging — secrets excluded by construction

`scripts/build-windows-zip.sh` packages a native Windows install as a zip
using `git archive` specifically so that anything not tracked by git —
including `.env`, the venv, `data/`, `logs/`, and build artifacts — cannot
possibly end up in the shipped package, regardless of what's present in the
working directory that ran the build:

```bash
# scripts/build-windows-zip.sh:1-25
# build-windows-zip.sh — package the source tree for a native Windows install
# (no Docker) as Apollo-Windows.zip.
#
# Packaging rules, and why they are what they are:
#
#   * Built with `git archive`, so ONLY TRACKED files ship. Untracked secrets
#     (.env), the virtualenv, runtime data/ and logs/, and build artifacts
#     therefore cannot leak into the zip by construction rather than by a
#     hand-maintained exclude list that some future edit forgets to update.
#
#   * Everything the ref tracks is included verbatim except the two drops
#     below. This matters: an earlier hand-rolled build applied .gitignore
#     patterns WITHOUT their `!` negation rules and silently dropped tracked
#     runtime files — including static/js/editor/build/*.js, which
#     galleryEditor.js imports, so the gallery editor failed to load. The
#     REQUIRED list below is a regression guard against exactly that.
#
#   * Dropped: macOS/Linux-only launchers (useless on Windows) and docs/
#     (~19MB of demo media, not runtime code).
```

**INTENT, explicitly stated in the comment itself:** this is a "secure by
construction" choice over a hand-maintained exclude list, made *after* a
real regression where a `.gitignore`-pattern-based exclude approach both
(a) risked leaking untracked secrets by omission and (b) actually broke a
shipped feature (the gallery editor) by over-excluding tracked build output
that happened to match a gitignore pattern without its negation rule. The
`git archive` approach inverts the failure mode: it can only *under*-include
(miss something that should ship but isn't tracked, which is caught by a
`REQUIRED` regression-guard list mentioned in the comment) rather than
*over*-include (leak a secret). `SECURITY.md`'s "Publishing A Fork" section
reinforces the same philosophy for the source repo itself, prescribing
`git grep` for API-key-shaped patterns before any public push.

## 6. Packaging: PyInstaller

`packaging/apollo.spec` builds a self-contained one-dir macOS bundle:

```python
# packaging/apollo.spec:1-17
"""PyInstaller spec for a self-contained Apollo.app (onedir).

Build from the repo root:
    venv/bin/pyinstaller packaging/apollo.spec --noconfirm

Produces dist/apollo/ (onedir). build-macos-bundle.sh wraps that into
Apollo.app + Apollo.dmg.
"""
...
for pkg in ("chromadb", "onnxruntime", "fastembed", "tokenizers", "cryptography",
            "pydantic", "pydantic_core", "crawl4ai", "mcp", "caldav", "icalendar",
            "markdown", "qrcode", "pyotp", "huggingface_hub", "tqdm", "certifi"):
    try:
        d, b, h = collect_all(pkg)
        datas += d; binaries += b; hiddenimports += h
    except Exception as exc:
        print(f"[apollo.spec] collect_all({pkg!r}) skipped: {exc}")

for pkg in ("routes", "services", "core", "src", "companion", "mcp_servers", "config"):
    if os.path.isdir(os.path.join(REPO, pkg)):
        hiddenimports += collect_submodules(pkg)
```

The spec explicitly `collect_all`s native/data-heavy dependencies
(ChromaDB, ONNX runtime, fastembed, tokenizers, cryptography) that
PyInstaller's static import analysis would otherwise miss, and pulls in
every first-party package's submodules wholesale (`routes`, `services`,
`core`, `src`, ...) because the app imports them dynamically at startup
(consistent with the `build_and_include_router` dynamic-wiring pattern in
Part 1 §2) rather than through statically analyzable top-level imports.

**PyInstaller vs. Electron — not explicitly documented anywhere reviewed**
(README, ROADMAP, THREAT_MODEL, the one ADR). This is **inferred**, not
found stated: `package.json` carries no `electron`/`electron-builder`
dependency, there is no `electron/` or equivalent wrapper directory, and the
packaging directory is purely PyInstaller-based. Given the frontend is
already a plain browser-served static app (§2) reached over HTTP/SSE, an
Electron wrapper would add a Chromium runtime and a second packaging
toolchain on top of the existing "package the Python backend, open the
default browser" model (`scripts/windows_launcher.py`'s
`_open_browser_when_ready()` opens the *system* browser, not an embedded
one) for no architectural necessity — the app is already "a local web server
plus your browser." A reviewing AI should treat this as a plausible and
consistent inference from the evidence, not a confirmed project rationale;
if PyInstaller-vs-Electron tradeoffs were ever discussed, that discussion
isn't in the reviewed docs.

## 7. Single-warm-model policy (VRAM constraint)

Documented in Part 1 §6. `services/localmodels/server_manager.py`'s module
docstring states the policy outright: `"""Launch and track local llama-server
processes (single warm chat model)."""` — and `ensure_running()`'s swap logic
(`if slot: self._stop_proc(slot)` before launching a new one) enforces it in
code, not just in the docstring. **Rationale (inferred from the mechanism,
not separately documented):** running two arbitrary-size GGUF chat models
concurrently on typical consumer/prosumer GPU VRAM (the scenario the
Cookbook's hardware-fit subsystem, `services/hwfit/`, exists to reason
about) risks OOM; swapping the single chat slot avoids ever having to reason
about *which* combination of concurrently-loaded models fits a given card.
The `_embed` slot is kept independent specifically because an embedding
model is typically small and needed simultaneously with a chat model (RAG at
chat time) — that's the one case where running two local models
simultaneously is treated as safe/necessary rather than swapped.

## 8. Loopback trust model for desktop use

`THREAT_MODEL.md` states this as the app's foundational security framing,
not as an incidental detail:

> Apollo is designed for **trusted users on a private network**, not public
> exposure. The README describes it as "treat it like an admin console" —
> that framing is accurate. A logged-in admin can execute shell commands,
> read and write files, send email, and control model serving. This is
> intentional. The threat model does not try to prevent admins from doing
> these things.

The `INTERNAL_TOOL_TOKEN` mechanism (Part 1 §7, `core/middleware.py:17-56`)
is the concrete embodiment of this trust model: a random per-process token
lets the agent's own tool calls reach admin-gated internal routes over HTTP
loopback without carrying a session cookie, because the agent *is* acting on
behalf of an already-authenticated admin session (verified separately via
`tool_security.owner_is_admin_or_single_user` before any loopback call is
issued, per `THREAT_MODEL.md`'s "Internal Tool Loopback" section). This is a
coherent design for a single-operator desktop tool, but it does mean the
entire privilege model collapses to one boundary: authenticated-admin vs.
not. There is no capability-scoped agent — an agent session that's allowed
to use `web_search` is, by construction, in the same trust tier as one that
can run arbitrary `bash`, once the session is admin-owned (the RAG tool
*selection* in Part 1 §5 controls what's offered to the model per-turn, but
`disabled_tools`/settings toggles are the only enforcement layer once a tool
is offered — there is no separate "can this specific session use bash"
permission distinct from "is this user an admin").

## 9. Consent-gated winget auto-install (Windows launcher)

`launch-windows.ps1` auto-installs missing prerequisites via `winget`, but
gates every install behind an interactive confirmation with a comment
labeling this "always with consent":

```powershell
# launch-windows.ps1:53-79
# --- Missing-prerequisite auto-install (via winget, always with consent) ---

function Test-Winget {
    return [bool](Get-Command winget -ErrorAction SilentlyContinue)
}

function Confirm-Install($what) {
    $answer = Read-Host ("    Install {0} now via winget? [Y/n]" -f $what)
    return ($answer -eq "" -or $answer -match "^[Yy]")
}

function Install-WithWinget($displayName, $wingetArgs) {
    Write-Step ("Installing {0} (winget)" -f $displayName)
    & winget install @wingetArgs --accept-package-agreements --accept-source-agreements
    if ($LASTEXITCODE -ne 0) {
        Write-Host ("winget could not install {0} (exit code {1})." -f $displayName, $LASTEXITCODE) -ForegroundColor Yellow
        return $false
    }
    Update-SessionPath
    return $true
}
```

`Confirm-Install` is a blocking `Read-Host` prompt with a default-yes-on-
Enter `[Y/n]` convention (empty input or a `y`/`Y` proceeds; anything else
declines) — used at least for Python and `llama.cpp` installation during
first-run setup (the script upgrades an existing `llama.cpp` via `winget
upgrade` later at line 184 without a fresh prompt, reusing the earlier
consent). This is a genuine per-install consent gate, not a silent
`--silent`/`-y`-flagged auto-install — the `--accept-package-agreements
--accept-source-agreements` flags on the `winget install` call accept
*winget's own* package/source EULAs programmatically (standard for
non-interactive winget usage) but do not bypass the `Confirm-Install`
human-in-the-loop step that precedes the call.

## 10. Security posture summary (from SECURITY.md / THREAT_MODEL.md)

Key points, both read in full for Part 2/3 evidence:
- Deployment guidance is explicit that Apollo should sit behind a trusted
  reverse proxy/VPN (Cloudflare Access, Tailscale, or similar) if exposed
  beyond localhost, with `AUTH_ENABLED=true` and `SECURE_COOKIES=true`.
- A concrete list of ports expected to stay internal-only: Apollo `7000`,
  SearXNG `8080`, ntfy `8091`, Ollama `11434`, local model/provider APIs
  `8000-8020`.
- A specific CVE is called out: *"Do not expose an external ChromaDB server
  unless it has a released remediation for `CVE-2026-45829`"* — relevant
  given ChromaDB is embedded by default (Part 2 §5) but the warning implies
  a server-mode ChromaDB deployment is at least conceivable/configurable.
- The four "Known Gaps" in THREAT_MODEL.md (no sandbox, SSRF via a token
  base_url param claimed-fixed by PR #1039, `src/search/` partial dup, coarse
  token scopes) are the project's own admitted, open risk list — Part 2 §9
  and §13 examine the first three in code.

## Areas for Review

Pointed, specific questions for the reviewing AI — grounded in the evidence
above, not generic best-practice prompts.

1. **Session-map eviction.** `core/session_manager.py`'s `self.sessions`
   dict (Part 2 §7) has no TTL, no LRU cap, and no scheduled eviction beyond
   an apparently-manual `cleanup_empty_sessions()`. What eviction policy
   (LRU with a cap sized to typical RAM budgets? TTL since last access with
   re-hydration on demand, which the code already supports via the lazy-
   hydrate path in `get_session`?) would bound memory without breaking the
   "resume a running agent stream" feature in `src/agent_runs.py` that
   depends on session state staying live mid-turn?

2. **The hydration race.** `get_session()`'s check-then-load
   (`core/session_manager.py:340-346`) has no lock. Is a
   per-session-id `asyncio.Lock` (or a single striped lock pool) sufficient,
   or does the fix need to be transactional at the DB layer given
   `_persist_message` and `_load_session_from_db` each open independent
   `SessionLocal()` connections?

3. **Unifying the four tool registries.** Given `src/agent_tools.py:58-61`
   already names the problem in a comment, and `tests/test_local_tool_parity.py`
   already enforces one of the six possible pairwise relationships between
   four registries — is there a single-source-of-truth design (e.g. one
   `@tool(...)` decorator or one declarative tool-definition dict that
   *generates* `FUNCTION_TOOL_SCHEMAS`, `TOOL_SECTIONS`, `BUILTIN_TOOL_DESCRIPTIONS`,
   and `TOOL_TAGS` at import time) that would fit the existing per-tool
   customization needs (e.g. `get_builtin_overrides()` letting users edit
   `TOOL_SECTIONS` text at runtime, which a generated-from-single-source
   design would need to accommodate as an explicit override layer)?

4. **Sandboxing agent tools.** THREAT_MODEL.md references issue #1058 for a
   sandbox proposal. Given `read_file`/`write_file` already have a working
   path-confinement primitive (`_tool_path_roots`/`_resolve_tool_path` in
   `src/tool_execution.py`), what's the minimal-diff path to applying
   equivalent confinement to `bash`/`python` — a subprocess-level restriction
   (Linux namespaces/seccomp, macOS sandbox-exec, a container-per-call), or a
   coarser policy layer (an explicit allow/denylist of commands, or working-
   directory confinement analogous to the file-tool roots)? What's the
   Windows story, given namespace/seccomp approaches are Linux-specific and
   the app explicitly targets native Windows too?

5. **Should regex tool-call parsing move to grammar-constrained decoding?**
   `src/tool_parsing.py`'s reactive per-model-family normalization (Part 1
   §4, Part 2 §11) trades off against llama.cpp's own GBNF grammar support
   or JSON-schema-constrained sampling, which would make a local model
   *physically unable* to emit an unparseable tool call rather than parsing
   whatever it emits after the fact. Given `services/localmodels/server_manager.py`
   already controls the `llama-server` launch command line, is passing a
   `--grammar`/response-format constraint at spawn time feasible, and would
   it need per-model-family tool-schema translation of its own (trading one
   fragility for a different one)?

6. **SSE backpressure.** The agent loop yields `tool_progress` events from an
   `asyncio.Queue` (Part 1 §3.2) with no apparent bound on queue size or on
   how fast the SSE consumer must drain relative to producer speed. Under a
   slow/stalled client connection with `agent_runs`'s detached-background-
   task design (Part 1 §9) continuing regardless, does the progress queue
   have an effective backpressure mechanism, or could a very chatty
   long-running tool (e.g. a verbose `bash` build) accumulate unbounded
   memory in the queue while a client is disconnected/slow?

7. **SQLite WAL mode and concurrent-write safety.** No `PRAGMA
   journal_mode=WAL` is set (Part 2 §6, Part 3 §4) despite background tool
   tasks, detached chat streams, and a cron-style scheduler all writing to
   the same `app.db`. Is the current default rollback-journal mode causing
   observed `database is locked` errors in practice (worth checking
   `logs/`/issue tracker), and would enabling WAL (plus `busy_timeout`) be a
   low-risk, high-value fix, or does something about the desktop packaging
   (single file, no separate WAL/SHM file cleanup on abrupt process kill)
   make WAL mode a worse fit for this deployment model?

8. **Python module decomposition priorities.** `routes/email_routes.py`
   (3,259 lines) and `src/agent_loop.py` (2,331 lines, Part 1 §3) have no
   automated size ratchet unlike their JS counterparts (Part 2 §10). Which
   should be decomposed first, and along what seams — `agent_loop.py`
   already delegates to `tool_parsing`/`tool_execution`/`tool_index`/`tool_schemas`,
   suggesting the remaining 2,331 lines is mostly the SSE-round-loop
   orchestration itself (Part 1 §3.2-3.3); is there a further split there
   (e.g. extracting the document-streaming state machine, which interleaves
   with the round loop across `_doc_acc`/`_doc_opened`/`_doc_last_len`) that
   wouldn't fragment a currently-cohesive control flow?

9. **`web_fetch` JSON handling.** Part 2 §12 shows `fetch_webpage_content()`
   special-cases only PDF by content-type and always HTML-parses everything
   else. Should a `application/json` branch return the raw (or pretty-
   printed/truncated) JSON body directly as the tool's `output`, bypassing
   `BeautifulSoup` entirely — and if so, should the same fix apply to the
   `src/search/` copy used by deep research (given the Known Gap #3 partial-
   duplication risk, Part 2 §13), or would fixing it in one copy without the
   other reintroduce exactly the drift THREAT_MODEL.md already flags?

10. **`src/search/` consolidation.** THREAT_MODEL.md's own Known Gap #3 says
    `analytics`, `cache`, `content`, `query`, and `ranking` are independent
    copies of `services/search/` that "can drift," while `core` and
    `providers` are already correctly aliased via `sys.modules` replacement.
    What's blocking applying the same aliasing technique to the remaining
    five modules, and is there evidence (behavioral test diffs, git history)
    that they've already drifted?

11. **Unifying the two admin-gate implementations.** `core/middleware.py:require_admin`
    and `routes/auth_routes.py:_require_admin_user` (Part 1 §§7-8) diverge
    specifically because the generic one trusts `request.state.current_user`
    in a way that was unsafe for one route. Given that divergence is now a
    known, documented, deliberate difference — should more routers audit
    whether they're using the "wrong" (looser) gate for auth-sensitive
    operations, and is there a principled rule for which gate a new
    admin-only route should use, beyond "auth routes use the strict one"?

12. **Memory relevance duplication.** Part 2 §3 documents two independently-
    implemented relevance algorithms — `MemoryManager.get_relevant_memories`'s
    token-overlap similarity and `MemoryVectorStore.search`'s embedding
    search — invoked from different call sites with no shared test asserting
    consistent ranking. Should the text-similarity path be removed entirely
    now that ChromaDB/fastembed is a load-bearing dependency elsewhere in the
    app (RAG, tool index), or does it serve a genuine degraded-mode purpose
    worth keeping as a documented fallback (the way `MemoryVectorStore`
    already degrades gracefully per Part 1 §2)?

13. **Capability-scoped agent sessions.** Part 3 §8 notes the trust model
    collapses to admin/non-admin with no finer-grained capability scoping
    once a session is admin-owned. Given the RAG tool-selection layer (Part
    1 §5) already computes a per-turn relevant-tool set, is there a natural
    extension to a per-session (not just per-user) declared capability set —
    e.g. "this scheduled task session should never get `bash`" — distinct
    from the global `disabled_tools`/settings toggles that apply uniformly?

14. **`ActivityEvent` ledger as a partial mitigation.** Given the ledger
    (Part 2 §9) captures pre-write file state for undo but nothing for
    `bash`'s broader effects (network calls, non-file side effects,
    processes it spawns), is there value in extending the ledger schema to
    at least *record* (not prevent) outbound network activity from `bash`/
    `python` tool calls, closing some of the audit gap even before a real
    sandbox (issue #1058) lands?

15. **Single-warm-model policy vs. multi-GPU or high-VRAM setups.**
    Part 3 §7's swap-based single-chat-slot policy is presumably tuned for
    VRAM-constrained consumer hardware. Given `services/hwfit/` already
    reasons about hardware fit for serving decisions, is there a case for
    letting `LocalModelServer` keep multiple chat slots warm when the
    detected hardware supports it (e.g. multi-GPU or high-VRAM systems),
    rather than the current always-swap policy regardless of headroom?
