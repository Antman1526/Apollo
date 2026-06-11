# Paperclip ⨉ Apollo — Engineering Handoff (for Codex / next agent)

**Last updated:** 2026-06-10
**Branch:** `codex/worktree-checkpoint` (continuation of `main`).
**Status:** Phase 1 + Phase 2 shipped & tested. **Update 2026-06-10:** Phase 3
(collector) and Phase 4 (The Floor) are now **built and tested** — see the
dated section at the end of this doc. Remaining: Phase 3.4 (per-agent token
attribution), Phase 5 (concurrency/federation), Phase 6 (native shell),
Phase 7 (productionization).

> Read this first, then the two companion docs:
> - Design/spec: [`docs/superpowers/specs/2026-06-07-paperclip-apollo-integration-design.md`](specs/2026-06-07-paperclip-apollo-integration-design.md) (see **Revision 2 — Native-first**)
> - Phase-1 task plan + spike record: [`docs/superpowers/plans/2026-06-07-paperclip-apollo-integration-phase1.md`](plans/2026-06-07-paperclip-apollo-integration-phase1.md)

---

## 1. The vision (what we're building toward)

**Apollo** = a native, installable macOS + Windows desktop app (no Docker required). Today it is a Python/FastAPI server + browser UI wrapped in a clickable launcher.

**Paperclip** (`paperclipai/paperclip` v2026.529.0, MIT) = an open-source "manage AI agents at work" platform (Node server + React UI, embedded Postgres). It lives **inside Apollo** as a section.

The end goal, in the user's words:
- Apollo + Paperclip feel like **one seamless product**.
- Agents run **entirely on local models** (by folder path), no cloud required.
- A **figurative-but-alive visual workspace** where you can *watch agents work* — see them pick up tasks, think, message each other, hand off — like observing an office. **Figurative** (rich animated dashboards), **not** literal sprites.

**Locked product decisions (from the design dialogue):**
| Topic | Decision |
| --- | --- |
| Integration shape | **B** — Paperclip is the orchestration *engine*; Apollo builds a **custom visual layer** on top of Paperclip's data/API. |
| Viz style | **Figurative** — "The Floor" + focus pane (see §5). |
| Agent work types | All: knowledge work, coding, research, ops. |
| Concurrency | "As many agents as allowed" → via **continuous batching on one served model + a scheduler**, not many model copies. Federate across the user's 2 machines. |
| Observability grains | All three: **state transitions**, **agent-to-agent messages**, **token/thinking** (figurative = stdout/transcript chunks; true token-level is an optional proxy enhancement). |
| Native shell | Lean **true native shell** (Tauri or Electron) — but **the viz is web tech, so build it in the current web UI first and wrap later** (shell is decoupled). |

User hardware (for concurrency planning): MacBook **M1, 64 GB** unified RAM; Windows **ROG, Ultra 9 285K, RTX 5070 Ti 16 GB VRAM, 96 GB RAM**. Wants max agent concurrency; both machines available (federation opportunity — Apollo already supports remote model endpoints via `LLM_HOSTS`/model-endpoints).

---

## 2. What is DONE and shipped (on `main`)

Bundling Paperclip into Apollo, native-first, on local models. **42 paperclip-specific tests pass** (`pytest tests/test_paperclip_*.py tests/test_lmproxy_routes.py tests/test_node_bootstrap.py`).

### Behavior today
- Paperclip is **enabled by default in the desktop launchers** (`PAPERCLIP_ENABLED=true`, `PAPERCLIP_MODE=native`). Off by default for library/Docker users.
- On native launch, Apollo:
  1. **Reuses** an already-running Paperclip on the configured port if healthy (no duplicate spawn / port clash), else
  2. **Auto-provisions a pinned Node** into `~/.apollo/.node` (downloads from nodejs.org; falls back to system Node), then
  3. **spawns + supervises `paperclipai run`** with env pointing its `opencode-local` agents at Apollo's local-model proxy.
- A **"Paperclip" sidebar tab** opens a **full-viewport "agents at work" workspace** (iframe to Paperclip's own origin) + a **Settings → Paperclip** panel (status/reachability) + a **pop-out** button.
- A **token-guarded local-model proxy** (`/lmproxy/v1/*`) forwards to whichever GGUF Apollo currently has warm (`llama-server`), so agents run on the user's local models.
- Opt-in **Docker** path also exists (`docker compose --profile paperclip up`).

### Files (all on `main`)
| File | Role |
| --- | --- |
| `services/paperclip/config.py` | Settings resolution: `mode` (docker/native/external/off), `url`, `browser_url`, `port`, model endpoint; `resolve_auth_secret()`, `resolve_proxy_token()`. |
| `services/paperclip/proxy.py` | Pure reverse-proxy helpers (URL build, hop-by-hop header filtering). |
| `services/paperclip/runtime.py` | Native lifecycle: `find_node/find_npx`, `build_env`, `build_command`, `PaperclipRuntime.start/stop/status/wait_healthy`; **reuse-if-already-serving**. |
| `services/paperclip/node_bootstrap.py` | Cross-platform pinned-Node download/extract: `dist_url`, `bin_paths`, `pick_lts`, `ensure_node` (idempotent, reuses installed). |
| `routes/paperclip_routes.py` | `setup_paperclip_routes(...)` — `/api/paperclip/status` (enabled/mode/browser_url/reachable) + a (now-secondary) `/paperclip/*` HTTP+WS reverse proxy. |
| `routes/lmproxy_routes.py` | `setup_lmproxy_routes(...)` — token-guarded `/lmproxy/v1/*` → warm `llama-server`. |
| `static/js/paperclip.js` | Reveals the tab when enabled; iframes `browser_url`; status + pop-out. |
| `static/index.html` | Sidebar `tool-paperclip-btn`, `#paperclip-modal` (full-viewport), Settings `#set-paperclip-section`. |
| `app.py` | Registers both routers; `/lmproxy` added to `AUTH_EXEMPT_PREFIXES`; startup hook auto-provisions Node + starts runtime (native), shutdown hook stops it. |
| `docker-compose.yml` | `paperclip` + `paperclip-db` services under the `paperclip` **profile** (opt-in). |
| `start-macos.sh`, `build-macos-app.sh`, `launch-windows.ps1` | Set `PAPERCLIP_MODE=native` + `PAPERCLIP_ENABLED=true`. |
| `.env.example`, `README.md`, `ACKNOWLEDGMENTS.md`, `licenses/paperclip-LICENSE` | Config docs + MIT attribution. |

### Config / env (see `.env.example`)
`PAPERCLIP_ENABLED`, `PAPERCLIP_MODE` (docker|native|external|off), `PAPERCLIP_URL`, `PAPERCLIP_BROWSER_URL`, `PAPERCLIP_PORT`, `PAPERCLIP_AUTH_SECRET`, `PAPERCLIP_PROXY_TOKEN` (+ `*_FILE` overrides), `PAPERCLIP_MODEL_ENDPOINT` (ollama|apollo|custom), `PAPERCLIP_MODEL_BASE_URL`, `PAPERCLIP_MODEL_NAME`, `PAPERCLIP_NODE_BIN`/`PAPERCLIP_NPX_BIN`/`PAPERCLIP_CLI`/`PAPERCLIP_VERSION`/`PAPERCLIP_NODE_VERSION`, `PAPERCLIP_PROXY_BASE_URL`.

### How to run / verify locally (no Docker)
```bash
# Tests
./venv/bin/python -m pytest tests/test_paperclip_*.py tests/test_lmproxy_routes.py tests/test_node_bootstrap.py -q

# Run Apollo natively with Paperclip on (7000 is taken by macOS Control Center → use another port)
PAPERCLIP_MODE=native PAPERCLIP_ENABLED=true AUTH_ENABLED=false \
  venv/bin/uvicorn app:app --host 127.0.0.1 --port 7872
curl -s http://127.0.0.1:7872/api/paperclip/status   # {enabled, mode, browser_url, reachable, ...}
```

---

## 3. Verified facts about Paperclip (load-bearing; confirmed from source on disk)

Source read from `~/Desktop/PaperClip_BP/paperclip-2026.416.0` (same shape as v2026.529.0) and the live instance on `:3100`.

1. **Self-contained DB.** `paperclipai` ships its own **embedded Postgres** (`@embedded-postgres/<platform>`), data under `~/.paperclip/instances/default/db/`. **Apollo does not manage Postgres** — only the one Node process. Cross-platform (Mac/Win/Linux).
2. **Realtime is a WebSocket** at **`/api/companies/:companyId/events/ws`** (Bearer-authed; checks better-auth sessions, `companyMemberships`, `instanceUserRoles`, and **`agentApiKeys`**). Source: `server/src/realtime/live-events-ws.ts`, `server/src/services/live-events.ts`.
3. **Event catalog** (`LIVE_EVENT_TYPES` in `packages/shared/src/constants.ts`; `LiveEvent = {id, companyId, type, createdAt, payload}`):
   - `agent.status` — agent presence/state
   - `heartbeat.run.queued` / `heartbeat.run.status` — task run lifecycle (state transitions)
   - `heartbeat.run.event` — structured run steps / tool calls
   - `heartbeat.run.log` — **live stdout/stderr chunks** `{ts, stream, chunk}` = the "thinking" transcript
   - `activity.logged` — general activity (rail; likely carries handoffs/messages)
   - `plugin.ui.updated` / `plugin.worker.crashed` / `plugin.worker.restarted`
   UI reference for consuming logs: `ui/src/components/transcript/useLiveRunTranscripts.ts` (connects to the same `/events/ws`, reconstructs transcript from log chunks — **chunk/line level, not raw tokens**).
4. **UI cannot be embedded under a subpath.** Paperclip's Vite build emits **absolute root paths** (`/assets/...`) and calls its API at absolute **`/api/*`** (e.g. `/api/auth/*`, `/api/health`), which collide with Apollo's own `/api`. → We **iframe Paperclip's own origin directly** (`browser_url`); Paperclip uses its own auth. (This reversed the original "reverse-proxy subpath" plan — see spike record in the phase-1 plan.)
5. **opencode local-model attribution is feasible.** The `opencode-local` adapter (`packages/adapters/opencode-local/src/server/{execute,test,runtime-config}.ts`) honors a **per-agent `OPENAI_API_KEY` override** (and base URL) from each agent's config. So minting **one proxy token per agent** lets `/lmproxy` map token→agent for true token-level observability + per-agent cost. Model ids are `provider/model` (e.g. `openai/<name>`); OpenCode does provider routing and honors `OPENAI_BASE_URL`.

---

## 4. The data pipeline for the visualization (designed, not built)

```
Paperclip  ──(one WebSocket)──>  Apollo Collector  ──normalize──>  Apollo event_bus  ──SSE/WS──>  Viz UI
/api/companies/:id/events/ws       (new)                (src/event_bus.py exists)        "The Floor"
  agent.status, heartbeat.run.*,                                                          + focus pane
  activity.logged

(optional enhancement) /lmproxy ── per-agent token streams ──> event_bus  (true token-level, per-agent cost)
                       requires per-agent OPENAI_API_KEY minting (see §3.5)
```

- **Overview = cheap aggregates** (state, a "thinking pulse", counts). **Detail = on-demand per-agent stream** (focus pane). Never fan all token/log streams to the UI at once.
- The `heartbeat.run.log` chunks already power the figurative "thinking" view — **no proxy tap required for v1**. The proxy tap is the optional path to true token-by-token.

---

## 5. The visualization design ("The Floor" — figurative, agreed)

Hero view = **focus + context**:
- **Floor (overview):** every agent is a card/dot in a **zone** (Backlog / Working / Review / Blocked / Done, or by team). Cards **animate between zones** on state change ("moving around the office"). A **pulse** marks agents actively generating. **Message arcs** flash on handoffs.
- **Focus pane:** click an agent → its **live transcript** (`heartbeat.run.log`), current task, tool calls (`heartbeat.run.event`), recent messages.
- **Activity rail:** global heartbeat from `activity.logged`.
- **Board toggle:** a living Kanban (lanes = run states) for precise work-tracking — same event stream, second rendering.

Event → view mapping: `agent.status`/`heartbeat.run.{queued,status}` → zones/cards; `activity.logged` → rail/arcs; `heartbeat.run.log`/`.event` → focus pane.

---

## 6. Open questions / risks (ordered)

1. **Collector auth (GATING).** How does Apollo obtain a Paperclip **Bearer token + companyId** to open `/api/companies/:id/events/ws` headlessly? Candidates: better-auth session (Apollo holds the admin/claim), or provision an **agent/instance API key** (`agentApiKeys` table). Confirm cleanest path, especially outside `local_trusted` mode. **Do this spike before building the collector.**
2. **`activity.logged` payload shape** — confirm it carries agent-to-agent messages/handoffs, or whether messages need the comments REST API.
3. **Transcript granularity** — `heartbeat.run.log` is chunk-level (fine for figurative). True token-level needs the `/lmproxy` per-agent-token path (§3.5).
4. **Concurrency reality** — agent count is bounded by served-model memory + batching throughput, not model copies. Design a scheduler + concurrency cap; consider LAN federation to the RTX box (`LLM_HOSTS`/model-endpoints already exist in Apollo).
5. **Native shell** — Tauri (lean, webview quirks) vs Electron (heavy, batteries-included, already have Node). Decoupled from the viz; decide later.
6. **Runaway/cost guards** — agent loop caps, budgets (Paperclip has budgets), and a global "pause all" are needed once many agents run autonomously.
7. **Paperclip API stability for token-level** — only if pursuing the proxy tap.

---

## 7. Phased roadmap (the path from here)

Each phase is independently shippable and testable. The viz is web tech → buildable in the current UI now; the native shell is a parallel/later track.

### Phase 3 — Observability backbone (collector + normalized stream)
**Goal:** a single Apollo event stream the UI can consume; prove signals flow end-to-end.
- **3.0 Spike (gating):** confirm collector auth to Paperclip's `/events/ws` (§6.1). Output: documented token-acquisition method.
- **3.1** `services/paperclip/collector.py` — connect to `/api/companies/:id/events/ws`, reconnect/backoff, normalize `LiveEvent` → Apollo events (`agent.state`, `task.run`, `agent.log`, `activity`, `agent.message`). Unit-test the normalizer against captured fixtures.
- **3.2** Discover companyId(s) via Paperclip REST (`/api/companies`) and subscribe per company.
- **3.3** Apollo SSE/WS endpoint (e.g. `/api/paperclip/stream`) fanning normalized events to the UI; overview events always, per-agent detail on subscribe. Auth via Apollo session.
- **3.4** (Optional) `/lmproxy` per-agent token attribution: mint a token per Paperclip agent, write it into the agent's `opencode-local` config (`OPENAI_API_KEY` override), map token→agent in the proxy, emit `agent.token` events.

### Phase 4 — "The Floor" visualization (web UI, shell-independent)
- **4.1** New full-section view (replace the iframe modal as the hero, keep iframe as a "classic Paperclip" toggle/board): render agents as zone cards from the normalized stream.
- **4.2** Animations: zone transitions, thinking pulse, message arcs.
- **4.3** Focus pane: live transcript + task + tools + messages for the selected agent.
- **4.4** Board toggle (living Kanban) + activity rail.
- **4.5** Scale/perf: virtualize for many agents; detail-on-focus only.

### Phase 5 — Local-model concurrency + federation
- **5.1** Define agent concurrency cap from served-model context/throughput; a scheduler/queue in front of `/lmproxy`.
- **5.2** Continuous-batching server option (llama.cpp server / vLLM on the RTX box) behind `/lmproxy`.
- **5.3** LAN federation: route a subset of agents to the Windows RTX model server via Apollo's existing remote-endpoint support; surface per-host load.
- **5.4** Runaway/budget guards + global pause.

### Phase 6 — True native shell (parallel track)
- **6.1** Choose Tauri vs Electron (footprint/native-feel vs ecosystem/speed).
- **6.2** Wrap the existing web UI; run Apollo (Python), Paperclip (Node), and inference as **sidecars**; manage lifecycle from the shell.
- **6.3** Packaging: macOS notarization + Windows code-signing; auto-update.
- **6.4** Native window/menu/tray; single-click launch.

### Phase 7 — Productionization
- Settings **enable/disable toggle** (settings-write endpoint; today enabling is via env).
- First-run UX for the Paperclip claim flow inside Apollo.
- Live end-to-end acceptance: serve a GGUF, native-run, create an `opencode-local` agent (`openai/<model>`), watch it work in The Floor.
- Telemetry/logging, error surfaces, docs.

---

## 8. Conventions & gotchas for the next agent
- Apollo routes: `setup_<name>_routes(...) -> APIRouter`, registered in `app.py` via `app.include_router(...)`. Tests are flat `tests/test_*.py`, stub `core.database` to avoid SQLAlchemy import side-effects, use `httpx.ASGITransport` (not `MockTransport`) when a route streams.
- Auth is a global `AuthMiddleware` in `app.py` (`AUTH_EXEMPT_PREFIXES`); **websockets bypass `BaseHTTPMiddleware`** — authenticate WS handlers explicitly.
- Port 7000 on macOS is held by Control Center (AirPlay) — use another port locally; `start-macos.sh` defaults to 7860.
- The user runs their own `paperclipai run` on `:3100`; the runtime's **reuse-if-healthy** avoids clobbering it. For a clean native-spawn test, use a different `PAPERCLIP_PORT` or stop the manual instance.
- TDD + frequent commits; Conventional Commits; this repo omits no trailer requirements beyond the project norm.
- Don't commit secrets. `BETTER_AUTH_SECRET`/`PAPERCLIP_PROXY_TOKEN` live in `~/.apollo/` (generated) or env.

---

## 9. Pointers
- Spec (with Revision 2 native-first): `docs/superpowers/specs/2026-06-07-paperclip-apollo-integration-design.md`
- Phase-1 plan + spike record: `docs/superpowers/plans/2026-06-07-paperclip-apollo-integration-phase1.md`
- Paperclip source on disk (read-only reference): `~/Desktop/PaperClip_BP/paperclip-2026.416.0`
- Live Paperclip instance: `http://localhost:3100` (`/api/health`, `/api/companies`)
- Upstream: `https://github.com/paperclipai/paperclip` (v2026.529.0, MIT)

---

## 10. Update — 2026-06-10 (Phases 3 + 4 built)

**Phase 4 ("The Floor") shipped** as an isometric Lego office in vanilla
SVG/CSS (`static/js/paperclip.js`, no deps): per-agent desks, shared stations
(Review Table / Help Bar / Done Dock), true depth-sorted occlusion (agents are
SVG groups painted with the furniture), walk/sit/talk/idle animations,
task-based conversation bubbles, Board role accents, reduced-motion support.
Tour + ingest API doc: `docs/paperclip-floor.md` (with renders).

**Phase 3 (collector) shipped:**
- **3.0 auth spike answer:** Paperclip's `/events/ws` accepts **tokenless**
  connections in `local_trusted` deployment mode (its default — what the
  bundled sidecar runs); REST is implicitly instance-admin in that mode too.
  In `authenticated` mode use an **agent API key** as a Bearer token
  (company-scoped, sha256-looked-up in `agentApiKeys`). Confirmed from
  `server/src/realtime/live-events-ws.ts` + `server/src/middleware/auth.ts`.
- **3.1–3.3:** `services/paperclip/collector.py` (REST company discovery, one
  WS per company, LiveEvent→Floor normalization, capped-backoff reconnect,
  clean shutdown) publishes into the shared `services/paperclip/events.EventHub`
  drained by `/api/paperclip/stream` (SSE; replays recent, emits
  `paperclip.stream.waiting` when idle, `…unavailable` when disabled).
  Env: `PAPERCLIP_COLLECTOR_ENABLED` / `PAPERCLIP_COLLECTOR_TOKEN` /
  `PAPERCLIP_COMPANY_ID`. Wired in `app.py` next to the runtime hooks.
  Tests: `tests/test_paperclip_collector.py`, `tests/test_paperclip_routes.py`,
  `tests/test_paperclip_floor_ui.mjs`.
- **Not yet validated against a live Paperclip** (none running during the
  build) — first end-to-end check: start the sidecar, open the Floor, confirm
  the label flips from "Live · waiting for agents" to "Live" when an agent runs.
- **3.4 MVP shipped (2026-06-11):** `services/paperclip/agent_tokens.py`
  (file-backed registry under `~/.apollo`, one token per agent, rotate on
  re-mint) + lmproxy accepts per-agent tokens alongside the shared one and
  publishes a debounced `heartbeat.run.event` Floor pulse per agent while it
  generates. Mint via admin `POST /api/paperclip/agent-tokens`; paste into
  the agent's `opencode-local` `OPENAI_API_KEY`. Still open from 3.4: writing
  the token into Paperclip's agent config automatically (needs live REST
  validation) and true per-agent token *accounting* (events exist, no ledger).

Also landed since 2026-06-08: SSE stream held open when idle (was: closed →
permanent preview), seq-watermark replay dedup, EventSource reconnect
tolerance + retry, native-mode model endpoint defaults (localhost, not
host.docker.internal), Node download SHASUMS256 verification + tar filter,
zombie reaping on stop, llama-server log fd leak fix, route hardening
(lmproxy/MCP), walk-once motion model, keyboard selection, arc aging.
