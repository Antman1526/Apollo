# Paperclip ⨉ Apollo Integration — Design

- **Date:** 2026-06-07
- **Status:** Approved (brainstorm) → ready for implementation plan
- **Target version:** Paperclip `v2026.529.0` (`paperclipai/paperclip`, MIT)
- **Author:** brainstormed with Claude Code

## 1. Goal

Bundle **Paperclip** (the open-source "manage AI agents at work" platform — Node
server + React UI, Postgres-backed) inside **Apollo** (Python/FastAPI self-hosted
AI workspace) so it feels like part of the application, and drive its agents with
**local models**.

Apollo runs Paperclip as a **managed sidecar**, reverse-proxies it under
`/paperclip/*`, and surfaces it as an iframe tab behind Apollo's auth gate.
Paperclip's agents run through its `opencode-local` adapter — the same opencode
runtime Apollo's own Agent is built on — pointed at a configurable local
OpenAI-compatible endpoint (default Ollama).

### Non-goals (v1)

- Deep SSO between Apollo identity and Paperclip's better-auth (stretch / phase 3).
- Replacing Apollo's existing Agent, skills, or memory subsystems.
- Cloud/multi-tenant Paperclip deployment. Scope is single-instance, private.

## 2. Decisions (locked during brainstorm)

| Decision | Choice |
| --- | --- |
| Integration shape | **Bundle Paperclip inside Apollo** (sidecar, not just a worker/plugin) |
| Deployment targets | **Docker (first-class) + native macOS (phase 2)** |
| Model wiring | **Configurable**: default Ollama, switchable to an Apollo OpenAI proxy or any custom OpenAI-compatible URL |
| UI seam | **Reverse-proxy `/paperclip/*` + iframe tab** in Apollo's nav, one origin, behind Apollo auth |
| Native Postgres | **Auto-managed Homebrew PostgreSQL**; Docker stays first-class, native is phase 2 |

## 3. Key facts about Paperclip v2026.529.0

Established by inspecting the repo (`paperclipai/paperclip`, default branch `master`):

- **Stack:** pnpm monorepo — `server/` (Node.js, run via `node --import tsx … server/dist/index.js`),
  `ui/` (React, built to static and served by the server when `SERVE_UI=true`),
  `cli/`, and `packages/` (shared, db, adapters, plugins).
- **Port:** `PORT=3100`, `HOST` configurable (`0.0.0.0` in Docker; we use `127.0.0.1` natively).
- **Database:** **requires Postgres** — `DATABASE_URL=postgres://paperclip:paperclip@localhost:5432/paperclip`.
  There is no zero-dependency embedded Postgres in the runtime; something must provide it.
- **Auth:** better-auth; `BETTER_AUTH_SECRET` required. Deployment env:
  `PAPERCLIP_DEPLOYMENT_MODE=authenticated`, `PAPERCLIP_DEPLOYMENT_EXPOSURE=private`,
  with a **first-admin browser claim flow** for fresh private deployments.
- **Data dir:** `PAPERCLIP_HOME` (e.g. `/paperclip`), `PAPERCLIP_INSTANCE_ID=default`,
  `PAPERCLIP_CONFIG=$PAPERCLIP_HOME/instances/default/config.json`.
- **"Models" = agent-runtime adapters**, not raw LLM API keys. Adapters include
  `opencode-local`, `codex-local`, `claude-local`, `gemini-local`, `grok-local`,
  `cursor-local/cloud`, `acpx-local`, `pi-local`, `openclaw-gateway`. Each shells out
  to a CLI agent; the production image globally installs `@anthropic-ai/claude-code`,
  `@openai/codex`, `opencode-ai`, and sets `OPENCODE_ALLOW_ALL_MODELS=true`.
- **Local model path:** wire the chosen adapter (default `opencode-local`) to an
  OpenAI-compatible base URL + model name. This is the seam Apollo configures.
- **External adapter support** is in progress (`adapter-plugin.md`): mutable server/UI
  adapter registries, open-ended `adapterType` input validation — useful if we later
  ship a first-class "Apollo local" adapter, but **not required for v1**.

## 4. Relevant facts about Apollo

- FastAPI app (`app.py` slim orchestrator), routes in `routes/`, domain logic in `src/`,
  long-running helpers in `services/` (e.g. `services/localmodels/`).
- **Local model serving** (`services/localmodels/server_manager.py`): spawns
  `llama-server` (llama.cpp, OpenAI-compatible) on a **dynamic free port** bound to
  `127.0.0.1`, single warm model. There is **no stable fixed OpenAI endpoint** today —
  hence the optional Apollo `/v1` proxy in phase 3.
- Apollo already **consumes** OpenAI-compatible / Ollama endpoints as providers
  (`src/endpoint_resolver.py`, `src/embeddings.py`, model-endpoints in `routes/model_routes.py`),
  and its Agent is built on **opencode** (per README) — natural fit with `opencode-local`.
- Deployment: `docker-compose.yml` (services `apollo`, `chromadb`, `searxng`, `ntfy`);
  native macOS via `start-macos.sh` / `build-macos-app.sh` (a launcher around the Python
  venv + uvicorn — **no Node or Postgres bundled today**).
- Established pattern for "missing native dependency": degrade gracefully with a helpful
  message instead of crashing (see `llama-server not found` in `server_manager.py`).

## 5. Architecture

```
Browser
  │
  ▼
Apollo :7000  ──(SecurityHeaders + AuthManager gate)
  │  GET/POST/WS /paperclip/{path}
  ▼
routes/paperclip_routes.py  (reverse proxy, HTTP + websocket)
  │
  ▼
Paperclip :3100 (SERVE_UI=true)
  │                                   ┌─ opencode-local adapter ─┐
  ├──> Postgres (paperclip db)        │  base_url + model        │ ──> local model endpoint
  └──> PAPERCLIP_HOME data dir        └──────────────────────────┘     (Ollama | Apollo /v1 | custom)
```

In **Docker**, Paperclip + Postgres are compose services; Apollo proxies to
`http://paperclip:3100`. In **native macOS**, Apollo spawns and supervises the
Paperclip process and a Homebrew Postgres cluster, proxying to `http://127.0.0.1:3100`.

## 6. Components

### 6.1 `services/paperclip/` (new, mirrors `services/localmodels/`)

- **`config.py`** — resolve settings from env + Apollo settings:
  `enabled`, `mode` (`docker|native|external|off`), `url`, `port`, `data_dir`, `db_url`,
  `auth_secret`, `version`, and model wiring (`model_endpoint`, `model_base_url`, `model_name`).
- **`prereqs.py`** — detect/bootstrap toolchain. Native: locate Node LTS (Homebrew),
  `corepack`/`pnpm` if building from source, and Postgres binaries. Docker/external: verify
  `PAPERCLIP_URL` reachable. Returns a structured readiness result (never throws into startup).
- **`postgres.py`** (native) — initialize a local PG data dir under `PAPERCLIP_DATA_DIR`,
  create the `paperclip` role + database, start/stop the cluster, health-check. Idempotent.
- **`server_manager.py`** — native only: spawn the Paperclip process on a fixed local port
  (default 3100, auto-bump if taken) with injected env (`DATABASE_URL`, `PORT`, `HOST=127.0.0.1`,
  `SERVE_UI=true`, `BETTER_AUTH_SECRET`, `PAPERCLIP_HOME`, deployment mode/exposure). Track the
  process, health-check `/`, restart/stop. No-op when `mode=docker|external`.
- **`adapter_config.py`** — seed/update Paperclip's `opencode-local` (or selected) adapter so its
  provider points at the configured local endpoint + model. Mechanism resolved in spike S3
  (Paperclip `config.json` vs adapter CLI config vs Paperclip API).
- **`lifecycle.py`** — orchestrate `prereqs → postgres → server → adapter_config`, expose
  `start()/stop()/status()`, and hook into Apollo startup/shutdown via `src/app_initializer.py`
  and readiness reporting.

### 6.2 `routes/paperclip_routes.py` (new)

- Mount `/paperclip/{path:path}` reverse proxy for **HTTP and websockets**.
- Forward method, headers, cookies, body; stream responses; handle the WS upgrade for
  Paperclip realtime/heartbeat traffic.
- Sit **behind Apollo's existing auth gate** — only authenticated Apollo users reach it.
- Subpath handling per spike S1 (configure Paperclip base path, else rewrite assets/HTML).

### 6.3 Apollo OpenAI gateway (phase 3, new route)

- Stable `/v1/chat/completions` + `/v1/models` forwarding to Apollo's currently-warm
  `llama-server`. One selectable value for `PAPERCLIP_MODEL_ENDPOINT=apollo`.

### 6.4 Settings + nav (UI)

- Settings → new "Paperclip" (or under AI) section: enable toggle, **model endpoint selector**
  (Ollama / Apollo / custom URL + model name), live status (running/healthy/degraded),
  first-admin-claim hint on first open.
- Nav: a "Paperclip" item that opens the iframe tab pointing at `/paperclip/`.

## 7. Configuration / secrets (`.env.example` additions)

```
PAPERCLIP_ENABLED=false
PAPERCLIP_MODE=docker            # docker | native | external | off
PAPERCLIP_URL=http://paperclip:3100   # docker service name; 127.0.0.1 native
PAPERCLIP_PORT=3100
PAPERCLIP_DATA_DIR=               # native data dir (PAPERCLIP_HOME); default under Apollo data/
PAPERCLIP_DB_URL=                 # postgres://… ; auto-derived natively
PAPERCLIP_AUTH_SECRET=            # auto-generated into Apollo's secret store if blank
PAPERCLIP_VERSION=2026.529.0
PAPERCLIP_MODEL_ENDPOINT=ollama   # ollama | apollo | custom
PAPERCLIP_MODEL_BASE_URL=http://localhost:11434/v1
PAPERCLIP_MODEL_NAME=
```

`PAPERCLIP_AUTH_SECRET` is generated once and persisted via Apollo's existing secret
mechanism (same approach as other Apollo secrets) so restarts keep sessions valid.

## 8. Deployment

### 8.1 Docker (phase 1, first-class)

- Add to `docker-compose.yml`:
  - `paperclip-db` — `postgres:16`, named volume, internal network, healthcheck.
  - `paperclip` — Paperclip v2026.529.0 (published image if available, else build from a
    vendored checkout / submodule), `SERVE_UI=true`, `DATABASE_URL` → `paperclip-db`,
    `PAPERCLIP_HOME` volume, deployment mode `authenticated`/exposure `private`. **Not**
    port-exposed to the host — reached only via Apollo's reverse proxy.
  - `apollo` gains `PAPERCLIP_URL=http://paperclip:3100` and the model env.

### 8.2 Native macOS (phase 2)

- On startup (when `PAPERCLIP_ENABLED` and `MODE=native`), `services/paperclip/lifecycle`
  bootstraps Node + Homebrew Postgres + the Paperclip process, supervised like `llama-server`.
- Acquire Paperclip via the pinned npm CLI (`paperclipai@2026.529.0`) or a vendored checkout
  (decided in spike S2).
- **Graceful degradation:** missing Node/brew/Paperclip disables the feature with guidance;
  Apollo continues to run. Wire into `start-macos.sh` / `build-macos-app.sh` only as far as
  surfacing prerequisites; the Python lifecycle owns runtime management.

## 9. Auth (v1)

- Reverse proxy requires an authenticated Apollo session.
- Paperclip runs in private/authenticated mode and uses its own first-admin claim flow for
  internal accounts. SSO between the two identity systems is explicitly deferred.

## 10. Testing

- **Unit:** config resolution & precedence; env injection; endpoint selection (ollama/apollo/custom);
  proxy URL building; mocked prereq detection; mocked Postgres bootstrap; auth-secret generation/persistence.
- **Integration:** reverse proxy forwards GET/POST and upgrades WS to a stub server; health-check
  transitions; idempotent `start()/stop()`; graceful degradation when Node/brew/Docker are absent.
- Follow Apollo's `tests/` layout and `pytest` + `pytest-asyncio`.
- **Manual acceptance:** `docker compose up` → log into Apollo → open Paperclip tab → claim admin →
  create an `opencode-local` agent bound to a local model → run a trivial task and see it complete.

## 11. Phasing

1. **Docker end-to-end** — compose services, reverse proxy (HTTP+WS), iframe tab, settings,
   `opencode-local` → Ollama wiring, auth gate. Independently shippable & testable.
2. **Native macOS** — Node + Homebrew PG bootstrap, subprocess lifecycle, degradation, launcher wiring.
3. **Polish** — Apollo `/v1` proxy option, endpoint-selector UI niceties, optional SSO,
   optional first-class "Apollo local" Paperclip adapter.

## 12. Open items / spikes (resolve during planning)

- **S1 — Subpath base.** Does the Paperclip UI support being served under `/paperclip/`
  (a base-path / public-URL setting)? If not, the proxy must rewrite asset/HTML paths.
  Biggest risk to the "seamless" seam.
- **S2 — Acquisition & pinning.** Published Docker image and/or npm CLI pinned to v2026.529.0,
  vs building from source with pnpm. Drives repo size and the `.dmg`.
- **S3 — Adapter wiring.** Exact way to point `opencode-local` (or codex) at a local
  OpenAI-compatible endpoint + model from Apollo (config.json vs adapter CLI config vs Paperclip API).
- **S4 — Realtime transport.** Paperclip websocket/SSE specifics through the proxy.
- **S5 — Native toolchain.** Node LTS + corepack/pnpm availability and bootstrap on macOS.

## 13. Licensing / attribution

Paperclip is **MIT**. Add attribution to `ACKNOWLEDGMENTS.md` and a license copy under
`licenses/` (Apollo already tracks third-party credits). Record the vendoring method chosen
in S2.

## 14. Risks

- **SPA subpath** rewriting (S1) could be the heaviest single task; mitigated by preferring a
  native base-path setting.
- **Native footprint:** Node server + Postgres + agent CLIs add real memory/CPU on desktop —
  document expectations; keep the feature opt-in (`PAPERCLIP_ENABLED=false` default).
- **Version drift:** pin `PAPERCLIP_VERSION`; surface mismatches in diagnostics.
- **Secret handling:** never log `BETTER_AUTH_SECRET` / `DATABASE_URL`; redact in diagnostics
  (consistent with Apollo's existing secret hygiene).

---

## Revision 2 — Native-first (2026-06-07)

Direction clarified by the user: **Apollo is an installable Mac/Windows app that
runs without Docker.** Paperclip is a built-in, interactive "AI agents for work"
section *inside* Apollo, running on the user's local models, where you can watch
agents working (an "office of AI agents"). Docker becomes a secondary/optional
deployment; native is primary.

### Enabling discovery
`paperclipai` is **fully self-contained and cross-platform**: it backs itself
with **`@embedded-postgres`** (downloads a platform Postgres binary, data under
`~/.paperclip/instances/<id>/db/`) and runs the Node server. Verified on the
host: live `postgres` backends execute from
`@embedded-postgres/darwin-arm64/native/bin/postgres`. So Apollo does **not**
manage Postgres at all — it only supervises the `paperclipai` process. This
removes the Homebrew/vendored-Postgres work from the native path entirely.

### Decisions (Revision 2)
| Topic | Choice |
| --- | --- |
| Node runtime | **Bundle Node** in the Apollo app (zero prerequisites on a fresh Mac/Windows). |
| Model wiring | **Apollo local-model proxy** — Apollo serves GGUF from the configured local-models path behind one stable OpenAI-compatible endpoint; Paperclip's `opencode-local` agents point at it. |
| UI | **Dedicated full section/view** in Apollo (persistent workspace, not a modal). |
| Postgres | None to manage — `paperclipai` uses embedded-postgres. |
| Lifecycle | Apollo spawns/health-checks/supervises `paperclipai run` and stops it on exit. |

### Native architecture
```
Apollo (native, bundled Python + bundled Node)
  ├─ supervises: paperclipai run  ──>  embedded-postgres (~/.paperclip/.../db)
  │                         │
  │                         └─ opencode-local agents → OPENAI_BASE_URL =
  │                                Apollo local-model proxy (token-auth, localhost)
  ├─ local-model proxy: stable /v1/* → warm llama-server (GGUF from the
  │     user's configured local-models folder)  [services/localmodels]
  └─ UI: dedicated "Paperclip" section iframes Paperclip's origin (browser_url)
```

### Phase 2 components (this revision)
1. **Apollo local-model proxy** — a stable, localhost, token-authenticated
   OpenAI-compatible endpoint (`/v1/chat/completions`, `/v1/models`, …) that
   forwards to the currently-warm `llama-server` (auto-starting the selected
   model when needed). Auth-exempt prefix guarded by a generated bearer token
   passed to Paperclip/opencode.
2. **Native lifecycle** (`services/paperclip/runtime.py`) — locate the bundled
   Node, ensure pinned `paperclipai`, spawn `paperclipai run` with env (PORT,
   PAPERCLIP_HOME, `OPENAI_BASE_URL`=proxy, `OPENAI_API_KEY`=token), health-check
   `/api/health`, supervise + restart, stop on shutdown. Cross-platform.
3. **Dedicated UI section** — a persistent Paperclip workspace view (not a modal)
   that iframes `browser_url`, with status/“start/stop” controls.
4. **Bundling** — ship a Node runtime in the macOS `.app`/`.dmg`
   (`build-macos-app.sh`) and the Windows launcher; warm the pinned `paperclipai`
   on first run.

### Phasing (Revision 2)
- **Phase 1 (done):** config/proxy/status, opt-in Docker compose, direct-iframe
  tab, attribution. Verified natively in external mode.
- **Phase 2a:** local-model proxy + native lifecycle (auto-managed paperclipai
  on local models) — verifiable on this host without installer changes.
- **Phase 2b:** dedicated full section UI.
- **Phase 2c:** Node bundling into Mac/Windows installers.
