# Apollo

```
───────────────────────────────────────────────
 ⊹ ࣪ ˖ ૮( ˶ᵔ ᵕ ᵔ˶ )っ  Apollo vers. 1.0
───────────────────────────────────────────────
```

![Apollo](docs/apollo.jpg)

**Apollo is a self-hosted, local-first AI workspace** — the ChatGPT/Claude UI experience
running entirely on your own hardware, with your own data and your own models. But with
more jank and fun. Privacy-first, no telemetry, no trojan.

Concretely, Apollo is one app that lets you **chat with language models** — local GGUF
files served on demand through `llama.cpp` (one warm model at a time, swapped automatically),
or any OpenAI-compatible / Anthropic / OpenRouter / Groq / Ollama endpoint you add — and then
wraps that chat in a full workspace: a **tool-running agent loop** with shell, python, web,
browser, email, calendar, notes, tasks, memory, skills, and image tools plus **MCP** servers;
**web access** through a managed no-Docker SearXNG sidecar with a per-message auto-search
decider and DuckDuckGo fallback; an **embedded interactive browser** (a live screencast of a
server-side Chromium you and the agent share); **deep research** that searches, crawls, and
synthesizes cited reports; **persistent memory and skills** (ChromaDB + local fastembed ONNX
embeddings) that the assistant carries across sessions; a hands-free **voice call mode**
(local Whisper STT + Piper/Kokoro TTS + energy-VAD + barge-in); a **second brain** that
distills chats into vector-indexed memories and imports your ChatGPT/Claude exports; a
**knowledge-graph** view over those memories; an **adversarial reviewer** that has a second
model critique answers; **email, calendar, notes, and tasks** the agent can act on; a
**multi-tab document editor**; a hardware-aware model **Cookbook**; **24 themes**; and an
installable **PWA**.

It is a **three-tier system**: a **FastAPI backend** (Python 3.11+, one `uvicorn` process)
exposing ~40 modular routers, a **framework-free vanilla-JS frontend** (ES modules,
server-sent events, no build step), and a **SQLite (SQLAlchemy) + ChromaDB data layer**.
Everything runs as that one process plus on-demand `llama-server` subprocesses, a managed
SearXNG sidecar, and the optional Paperclip Node sidecar. See [Architecture](#architecture)
for enough detail to rebuild it, and [docs/recreation/](#recreation--full-technical-docs)
for the complete reconstruction spec.

> Apollo is a renamed distribution of **[Odysseus](https://github.com/pewdiepie-archdaemon/odysseus)** by **pewdiepie-archdaemon**. All the original work is theirs — Apollo only changes the name. See [ACKNOWLEDGMENTS.md](ACKNOWLEDGMENTS.md) for full credits.

## What it actually does

  - **Chat** -- chat with any local model or API; adding them is super simple.<br>　<sub>vLLM · llama.cpp · Ollama · OpenRouter · OpenAI · Anthropic · Groq · OpenAI-compatible</sub>
  - **Local Models** -- point Apollo at folders of GGUF models; they're discovered automatically, appear in the model picker, and a llama.cpp server is launched on the fly when you pick one.<br>　<sub>folder auto-scan · auto-serve on select · single warm chat model · configurable dirs (Settings → AI)</sub>
  - **Agent** -- hand it tools and let it run the whole task itself: `bash`, `python`, `web`, `browser`, `email`, `calendar`, `notes`, `tasks`, `memory`, `skills`, `image`, and any MCP server you add.<br>　<sub>built on [opencode](https://github.com/anomalyco/opencode) · MCP · web · files · shell · skills · memory · SSE streaming</sub>
  - **Voice call mode** -- a hands-free "call" overlay: talk, it transcribes, thinks, and speaks back, with barge-in so you can interrupt.<br>　<sub>local Whisper STT · Piper/Kokoro/Voicebox TTS · energy-RMS VAD · barge-in · `/api/stt` · `/api/tts`</sub>
  - **Second brain** -- distill any chat (or your imported ChatGPT/Claude export) into durable, deduped, vector-indexed memories the agent recalls later.<br>　<sub>LLM fact distiller · dedup + vector index · ChatGPT/Claude export parsers · `/api/memory/distill-session` · `/api/memory/import-chat-export`</sub>
  - **Knowledge graph** -- a graph view over your memories: semantic + same-session edges rendered as an interactive SVG force layout.<br>　<sub>semantic + session edges · deterministic layout · `/api/memory/graph`</sub>
  - **Adversarial reviewer** -- one click has a *second* model critique the last answer for errors, missing caveats, and gaps.<br>　<sub>reviewer/utility model role · Verdict/Issues/Suggestion · inline Review button + persisted Review mode · `/api/review`</sub>
  - **Skill-pack installer** -- import an Agent-Skills pack straight from a GitHub repo; prose skills are published, script-backed ones are quarantined as drafts.<br>　<sub>SKILL.md discovery · trust-tier classifier · SSRF/zip-bomb/tar-symlink guards · admin-gated · `/api/skills/packs/{preview,install}`</sub>
  - **Ralph Loop** -- an opt-in PRD/task loop for learning across iterations and getting scoped agent work done.<br>　<sub>prd.json · progress.md · AGENTS learning snippets · quality gates</sub>
  - **Embedded Browser** -- a live interactive screencast of a shared server-side Playwright Chromium; you and the agent drive the same session, and even iframe-blocked sites work.<br>　<sub>CDP screencast over WebSocket · input forwarding · `browser` agent tool · `/api/browser/*`</sub>
  - **Browser + Crawl Agents** -- browser-use verifies real UI workflows, while crawl4ai turns web sources into research-ready Markdown.<br>　<sub>Paperclip Floor QA · Ralph verification commands · Crawl4AI source imports</sub>
  - **Cookbook** -- Scans your hardware, recommends models, click to download and serve.. easy!<br>　<sub>built on [llmfit](https://github.com/AlexsJones/llmfit) · VRAM-aware · GGUF / FP8 / AWQ · fit scoring · vLLM / llama.cpp serving</sub>
  - **Web Access** -- tri-state toggle (off / auto / always) per chat. **Auto** runs a per-message heuristic to decide whether a search is useful, then injects results as context — even models without tool-calling get fresh sourced answers; the spinner shows "Searched the web" when auto fired. **Always** pre-searches every message in chat mode and enables web tools in agent mode. The built-in **SearXNG** sidecar (no Docker required) installs into `data/searxng/` via `scripts/setup-searxng.sh` (macOS/Linux) or `scripts/setup-searxng.ps1` (Windows), pinned to a smoke-tested commit (override with `SEARXNG_GIT_REF`); Apollo starts and health-checks it automatically. If the sidecar crashes, a **watchdog** restarts it automatically (at most once per 5-minute cooldown). When the sidecar is down or not installed, the provider chain skips it with no timeout penalty and falls back to DuckDuckGo; sources are tagged with the answering provider and the UI shows a "via DuckDuckGo" badge. **Incognito** chats never send queries to search engines. Sidecar output is logged to `logs/searxng.log` and its tail is visible in the status panel. Admin default: **Settings → Web Access** (`web_access_mode`; default `manual` = legacy toggle behavior for untouched installs).<br>　<sub>SearXNG managed sidecar · DuckDuckGo fallback · off / auto / always · heuristic auto-decider · localhost-only port 8893</sub>
  - **Deep Research** -- multi-step runs that gather, read, and synthesize sources into a nice visual report.<br>　<sub>adapted from [Tongyi DeepResearch](https://github.com/Alibaba-NLP/DeepResearch)</sub>
  - **Compare** -- a fun tool to compare models side by side. Test completely blind, no bias!<br>　<sub>multi-model · blind test · synthesis</sub>
  - **Documents** -- YOU write the text, AI is there to assist, not the opposite.<br>　<sub>multi-tab editor · markdown · HTML · CSV · syntax highlighting · AI edits · suggestions</sub>
  - **Memory / Skills** -- Persistent memory and skills, your agent evolves over time as it better understands you and your tasks!<br>　<sub>ChromaDB · fastembed (ONNX) · vector + keyword retrieval · import/export · distill · graph</sub>
  - **Email** -- IMAP/SMTP inbox with AI triage built in: urgency reminders, auto-tag, auto-summary, auto-reply drafts, auto-spam.<br>　<sub>IMAP · SMTP · per-account routing · CalDAV-aware</sub>
  - **Notes & Tasks** -- Quick notes with reminders, a todo list, and scheduled tasks the agent can act on.<br>　<sub>note pings · checklist · cron-style tasks · ntfy / browser / email channels</sub>
  - **Calendar** -- Local-first calendar with CalDAV sync to Radicale / Nextcloud / Apple / Fastmail.<br>　<sub>CalDAV pull · .ics import/export · per-calendar colors · agent-aware</sub>
  - **Works on mobile** -- looks and runs great on your phone, not just desktop.<br>　<sub>responsive · installable (PWA) · touch gestures</sub>
  - **Extras** -- more to explore, happy if you give it a go!<br>　<sub>image editor · theme editor (24 themes, dark + light) · file uploads (vision + PDF) · presets · sessions · 2FA</sub>

## New this session

A batch of features landed recently. Each is grounded in real code (file paths in
[Architecture](#architecture) and [docs/recreation/](#recreation--full-technical-docs)):

- **Hands-free voice call mode.** A "call" overlay drives a full listen → transcribe →
  think → speak loop with no button presses. A pure, unit-tested state machine
  (`static/js/voiceCall.js`, `createCallMachine`) sits over browser effects; an
  **energy-RMS voice-activity detector** (`static/js/vad.js`, `createVadGate` — 512-FFT,
  ~60 fps, configurable threshold and silence timeout) fires speech-start/end events. Mic
  audio (WebM, with echo-cancellation + noise-suppression) is posted to `/api/stt/transcribe`
  and the reply is spoken via the TTS manager. **Barge-in** is built in: talking while the
  assistant is speaking stops playback and starts capturing you again.
- **Voicebox as an optional TTS *and* STT engine.** Alongside local **faster-whisper** (STT)
  and local **Kokoro-82M / Piper** (TTS), you can point Apollo at a running Voicebox voice
  studio. It's selected as the `voicebox` provider in **Settings**, defaults to
  `http://127.0.0.1:17493` (`voicebox_url`), synthesizes via `POST {url}/generate` with a
  picked **profile**, and transcribes via `POST {url}/transcribe`
  (`services/tts/tts_service.py`, `services/stt/stt_service.py`).
- **Second brain over chats.** Distill any session — or an uploaded **ChatGPT / Claude
  export** — into durable, atomic, **deduplicated, vector-indexed memories** the agent recalls
  later. An LLM fact-distiller (`services/memory/distiller.py`) feeds an orchestrator
  (`services/memory/brain.py`) that skips duplicates and best-effort indexes into the vector
  store; export parsers (`services/memory/chat_import.py`) auto-detect and normalize both
  formats. Endpoints: `POST /api/memory/distill-session`, `POST /api/memory/import-chat-export`.
- **Knowledge-graph tab.** A graph over your memories: **semantic** edges (vector-similarity
  neighbors, thresholded and top-N capped) plus **same-session** chain edges, built purely and
  deterministically (`services/memory/graph.py`, `GET /api/memory/graph`) and rendered as an
  interactive **SVG force layout** with a deterministic layout step (unit-tested,
  `tests/test_graph_layout.mjs`).
- **Adversarial reviewer (second opinion).** An inline **Review** button (plus a persisted
  **Review mode** toggle) sends the question + answer to a *second* model that returns a
  structured **Verdict / Issues / Suggestion** critique (`services/review/reviewer.py`,
  `POST /api/review`). It resolves the **reviewer** model role, falling back to **utility**
  then the default chat endpoint.
- **GitHub skill-pack installer.** Import an **Agent-Skills** pack straight from a GitHub repo:
  Apollo fetches the tarball (SSRF-, zip-bomb-, and tar-symlink-guarded), discovers `SKILL.md`
  files, and classifies each by **trust tier** — prose-only skills are published, while
  **script-backed** skills (any `scripts/`, `hooks/`, `.py/.sh/.mcp.json`, …) are **quarantined
  as drafts** and never auto-run. Provenance is stamped into each installed skill
  (`services/skills/pack_installer.py`, admin-gated `POST /api/skills/packs/{preview,install}`).
- **Agent-subprocess secret-scrub (security fix).** The agent's `bash`/`python` tools,
  background jobs, the shell service, and MCP stdio servers used to inherit the *full*
  `os.environ` — every provider API key, `DATABASE_URL`, decrypted SMTP/IMAP password,
  `SEARXNG_SECRET`, etc. `src/subproc_env.py` (`build_agent_env`) now hands those children a
  **minimal allowlisted, default-deny** environment with a denylist scrub layered on top, so a
  prompt-injected agent or malicious skill can't `env | curl` your secrets out.

## Demo
A full, hover-to-play tour lives on the landing page (`docs/index.html`).

<details>
<summary>Screenshots / clips</summary>

### Chat & Agents
![Chat & Agents](docs/chat.gif)
### Deep Research
![Deep Research](docs/research.gif)
### Compare
![Compare](docs/compare.gif)
### Documents
![Documents](docs/document.gif)
### Notes & Tasks
![Notes & Tasks](docs/notes.gif)

</details>

## Requirements

- **Python 3.11+** (CI runs on 3.12). The core app — chat, agent, memory, documents, email,
  calendar, deep research, voice, second brain, graph, reviewer — runs fully native on
  macOS / Linux / Windows.
- **Cookbook** background model downloads/serving also needs `tmux` (POSIX) or Git-for-Windows
  `bash.exe`. Local GPU *serving* of vLLM/SGLang is CUDA/ROCm-only (Linux/WSL2); macOS uses
  llama.cpp/Ollama over Metal.
- **Voice** local engines are optional extras: `faster-whisper` (STT), plus your chosen TTS
  (Kokoro / Piper / an OpenAI-compatible `/audio/speech` endpoint / Voicebox). The app runs
  without them; voice just stays disabled until configured.
- The app itself is lightweight; local model serving is the heavy part and depends on the
  model, runtime, GPU, and VRAM — small hosts can connect to API or remote model servers
  instead.

## Quick Start

Defaults work out of the box: clone, run, then configure models/search/email/voice
inside **Settings**. Only edit `.env` for deployment-level overrides like
`APP_BIND`, `APP_PORT`, `AUTH_ENABLED`, `DATABASE_URL`, or a pre-seeded admin password.

On first setup, Apollo creates an admin account (`admin` unless
`APOLLO_ADMIN_USER` is set) and prints a temporary password in the terminal.
For Docker installs, the same line is in `docker compose logs apollo`.
Use that for the first login, then change it in **Settings**.

Contributing? See [CONTRIBUTING.md](CONTRIBUTING.md) for setup, testing, and
pull request guidelines.

### Docker (recommended)
```bash
git clone https://github.com/Antman1526/Apollo.git
cd Apollo
cp .env.example .env       # optional, but recommended for explicit defaults
docker compose up -d --build
```
Open `http://localhost:7000` when the containers are healthy. Docker Compose
binds the web UI to `127.0.0.1` by default. If the port is taken, set
`APP_PORT=7001` in `.env` and recreate the container. Set `APP_BIND=0.0.0.0`
only when you intentionally want LAN/reverse-proxy access. Compose starts the
bundled `searxng` and `ntfy` services; vector memory is persisted in
`./data/chroma` inside Apollo rather than exposed through a separate ChromaDB
HTTP service.

### Native Linux / macOS
```bash
git clone https://github.com/Antman1526/Apollo.git
cd Apollo
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python setup.py
python -m uvicorn app:app --host 127.0.0.1 --port 7000
```
`app:app` is the ASGI application object in `app.py`. `setup.py` creates the data dirs,
initializes the SQLite DB, and prints the first-boot admin password.
Use `--host 0.0.0.0` only when you intentionally want LAN/reverse-proxy access.

### Apple Silicon (one command)
Docker on macOS is a Linux VM with no Metal GPU access. For GPU-accelerated
Cookbook on an M-series Mac, run Apollo natively. `start-macos.sh` installs
Homebrew deps, creates the venv, runs `setup.py`, and starts uvicorn (it is
effectively `venv/bin/python -m uvicorn app:app`):

```bash
git clone https://github.com/Antman1526/Apollo.git
cd Apollo
./start-macos.sh
```

It launches at `http://127.0.0.1:7860` (not `7000` — macOS AirPlay Receiver
often holds `7000`). The script reads `.env` at startup, so `APP_BIND` and
`APP_PORT` are picked up automatically. To expose it to your phone over a
trusted LAN/VPN such as Tailscale:

```bash
APOLLO_HOST=0.0.0.0 ./start-macos.sh
# then open http://<tailscale-ip>:7860
```

Keep `AUTH_ENABLED=true` (the default) before binding outside loopback, and do
not expose the port directly to the public internet.

### macOS desktop builds (two flavors)

Apollo ships **two** macOS build scripts that produce a double-clickable `Apollo.app` and a
drag-to-Applications `Apollo.dmg`. Pick one:

```bash
# 1. Launcher build — small, drives THIS repo's venv (Python not bundled).
#    Best for developers who keep the repo; Cookbook keeps direct Metal-GPU access.
./build-macos-app.sh
#   -> dist/Apollo.app  (double-click to start the server + open the UI)
#   -> dist/Apollo.dmg

# 2. Self-contained bundle — PyInstaller-packs Python + all deps, so the app
#    runs on any Apple-Silicon Mac WITHOUT the repo or a preinstalled venv.
./build-macos-bundle.sh
#   -> dist/Apollo.app  (fully self-contained, ~onedir under Contents/Resources/apollo)
#   -> dist/Apollo.dmg
```

The launcher build (`build-macos-app.sh`) bakes the repo install path into the app — rebuild
after moving the repo. The self-contained build (`build-macos-bundle.sh`) uses
`packaging/apollo.spec` + `packaging/apollo_boot.py`, pins the app's SQLite DB, and waits on a
readiness probe before opening the UI; it needs a working `./venv` with the app deps +
`pyinstaller` only to *build*, not to *run*. Both default to port `7860` (override with
`APOLLO_PORT`).

### Native Windows

**One-command launcher** (creates the venv, installs deps, runs setup, starts the
server; safe to re-run):

```powershell
git clone https://github.com/Antman1526/Apollo.git
cd Apollo
powershell -ExecutionPolicy Bypass -File .\launch-windows.ps1
```

Or do it by hand:

```powershell
py -3.11 -m venv venv
venv\Scripts\Activate.ps1
pip install -r requirements.txt
python setup.py
python -m uvicorn app:app --host 127.0.0.1 --port 7000
```

If `python` points at an older interpreter, use `py -3.12` (or another installed
3.11+ version) for the venv step.

For full **Cookbook** background model downloads and the agent shell tool, also install
[Git for Windows](https://git-scm.com/download/win) (provides `bash.exe`).
Local GPU *serving* of vLLM/SGLang needs Linux/WSL2; for a local model on Windows,
[Ollama](https://ollama.com/download) is the easiest path — point Apollo at
`http://localhost:11434/v1` in Settings.

Open `http://localhost:7000`, log in with the generated admin password,
and configure everything else inside **Settings**.

### Optional sidecars and runtimes

<details>
<summary>Paperclip (agent management), Ralph loop, embedded browser, browser-use, crawl4ai, GPU overlays, Ollama, troubleshooting</summary>

#### Paperclip (agent management) — optional

Apollo can bundle **[Paperclip](https://github.com/paperclipai/paperclip)**, an
agent-management UI, as an opt-in sidecar. In Docker it runs as its own
container (plus a small Postgres) behind a `paperclip` Compose profile; in
native desktop mode Apollo supervises the `paperclipai` process directly and
**auto-downloads a pinned Node runtime** into `~/.apollo/.node`. Its agents run
on your **local model** through Apollo's local-model proxy (`/lmproxy/v1`).

```bash
# in .env
PAPERCLIP_ENABLED=true
PAPERCLIP_AUTH_SECRET=$(openssl rand -hex 32)   # paste the generated value

docker compose --profile paperclip up -d --build
```

On first open, Paperclip shows a one-time **claim** screen to create its admin
account. Then add an agent with the **`opencode-local`** adapter and a model id
like `openai/<your-ollama-model>`. A normal `docker compose up` (without the
profile) is unaffected.

Open it from the **Paperclip** sidebar tab. Apollo shows three views: **Floor**
(the Apollo-native animated isometric office where agents appear as Lego-like
minifigs with their own desks; see [docs/paperclip-floor.md](docs/paperclip-floor.md)),
**Board** (the same state as a Kanban work board), and **Classic** (Paperclip's
own UI). The Floor renders live data from `/api/paperclip/stream` (SSE, fed by
`POST /api/paperclip/events`); it plays a demo preview until agent activity
starts.

#### Apollo Ralph loop (optional)

An opt-in Ralph-style loop inspired by
[Ralph for Claude Code](https://github.com/frankbria/ralph-claude-code) and
[snarktank/ralph](https://github.com/snarktank/ralph). It does not run unless you
invoke it, and keeps state under `.apollo/ralph/`.

```bash
scripts/apollo-ralph init
scripts/apollo-ralph status
scripts/apollo-ralph next --prompt
scripts/apollo-ralph run-once --agent-cmd "claude --print" --auto-mark
```

The loop uses `prd.json` (user stories, deps, priorities, pass state),
`progress.md` (append-only learning), and `AGENTS.learning.md` (durable notes).
`--auto-mark` requires both the configured quality check (`./scripts/check.sh`)
and an explicit `EXIT_SIGNAL: true` from the agent. Stories may also require a
`verificationCommand` (e.g. `scripts/check-paperclip-browser --base-url http://127.0.0.1:7000`).

Use the shared workbench status for one readiness view across Paperclip, the
embedded browser, browser-use, crawl4ai, and Ralph:

```bash
scripts/apollo-integrations agent-workbench --pretty
```

#### Embedded browser panel and agent tool

Apollo is a FastAPI/static-web app, not an Electron shell, so the IDE browser is
a **live interactive screencast** of the shared server-side Playwright Chromium:
frames stream over a WebSocket (`/api/browser/ws`, CDP `Page.startScreencast`)
onto a canvas, and your clicks, scrolls, and keystrokes are forwarded back —
every site works, including ones that block iframes (X-Frame-Options / CSP
frame-ancestors). Because the session is shared with the agent's `browser`
tool, you can watch and assist while an agent browses. Open the panel from
**Browser** in the sidebar; click the page to interact, Esc releases focus.

Agent mode exposes a native `browser` tool:

```json
{"action":"navigate","url":"http://localhost:3000"}
{"action":"getVisibleText"}
{"action":"click","selector":"button[type=submit]"}
{"action":"type","selector":"input[name=q]","text":"Apollo"}
{"action":"screenshot","full_page":true}
```

The same contract is available over HTTP under `/api/browser/*` (`/navigate`,
`/current`, `/html`, `/text`, `/execute`, `/screenshot`, `/wait`, `/click`,
`/type`, `/events`, `/detect-localhost`). Only `http://` and `https://`
navigation is allowed; `file://`, `javascript:`, `data:`, Chromium internal
URLs, and Node/Electron-style schemes are blocked. Install the runtime when
agent browser control is needed:

```bash
pip install playwright
python -m playwright install chromium
```

#### browser-use and crawl4ai

Install crawl4ai with Apollo's main dependencies, then install browser-use into
its isolated environment:

```bash
pip install -r requirements.txt
scripts/setup-browser-use-env
```

Verify Paperclip's animated Floor from a real browser agent (defaults to
Apollo's local OpenAI-compatible proxy, so no `BROWSER_USE_API_KEY` is needed):

```bash
scripts/check-paperclip-browser --base-url http://127.0.0.1:7000
```

Import a web source into the research library as clean Markdown:

```bash
scripts/apollo-research crawl https://example.com --owner <apollo-user>
```

API equivalents: `GET /api/integrations/agent-workbench/status`,
`GET /api/research/crawl4ai/status`, `POST /api/research/crawl4ai/crawl`. For
safety, crawl4ai URL imports block private, loopback, link-local, reserved, and
non-HTTP(S) targets by default; set `APOLLO_CRAWL4AI_ALLOW_PRIVATE=true` only for
trusted local development.

#### Docker bundled services, GPU overlays, Ollama, checks

**Bundled services.** Compose starts Apollo, ChromaDB, SearXNG, and ntfy, all
bound to `127.0.0.1`. Native installs use Apollo's managed SearXNG sidecar
(installed via `scripts/setup-searxng.sh` / `.ps1`) instead of the Docker
instance.

**Cookbook storage in Docker.** Downloads live in `./data/huggingface`; installed
CLIs/serve engines live in `./data/local`, so they survive container recreation.

**Docker GPU overlays.** Cookbook can only detect GPUs Docker exposes to the
container. For NVIDIA, `scripts/check-docker-gpu.sh` diagnoses passthrough and can
install the host runtime or write `COMPOSE_FILE=docker-compose.yml:docker/gpu.nvidia.yml`
to `.env` (only after passthrough succeeds; the app never edits `.env` on its
own). For AMD/ROCm, run `scripts/check-docker-amd-gpu.sh` and add
`docker/gpu.amd.yml` plus your `RENDER_GID`. Verify with
`docker compose exec apollo nvidia-smi -L`.

> **GPU passthrough ≠ llama.cpp CUDA.** `nvidia-smi` passing inside the container
> confirms Docker GPU access, but llama.cpp also needs `cudart` and the CUDA
> Toolkit at runtime. If Cookbook logs show `Unable to find cudart library` or
> tensors on CPU, re-install the serve engine via **Cookbook → Dependencies** for
> a CUDA-enabled build.

**Ollama with Docker.** Add `http://host.docker.internal:11434/v1` in Settings,
and run Ollama with `OLLAMA_HOST=0.0.0.0:11434 ollama serve` so it listens
outside its own loopback. This connects Apollo to a host Ollama; it does not
start Ollama in the container.

**Useful checks.**

```bash
docker compose ps
docker compose logs --tail=120 apollo
docker compose logs apollo | grep -E 'ChromaDB|MemoryVectorStore|DEGRADED'
```

**macOS notes.** `start-macos.sh` runs on `7860` (AirPlay holds `7000`). It uses
llama.cpp/Ollama for Metal; vLLM/SGLang are CUDA/ROCm-only and do not run on
macOS; MLX-only models are not served by Apollo.

</details>

## Architecture

Apollo is a **three-tier system** designed so a skilled engineer could rebuild it.
The load-bearing patterns are below; the full reconstruction spec lives in
[docs/recreation/](#recreation--full-technical-docs).

```
┌──────────────────────────────────────────────────────────────────────────┐
│  TIER 1 — FRONTEND  (static/, vanilla ES modules, NO build step)           │
│  index.html · app.js · ~90 static/js/*.js modules (chat.js, research/,     │
│  editor/, compare/, cookbook*, paperclip.js, browserPanel.js, settings.js, │
│  voiceCall.js, vad.js, graph tab, memory panel)                            │
│  Served by Starlette StaticFiles; SSE + WebSocket for live streams; PWA.   │
└───────────────┬──────────────────────────────────────────────────────────┘
                │  HTTP / SSE / WebSocket  (cookie session or Bearer ody_… token)
┌───────────────▼──────────────────────────────────────────────────────────┐
│  TIER 2 — BACKEND  (FastAPI, one uvicorn process — app.py)                  │
│   middleware (outer→inner): CORS → SecurityHeaders → RequestTimeout(45s)   │
│                              → AuthMiddleware                              │
│   routes/ (~53 *_routes.py)  →  src/ (handlers, managers, agent loop)      │
│                              →  services/ (search, searxng, browser,       │
│                                 localmodels, paperclip, memory, research,  │
│                                 review, skills, stt, tts)                  │
│                              →  core/ (database, auth, session, middleware)│
└───────┬───────────────────────────────────────────────┬──────────────────┘
        │                                                │
┌───────▼─────────────────┐                  ┌───────────▼──────────────────┐
│ TIER 3 — DATA            │                  │  SIDECARS / SUBPROCESSES      │
│  SQLite  data/app.db     │                  │  llama-server (GGUF, on-demand)│
│   (SQLAlchemy, ~30 models)│                 │  SearXNG (Python venv, :8893) │
│  ChromaDB  data/chroma/  │                  │  Paperclip (Node, opt-in)     │
│   (RAG + semantic memory)│                  │  Playwright Chromium / ntfy   │
│  JSON  sessions/settings │                  │  Voicebox (opt-in, :17493)    │
└─────────────────────────┘                  └───────────────────────────────┘
```

**Tier 1 — Frontend (`static/`).** `index.html` + `app.js` plus ~90 ES modules under
`static/js/`. No bundler or transpiler — browsers load raw `.js` modules. Cache discipline
is server-side: `.js/.css/.html` are served `Cache-Control: no-cache` so a code change
appears without a hard refresh. Installable PWA (`manifest.json`, `sw.js`). Theming is
pure CSS variables + `color-mix` tokens (24 presets + a full custom theme editor). Voice
call mode (`voiceCall.js`) is split into a pure state machine and browser wiring so the
machine is unit-testable in Node.

**Tier 2 — Backend (FastAPI).** `app.py` is a slim orchestrator: it builds the middleware
stack, constructs managers once via `initialize_managers()` (`src/app_initializer.py`), and
registers ~40 routers. The actual logic lives in `routes/`, `src/`, `services/`, and `core/`
with a strict dependency direction (`routes → src/services → core`, no cycles).

**Tier 3 — Data.** SQLite via SQLAlchemy (`core/database.py`, `check_same_thread=False`,
`PRAGMA foreign_keys=ON`), ~30 ORM models (`Session`, `ChatMessage`, `Document`,
`ModelEndpoint`, `McpServer`, `ApiToken`, `Webhook`, `ScheduledTask`, `Memory`, `Note`,
`CalendarEvent`, `EmailAccount`, …). ChromaDB holds vectors for personal-doc RAG and
semantic memory (and now the knowledge graph's semantic edges) — embedded on-disk natively,
`HttpClient` when `CHROMADB_HOST` is set (Docker). Local ONNX embeddings via **fastembed**
(`sentence-transformers/all-MiniLM-L6-v2`).

**Key patterns (the parts you'd need to get right when recreating it):**

- **Router factories with labeled registration.** Every feature is
  `setup_<name>_routes(...) -> APIRouter`, registered in `app.py` through
  `build_and_include_router(app, "Label", factory, *deps)` / `RouterSpec` (in
  `services/app_startup.py`) so a broken feature fails loudly at startup with its label.
  Dependencies are injected — routes never construct globals.
- **One auth middleware, explicit exemptions.** `AuthMiddleware` is installed only when
  `AUTH_ENABLED != "false"`. It supports cookie sessions, optional TOTP 2FA, `ody_` bearer
  API tokens (bcrypt-checked against a prefix-keyed in-memory cache), and a loopback
  internal-tool token. Self-authenticating endpoints (`/lmproxy/*`, task webhooks,
  `/api/paperclip/events`) are auth-exempt and prove identity themselves. WebSockets bypass
  HTTP middleware, so the Paperclip and browser WS proxies validate the session cookie
  explicitly. Loopback trust is hardened: a request with any proxy-forwarding header
  (`x-forwarded-for`, `cf-connecting-ip`, …) is not treated as trusted loopback.
- **Model roles resolve with fallback.** `src/endpoint_resolver.resolve_endpoint("<role>")`
  reads `{role}_endpoint_id` / `{role}_model` from settings and dispatches to a
  `ModelEndpoint`. Roles like `reviewer` and `utility` fall back to the default chat endpoint
  when unset — so the adversarial reviewer works with just a default model, and the web
  auto-decider deliberately refuses to run unless `utility_endpoint_id` is explicitly set.
- **Local models: scan → catalog → single warm slot.** `services/localmodels/scanner.py`
  walks configured dirs for GGUFs (skipping AppleDouble `._` files, split parts, mmproj);
  the registry syncs a deduped name list into the picker; `server_manager.ensure_running(model)`
  swaps one warm `llama-server` subprocess (context = `min(model window, APOLLO_LLAMA_CONTEXT=16384)`).
  A stable `/lmproxy/v1` OpenAI-compatible proxy forwards to whichever model is warm
  (consumed by Paperclip and browser-use agents).
- **Streaming.** Chat and the Floor both use SSE: `data:` JSON frames, an `event: error`
  channel with status codes, `[DONE]` terminators. Client disconnects are handled with a
  guarded partial-save so a save failure never masks the `CancelledError`.
- **Web access pipeline.** Tri-state `web_access` (`off | auto | always`, `src/web_decider.py`).
  **Auto** runs an instant regex `heuristic_decision()` → `yes | no | ambiguous`, with an
  async tie-break for the ambiguous case. The provider chain (`services/search/`) tries the
  managed SearXNG sidecar → DuckDuckGo → user-added Brave/Tavily/Serper/Google PSE, tags each
  result with its provider, and skips a down sidecar with no timeout penalty.
- **Managed SearXNG sidecar.** `services/searxng/` runs a git checkout of `searxng/searxng`
  in its own venv under `data/searxng/`, bound to `127.0.0.1:8893`. `SearxngRuntime`
  supervises it with a 2s health TTL (fail-closed on `/healthz`), a 300s restart-cooldown
  watchdog, and log truncation to `logs/searxng.log`. Started in a daemon thread on the
  `startup` event; reuses an already-running instance.
- **Voice pipeline.** `services/stt/stt_service.py` (multi-provider STT: local
  **faster-whisper**, an OpenAI-compatible `/audio/transcriptions` endpoint, browser Web
  Speech, or **Voicebox**) and `services/tts/tts_service.py` (multi-provider TTS: local
  **Kokoro-82M** / **Piper**, an OpenAI-compatible `/audio/speech` endpoint, browser, or
  **Voicebox**) sit behind `/api/stt/*` and `/api/tts/*`. TTS output is cached on disk. The
  hands-free call loop lives entirely in the frontend state machine + VAD gate.
- **Second brain + graph.** `services/memory/distiller.py` extracts atomic facts via an
  injected LLM caller; `brain.py` dedups against existing memories and vector-indexes new
  ones; `chat_import.py` parses ChatGPT (`mapping`) and Claude (`conversations`) exports.
  `services/memory/graph.py` builds a bounded, deterministic graph (semantic neighbors +
  same-session chains) served at `/api/memory/graph`.
- **Skill-pack installer.** `services/skills/pack_installer.py` fetches a GitHub tarball
  behind SSRF, size (50 MB), member-count (5,000), and traversal/symlink guards (Python
  `filter="data"`), discovers `SKILL.md` files, classifies each as `prose` or `script`, and
  installs into the skills store — publishing prose skills and quarantining script-backed
  ones as drafts, with import provenance stamped in.
- **Agent-subprocess env scrub.** `src/subproc_env.build_agent_env()` gives every
  agent-reachable child process (bash/python tools, background jobs, shell service, MCP stdio
  servers) a minimal **allowlisted, default-deny** environment plus a secret-shaped denylist
  scrub, mirroring the SearXNG sidecar allowlist pattern.
- **Embedded browser.** `services/browser/embedded_browser.py` drives a shared Playwright
  Chromium page; the UI is a canvas screencast over `/api/browser/ws`. A scheme allowlist
  (`http`/`https` only) with a hard `BLOCKED_SCHEMES` set guards against SSRF/exfiltration.
- **Paperclip pipeline.** One bounded `EventHub` (replay deque + seq watermark,
  drop-don't-backpressure) is fed by HTTP ingest, a reconnecting WS collector against
  Paperclip's live events, and per-agent lmproxy activity pulses, then drained by
  `/api/paperclip/stream`. The Floor UI keeps all layout in logical 0-100 coordinates and
  projects to an isometric SVG stage at render time, with depth-sorted true occlusion.
- **Packaging is two-flavor.** `build-macos-app.sh` produces a launcher `Apollo.app`/`Apollo.dmg`
  that drive this repo's venv (install path baked at build time); `build-macos-bundle.sh` +
  `packaging/apollo.spec`/`apollo_boot.py` produce a **self-contained PyInstaller** bundle that
  runs without the repo. A small C launcher (`scripts/windows-launcher/`) cross-compiles to
  `Apollo.exe`, which opens `launch-windows.ps1` beside it.

**Stack:** Python 3.11+ · FastAPI/Starlette/uvicorn · httpx (all outbound HTTP incl. streaming
proxies) · websockets · SQLAlchemy/SQLite · ChromaDB + fastembed · llama.cpp · faster-whisper ·
Kokoro/Piper TTS · PyInstaller (bundle build) · vanilla ES-module JS · node:test + pytest. Full
dependency rationale lives in `requirements.txt` comments and
[docs/recreation/TECHNOLOGY-AUDIT.md](docs/recreation/TECHNOLOGY-AUDIT.md).

## Configuration

Most setup is done inside the app with `/setup` or **Settings**. Use `.env`
for deployment-level defaults and secrets you want present before first boot.
Key settings:

| Variable | Default | Description |
|---|---|---|
| `LLM_HOST` | `localhost` | Your LLM server (e.g. `llm-host.local:8000`) |
| `LLM_HOSTS` | -- | Comma-separated list for model discovery |
| `OPENAI_API_KEY` | -- | Optional OpenAI key. Prefer adding providers in the app unless pre-seeding. |
| `SEARXNG_INSTANCE` | `http://localhost:8080` | SearXNG URL override. Docker sets this to `http://searxng:8080`. In native mode the managed sidecar on `searxng_port` (default 8893) is used automatically — only set this to point at a separate instance. |
| `SEARXNG_GIT_REF` | pinned commit | Override the SearXNG checkout ref used by the setup scripts. |
| `web_access_mode` (Settings) | `manual` | Admin default for the per-chat off/auto/always toggle. |
| `APP_BIND` | `127.0.0.1` | Docker Compose host bind address. Use `0.0.0.0` only for intentional LAN/reverse-proxy access. |
| `APP_PORT` | `7000` | Docker Compose host port for the web UI (`7860` for `start-macos.sh` / desktop builds). |
| `AUTH_ENABLED` | `true` | Enable/disable login |
| `LOCALHOST_BYPASS` | `false` | Development-only auth bypass for loopback requests. Keep false for shared/network deployments. |
| `SECURE_COOKIES` | `false` | Set true when serving Apollo through HTTPS at a trusted proxy or private access gateway. |
| `DATABASE_URL` | `sqlite:///./data/app.db` | Database connection string |
| `CHROMADB_HOST` | -- | Optional trusted external ChromaDB host. Docker uses the embedded persisted store by default. |
| `CHROMADB_PORT` | -- | Port for an explicitly configured external ChromaDB host. |
| `FASTEMBED_MODEL` | `sentence-transformers/all-MiniLM-L6-v2` | Local ONNX embedding model. |
| `EMBEDDING_URL` | -- | Optional OpenAI-compatible embeddings endpoint |
| `PAPERCLIP_ENABLED` | `false` | Enable the Paperclip agent-management sidecar. |

Provider endpoints and search-provider keys are added **in the app**, not `.env`:

- **Model providers** (OpenAI / Anthropic / OpenRouter / Groq / Ollama / any OpenAI-compatible)
  are added in **Settings → AI**; each is a `ModelEndpoint`. **Model roles** —
  `default`/`chat`, `utility`, `research`, `task`, and **`reviewer`** (for the adversarial
  reviewer) — are assigned there via `{role}_endpoint_id` / `{role}_model` settings and fall
  back sensibly when unset.
- **Local-model folders** for GGUF auto-scan are configured per-user in **Settings → AI**.
- **Search-provider keys** (Brave / Tavily / Serper / Google PSE) are added in the app.
- **Voice** is configured in **Settings**: `stt_provider` / `tts_provider`
  (`disabled | browser | local | endpoint:<id>`, plus `voicebox` and — for TTS — `piper`),
  `stt_model` (Whisper size, default `base`), `tts_model` / `tts_voice` / `tts_speed`, and
  `voicebox_url` (default `http://127.0.0.1:17493`).

The full configuration reference is
[docs/recreation/09-configuration-environment-variables.md](docs/recreation/09-configuration-environment-variables.md).

### Built-in MCP servers (optional setup)

Apollo auto-registers a few built-in MCP servers at startup. The npx-based ones (currently
the browser server, `@playwright/mcp`) only start when their npm package is already in the
local npx cache; otherwise that server is skipped with a startup log message, so a fresh
install never blocks on a multi-minute npm download. To enable the browser MCP (navigation,
screenshots, vision), run once, then restart Apollo:

```bash
npx -y @playwright/mcp@latest --version
```

## Project layout

```
Apollo/
├── app.py                 # Slim FastAPI orchestrator: middleware, manager init, ~40 routers
├── setup.py               # First-run: data dirs, DB, admin account + temp password
├── requirements*.txt      # Core / optional / browser-use dependency sets
├── docker-compose.yml     # apollo + embedded vector store + searxng + ntfy + paperclip(+db) profile
├── start-macos.sh         # Native macOS quick-start (venv + brew + uvicorn)
├── launch-windows.ps1     # Native Windows launcher
├── build-macos-app.sh     # Launcher build: dist/Apollo.app + Apollo.dmg (drives repo venv)
├── build-macos-bundle.sh  # Self-contained PyInstaller build (packaging/apollo.spec)
├── packaging/             # apollo.spec + apollo_boot.py (PyInstaller bundle)
├── routes/   (53 *.py)    # HTTP boundary — APIRouter factories (setup_<name>_routes)
├── src/      (79 *.py)    # App logic, managers, handlers, agent loop, subproc_env, resolver
├── services/             # Self-contained subsystems (search, searxng, browser, localmodels,
│                         #   paperclip, memory, research, review, skills, stt, tts)
├── core/     (10 *.py)    # Cross-cutting primitives (database, auth, session, middleware)
├── static/               # Vanilla-JS frontend (ES modules, no build) + style.css
├── mcp_servers/          # Built-in MCP servers (email, image-gen, memory, rag)
├── scripts/              # apollo-* CLIs + setup/maintenance scripts
├── tests/                # pytest suite + node:test .mjs suites
├── docs/                 # Landing page, OPERATIONS.md, recreation/ spec
├── data/     (gitignored) # SQLite, ChromaDB, JSON state, model caches, tts_cache
└── dist/                 # Built desktop artifacts (Apollo.app, Apollo.dmg)
```

The full annotated breakdown is
[docs/recreation/15-file-structure-code-organization.md](docs/recreation/15-file-structure-code-organization.md).

### Data

All user data lives in `data/` (gitignored): `app.db` (SQLite: sessions, messages,
documents, model endpoints, MCP servers, notes, calendars, scheduled tasks, gallery,
memories, email accounts, API tokens, webhooks), `auth.json` (users/2FA),
`user_prefs.json` (per-user prefs incl. custom themes and local-model folders),
`settings.json` (admin/service settings incl. voice + model-role config),
`memory.json`, `presets.json`, `uploads/`, `personal_docs/`, `chroma/` (vector memory),
`tts_cache/`. Generated secrets live in `~/.apollo/` with 0600 permissions (Paperclip auth
secret, proxy token, per-agent tokens).

## Recreation — full technical docs

The [`docs/recreation/`](docs/recreation/) directory contains **15 deep technical
documents plus a technology audit** intended to let a skilled engineer reconstruct
Apollo from scratch — each grounded in real file paths and line references:

| # | Document |
|---|---|
| 01 | [Project Overview & Architecture](docs/recreation/01-project-overview-architecture.md) |
| 02 | [Environment Setup & Dependencies](docs/recreation/02-environment-setup-dependencies.md) |
| 03 | [Database Schema & Data Models](docs/recreation/03-database-schema-data-models.md) |
| 04 | [Backend API Specifications](docs/recreation/04-backend-api-specifications.md) |
| 05 | [Frontend Architecture & Components](docs/recreation/05-frontend-architecture-components.md) |
| 06 | [Authentication & Authorization](docs/recreation/06-authentication-authorization.md) |
| 07 | [Business Logic & Core Algorithms](docs/recreation/07-business-logic-core-algorithms.md) |
| 08 | [Integrations & External Services](docs/recreation/08-integrations-external-services.md) |
| 09 | [Configuration & Environment Variables](docs/recreation/09-configuration-environment-variables.md) |
| 10 | [Testing Strategy & Test Cases](docs/recreation/10-testing-strategy-test-cases.md) |
| 11 | [Build & Deployment Pipeline](docs/recreation/11-build-deployment-pipeline.md) |
| 12 | [Error Handling & Logging](docs/recreation/12-error-handling-logging.md) |
| 13 | [Performance Optimization & Caching](docs/recreation/13-performance-optimization-caching.md) |
| 14 | Security Implementation *(kept local-only — enumerates specific residual weaknesses; not published to this public repo)* |
| 15 | [File Structure & Code Organization](docs/recreation/15-file-structure-code-organization.md) |
| — | [Technology Audit](docs/recreation/TECHNOLOGY-AUDIT.md) |

## Testing & Build

`scripts/check.sh` is the single quality gate: `compileall` (fast syntax check) + `pytest` +
`npm test` (node:test `.mjs` suites, incl. the voice VAD/call-machine and graph-layout tests).
Run the suites directly with:

```bash
venv/bin/python -m pytest -q          # full pytest suite
npm run test:js                       # frontend node:test suites (aliased by `npm test`)
scripts/check.sh                      # everything (the CI gate)
```

CI (`.github/workflows/ci.yml`) runs on Python 3.12: it installs `requirements.txt`,
runs `python -m compileall` over the source tree, then `python -m pytest -q`. Desktop
artifacts are built with `./build-macos-app.sh` (launcher `.app`/`.dmg`) or
`./build-macos-bundle.sh` (self-contained PyInstaller `.app`/`.dmg`); the Windows launcher
(`launch-windows.ps1`) and Linux `systemd` install (`install-service.sh`, `apollo-ui.service`)
cover the other platforms. Full pipeline details are in
[docs/recreation/11-build-deployment-pipeline.md](docs/recreation/11-build-deployment-pipeline.md).

## Security & threat model

Apollo is a self-hosted workspace with powerful local tools: shell access, file uploads,
model downloads, web research, email/calendar integrations, skill-pack imports, and API
tokens. **Treat it like an admin console.** The essentials:

- Keep `AUTH_ENABLED=true` for any network-accessible deployment, and `LOCALHOST_BYPASS=false`
  outside local development.
- Use `SECURE_COOKIES=true` when Apollo is served over HTTPS by a trusted reverse proxy or
  private access gateway. Do not expose the raw app port to the public internet.
- Keep `.env`, `data/`, `logs/`, databases, uploads, generated media, backups, and
  auth/session/token files out of Git and shared drives (they are gitignored by default).
  Before publishing a fork, run `git status --short` and confirm none are staged.
- Review `data/auth.json` after first boot: disable open signup unless intended, keep only
  your own account admin, and keep demo/test accounts non-admin. Non-admin users get no
  shell/Python/file access by default, and admin-only routes/tools (MCP management, API
  tokens, webhooks, model/cookbook serving, **skill-pack install**, backup/vault, settings)
  are admin-gated.
- **Agent subprocesses are secret-scrubbed.** Tools the agent can reach (`bash`/`python`,
  background jobs, the shell service, MCP stdio servers) run with an allowlisted, default-deny
  environment (`src/subproc_env.py`) so provider keys, `DATABASE_URL`, and SMTP/IMAP passwords
  never leak into a child process.
- **Imported skill packs are quarantined.** Script-backed skills from a GitHub pack are
  installed as **drafts** (never auto-run); only prose skills are published. The fetch path is
  SSRF-, zip-bomb-, and tar-symlink-guarded.
- Keep ChromaDB, SearXNG, ntfy, Ollama, vLLM, llama.cpp, Voicebox, databases, and raw
  model/provider APIs internal-only. Expose only the authenticated Apollo entrypoint through
  your proxy.

The full policy and threat analysis are in [SECURITY.md](SECURITY.md) and
[THREAT_MODEL.md](THREAT_MODEL.md). (A detailed security-implementation walkthrough
that enumerates specific residual weaknesses is maintained locally and intentionally
not published in this public repository.) For runtime health checks, startup
diagnostics, and recovery order, see [docs/OPERATIONS.md](docs/OPERATIONS.md).

### Private or proxied deployments

Apollo serves plain HTTP on its app port; Docker Compose binds it and the bundled services to
`127.0.0.1`. A typical private setup: keep Apollo on localhost (e.g. `127.0.0.1:7000`),
terminate HTTPS at a trusted reverse proxy or private access gateway, put the authenticated
Apollo entrypoint behind it, and keep raw service/model ports internal-only. Cloudflare
Access, Tailscale, Caddy, nginx, and Traefik all fit this pattern; none are required. When
proxying, keep `AUTH_ENABLED=true`, `LOCALHOST_BYPASS=false`, and `SECURE_COOKIES=true`.

Common internal-only ports from the default setup:

| Port | Service |
|---|---|
| `7000` | Apollo raw app port (`7860` for `start-macos.sh` / desktop builds) |
| `8080` | SearXNG (Docker-bundled instance) |
| `8091` | ntfy |
| `8893` | SearXNG managed sidecar (native installs; `searxng_port` setting) |
| `11434` | Ollama |
| `17493` | Voicebox (opt-in TTS/STT engine; `voicebox_url`) |
| `8000-8020` | Common local model/provider APIs |

## Contributing

Help is welcome. The best entry points are fresh-install testing, provider setup bugs,
mobile/editor polish, docs, and small focused refactors. See [CONTRIBUTING.md](CONTRIBUTING.md)
for setup and PR guidelines, and [ROADMAP.md](ROADMAP.md) for the current help-wanted list.

## Star History

<a href="https://www.star-history.com/?repos=Antman1526%2FApollo&type=date&legend=top-left">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/chart?repos=Antman1526/Apollo&type=date&theme=dark&legend=top-left" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/chart?repos=Antman1526/Apollo&type=date&legend=top-left" />
   <img alt="Star History Chart" src="https://api.star-history.com/chart?repos=Antman1526/Apollo&type=date&legend=top-left" />
 </picture>
</a>

## License & Acknowledgments

MIT -- see [LICENSE](LICENSE). Apollo is a renamed distribution of
**[Odysseus](https://github.com/pewdiepie-archdaemon/odysseus)** by
**pewdiepie-archdaemon**; all original work is theirs. See
[ACKNOWLEDGMENTS.md](ACKNOWLEDGMENTS.md) for full credits to Odysseus and the
upstream projects Apollo builds on (opencode, llmfit, Tongyi DeepResearch,
Paperclip, browser-use, crawl4ai, SearXNG, and more).

```
                                  |
                                 |||
                                |||||
                  |    |    |   |||||||
                 )_)  )_)  )_)   ~|~
                )___))___))___)\  |
               )____)____)_____)\\|
             _____|____|____|_____\\\__
             \                       /
       ~^~^~~^~^~~^~^~~^~^~~^~^~~^~^~~^~^~~^~^~
               ~^~  all aboard!  ~^~
       ~^~^~~^~^~~^~^~~^~^~~^~^~~^~^~~^~^~~^~^~
```
