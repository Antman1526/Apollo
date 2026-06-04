# Local Models — Seamless Integration Design

**Date:** 2026-06-03
**Status:** Approved (design), pending implementation plan
**Scope:** Make on-disk GGUF chat + embedding models in user-configured
directories appear in Apollo's model picker and run automatically when
selected.

## Goal

Point Apollo at one or more local model directories. Discovered GGUF
**chat** and **embedding** models appear in the normal chat model picker.
Selecting one **auto-launches `llama-server`** behind it and routes the
chat there — no manual serve step. Only one chat model stays warm at a
time (the previous is stopped) to respect RAM; the lightweight embedding
server runs independently.

Initial target directories (configurable, default-seeded):
- `/Volumes/MainStore/Development/AI_Models`
- `/Users/Antman/Desktop/AI_Models`

## Non-goals (YAGNI)

- STT / TTS integration (Whisper, Piper) — deferred to a later spec.
- Remote-host serving — the existing Cookbook (`routes/cookbook_*.py`)
  already does tmux/remote serving; this feature is local-only.
- Multiple concurrent warm chat models — single warm chat model only.
- Replacing or duplicating the Cookbook. We **reuse** its GGUF metadata
  parsing and binary-discovery logic.

## Background: how Apollo handles models today

Apollo is **endpoint-centric**:

- `model_endpoints` DB table (`core/database.py:326`) is the registry:
  `name`, `base_url`, `api_key`, `is_enabled`, `cached_models` (JSON),
  `model_type`, `owner`, etc.
- The picker endpoint `/api/models` (`routes/model_routes.py:645`) reads
  enabled endpoints and returns their `cached_models`.
- Chat routing resolves an endpoint via `src/endpoint_resolver.py`
  (`resolve_endpoint()`, ~line 205) → `(base_url, model_name, headers)`,
  then `src/llm_core.py` dispatches an OpenAI-compatible request.
- The Cookbook can already launch `llama-server --model X.gguf --port P`
  (`routes/cookbook_routes.py:949`) and scan a directory for `.gguf`
  files with quant/role metadata (`routes/cookbook_helpers.py:233-375`).

Gap: directory scanning is on-demand only (per-request `model_dir`
param), not persisted, not run on startup, and LLM GGUFs are never
auto-registered or auto-served. This feature closes that gap.

Tooling confirmed present on host: `llama-server`, `llama-cli`,
`ollama`, `python3` (Homebrew, on PATH).

## Architecture

One **managed "Local (llama.cpp)" endpoint** row represents all local
models, so they flow through the existing `/api/models` picker with no
new aggregation plumbing. Its `cached_models` list is the discovered
catalog. A routing special-case lazily starts the right `llama-server`
when a local model is actually used.

### Components (isolated, single-purpose)

1. **`services/localmodels/scanner.py`**
   - Scans configured dirs (recursively, sensible depth) for `*.gguf`.
   - Reuses GGUF metadata parsing from `routes/cookbook_helpers.py`
     (quant format, role) — extract a shared helper if needed rather
     than duplicating regex.
   - Produces catalog entries:
     `{id, name, path, quant, kind: "chat"|"embedding", size_bytes, dir}`.
   - Classification: name matches `embed`/`nomic` → `embedding`, else
     `chat`. `mmproj`/projector files are skipped (or paired later).
   - Skips macOS `._*` AppleDouble sidecars and unreadable/unmounted
     dirs (logs a warning, never raises).
   - Stable `id` derived from the file path (so start/stop and the
     picker agree across rescans).

2. **`services/localmodels/server_manager.py`**
   - `ensure_running(model_id) -> base_url`: if not already serving,
     pick a free localhost port, launch
     `llama-server --model <path> --host 127.0.0.1 --port <p>`
     (+ `--embedding` for embedding models, sensible `-c` context),
     wait for `/health` to report ready, return `http://127.0.0.1:<p>/v1`.
   - **Single warm chat model:** starting a chat model stops the
     currently-warm chat model first. Embedding server is tracked
     separately and not evicted by chat changes.
   - `stop(model_id)`, `stop_all()`, `status() -> {model_id: {port,
     pid, state}}`.
   - Binary discovery reuses the Cookbook's search
     (`$PATH`, `/opt/homebrew/bin`, `~/llama.cpp/build/bin`, etc.).
   - In-memory process registry; best-effort reconcile/cleanup of
     orphaned servers on startup and on shutdown (`stop_all`).

3. **Settings**
   - Add persistent `local_model_dirs: list[str]` to settings
     (`src/settings.py` schema + `data/settings.json`).
   - Seeded from env `APOLLO_MODELS_DIRS` (comma-separated) on first
     run; default seed = the two target paths above. Add to
     `.env.example`.
   - Editable via the Local Models settings panel.

4. **`routes/localmodels_routes.py`**
   - `GET  /api/local-models` — discovered catalog + per-model serve
     status.
   - `POST /api/local-models/scan` — rescan now.
   - `GET/PUT /api/local-models/dirs` — read/update configured dirs.
   - `POST /api/local-models/{id}/start` and `/stop` — manual control.
   - **Startup hook:** non-blocking background scan on app boot that
     populates the managed endpoint's `cached_models`.

5. **Routing hook** (`src/endpoint_resolver.py` + chat dispatch)
   - When the resolved endpoint is the managed-local one, call
     `server_manager.ensure_running(model)` to obtain the live base_url
     before dispatch. First message spins the server up; later messages
     reuse the warm process. Optional idle auto-stop timer (later).

6. **Frontend**
   - Local models appear under a **"Local"** group in the existing chat
     model dropdown (driven by the managed endpoint's `cached_models`).
   - **Settings → Local Models** panel: list/add/remove directories,
     "Rescan" button, per-model rows with status + Start/Stop, and an
     optional "What Fits?" hint reusing the existing `services/hwfit`
     service.
   - A "Loading model…" indicator while a server is first warming up.

## Data flow (auto-serve on select)

1. Boot → background scan of configured dirs → catalog cached → managed
   endpoint `cached_models` populated.
2. `/api/models` returns local models under the managed endpoint → picker
   shows them under "Local".
3. User selects `Qwen3.5-9B (local)` as the session model.
4. First chat request → resolver detects managed-local → `ensure_running`:
   stop previous warm chat model, pick free port, launch llama-server,
   wait for `/health`.
5. Chat dispatched to `http://127.0.0.1:<port>/v1`; warm for subsequent
   requests.
6. `nomic-embed` is surfaced as an embedding model and, when selected as
   Apollo's embeddings endpoint, serves on its own port with
   `--embedding`. (It is made available/selectable, not silently
   force-wired into RAG.)

## Error handling

- **Missing `llama-server` binary** → clear, actionable error (point to
  Cookbook build flow which already exists).
- **Load failure / OOM** → capture `llama-server` stderr, surface a
  concise message, mark model stopped.
- **Port conflict** → retry with next free port.
- **Directory unavailable** (e.g., `/Volumes` unmounted) → skip with a
  warning; scan never crashes.
- **Health-check timeout** → kill the process and report.

## Testing

- **Unit**
  - Scanner: fixture directory of fake `.gguf` filenames →
    correct `kind` classification, quant extraction, sidecar/projector
    skipping, unmounted-dir tolerance.
  - Settings: `APOLLO_MODELS_DIRS` parsing + persistence round-trip.
  - server_manager: free-port selection and single-warm eviction logic
    with a stubbed launcher (no real process).
- **Integration (optional, gated behind an env flag)**
  - Actually serve the smallest model (`Llama-3.2-1B-Instruct-Q4_K_M`),
    hit `/health`, and request a 1-token completion.

## Open questions / future work

- Idle auto-stop timeout for the warm chat model (default off initially).
- Pairing `mmproj` projectors with multimodal base models.
- STT/TTS integration (separate spec).
- Optional GPU offload flags (`-ngl`) auto-tuned via hwfit profiles.
