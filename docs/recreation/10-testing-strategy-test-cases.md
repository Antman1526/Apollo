# 10 — Testing Strategy & Test Cases

## Test suite at a glance

```
$ ls tests/ | wc -l
273
$ find tests -name 'test_*.py' | wc -l
265
```

The `tests/` directory holds **265 `test_*.py` files** (plus JS test `.mjs` files,
fixtures, and `conftest.py`). Counting `def test_…` / `async def test_…` functions
gives roughly **~1,500–1,700 Python test cases** — a large, fast unit suite that runs
without a database, a model server, or a browser, because heavy dependencies are
stubbed at collection time.

### Categories (by filename prefix, top buckets)

| Area | Approx. files | Representative files |
|---|---|---|
| Search / web | 11+ | `test_search_*`, `test_searxng_*`, `test_web_decider*` |
| Research / deep research | 9 + 4 | `test_research_*`, `test_deep_*` |
| LLM core | 9 | `test_llm_*` |
| Local models | 8 | `test_localmodels_*`, `test_gguf_meta.py` |
| Paperclip | 7 | `test_paperclip_*` |
| Email / personal / calendar | 6+6+4 | `test_email_*`, `test_personal_*`, `test_calendar_*` |
| Cookbook (model serving) | 6 | `test_cookbook_*` |
| Chat / sessions / documents | 6+5+5 | `test_chat_*`, `test_session_*`, `test_document_*` |
| Skills / MCP / uploads | 5 each | `test_skills_*`, `test_mcp_*`, `test_upload_*` |
| Browser | — | `test_browser_ws.py` |

---

## Running the suite

Python tests use the in-repo virtualenv directly (no global pytest):

```bash
venv/bin/python -m pytest -q          # full suite, quiet
venv/bin/python -m pytest tests/test_web_decider.py -q   # one file
```

CI runs the same command (`.github/workflows/ci.yml:32`): `python -m pytest -q`.

JS tests use Node's built-in test runner (`package.json:8`). `npm run test:js`
expands to a `node --test <files…>` invocation, and individual `.mjs` files can
also be run directly with `node --test tests/<file>.mjs`:

```bash
npm run test:js
# → node --test tests/test_paperclip_floor_ui.mjs tests/test_system_status_card.mjs \
#          tests/test_system_status_actions.mjs tests/test_theme_presets.mjs \
#          tests/test_voice_vad.mjs tests/test_voice_call_machine.mjs \
#          tests/test_graph_layout.mjs …
```

Both runners are used side by side: **Python** via `venv/bin/python -m pytest`
(the whole suite or a single file) and **JS** via `node --test` /
`npm run test:js`. The two are independent — a JS-only change can be verified
with `node --test tests/test_voice_call_machine.mjs` without touching pytest.

---

## New feature test files (what each covers)

The features added on top of the base app ship with focused, DB-free unit tests
that follow the same seam-injection / tolerant-parsing patterns as the rest of
the suite. Grouped by feature:

### Security — agent subprocess env scrub

- **`tests/test_subproc_env.py`** — pins `src.subproc_env.build_agent_env`'s
  **allowlist + denylist** behavior: every secret-shaped var
  (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `DATABASE_URL`, `SMTP_PASSWORD`,
  `IMAP_PASSWORD`, `SEARXNG_SECRET`, `*_TOKEN`, `*_SECRET`) is stripped, the
  safe vars (`PATH`, `HOME`) survive, `extra=` constants and `passthrough=`
  opt-ins are honored — **but the denylist overrides passthrough** so a secret
  can't be opted back in. It then proves the *real* spawn paths don't leak by
  running `env` through each: the agent `bash` tool (`_direct_fallback`), the
  `ShellService`, MCP stdio child env (`_stdio_env`), a detached background job
  (`bg.launch`), the `run_local` builtin action, and the Ralph quality-check
  command — asserting no `sk-secret-openai` / `OPENAI_API_KEY` /
  `postgres://…` appears in captured output.

### Skill-pack installer

- **`tests/test_pack_installer.py`** — the pure library: `classify_tier`
  (prose vs script by presence of `scripts/`, `.mcp.json`, or loose code
  files), `discover_skills` (multi-skill walk; **PyYAML block-scalar
  descriptions parsed to real text**, not a literal `|`; a malformed
  `SKILL.md` is *reported, not fatal*), `render_skill_md` (prose →
  `status: published`, script → `status: draft` quarantine; provenance
  `imported_from`/`imported_ref`; body preserved verbatim), `install_skills`
  (writes `<root>/<category>/<name>/SKILL.md`, skips existing without
  `overwrite`, **sanitizes a `../../ESCAPED` category** so nothing lands outside
  `skills_root`), and the **`safe_extract_tar` guards** — path-traversal,
  symlink-escape, and member-count cap all raise `ValueError`.
- **`tests/test_skill_pack_routes.py`** — the FastAPI routes
  (`/api/skills/packs/preview` and `/install`) via `TestClient`, with
  `fetch_pack` stubbed to a local temp dir (no network) and `require_admin`
  bypassed: preview lists skills **without writing**, install writes the
  provenance-stamped `SKILL.md`, and selecting an absent name installs nothing.

### Second brain — distill / import / memory graph

- **`tests/test_distiller.py`** — `build_distill_prompt` (transcript embedded
  in the user turn), `parse_facts` (bullets/numbers/blank lines normalized;
  `NONE` / `(no durable facts)` markers → `[]`), and `distill_transcript` with
  an **injected LLM callable** so no model is needed.
- **`tests/test_chat_import.py`** — `parse_chatgpt_export` (mapping-tree),
  `parse_claude_export` (flat `chat_messages`), and `parse_export`
  auto-detection; garbage/non-JSON shapes return `[]` without raising.
- **`tests/test_brain.py`** — the `distill_and_store` orchestrator with fake
  `MemoryManager`/vector: de-dupes (both bool and `List[Dict]`
  `find_duplicates` returns), indexes only when the vector store is `healthy`,
  stamps `session_id` onto stored entries, calls `save` exactly once, and is a
  no-op on empty facts.
- **`tests/test_brain_routes.py`** — `/api/memory/distill-session` and
  `/api/memory/import-chat-export` via `TestClient` on the **auth-off**
  single-user path (`AUTH_ENABLED=false` → owner `None`), asserting the routes
  forward the right args and reject bad JSON (400) / missing form field (422).
- **`tests/test_memory_graph.py`** — the pure `build_graph`: nodes carry
  fields + truncated label, semantic edges are **thresholded and
  symmetric-deduped**, session edges added for same-`session_id` nodes, and
  `max_nodes` caps the set with the neighbor fn called **only for kept nodes**.
- **`tests/test_memory_graph_route.py`** — `GET /api/memory/graph` returns
  `{nodes, edges}` and **degrades to session-only edges** when the vector store
  is absent or unhealthy (no semantic search, no crash).

### Adversarial reviewer

- **`tests/test_reviewer.py`** — `build_review_prompt` (asks for a verdict) and
  `parse_review` (extracts `verdict`/`issues`/`suggestion`, keeps `raw`,
  tolerates free-form text).
- **`tests/test_review_route.py`** — `POST /api/review` via `TestClient`:
  returns the parsed verdict + issues + `model`, **400** on an empty answer
  (guard runs before endpoint resolution) and **400** when no model resolves.
- **`tests/test_resolve_endpoint_fallbacks.py`** — the real
  `resolve_endpoint()` fallback ladder, including
  **reviewer → utility → default** and the hidden-model auto-pick, using
  in-file fakes for settings + the DB query.

### Voice (Voicebox provider + JS call mode)

- **`tests/test_voicebox_provider.py`** — the local Voicebox TTS/STT adapters
  with `httpx` monkeypatched: TTS posts `/generate` with the
  `X-Voicebox-Client-Id: apollo` header and falls back to the first profile;
  STT posts multipart to `/transcribe` and parses `text` / `transcription` /
  `segments` reply shapes; `available` flips on reachability.
- **`tests/test_voice_vad.mjs`** (`node --test`) — the browser VAD gate
  (`static/js/vad.js`): threshold crossing emits `speechstart`, sustained
  silence emits `speechend`, a loud blip resets the silence timer, `reset()`
  clears state.
- **`tests/test_voice_call_machine.mjs`** (`node --test`) — the call-mode
  state machine (`static/js/voiceCall.js`): idle→listening→capturing→
  transcribing→thinking→speaking→listening, empty-transcript no-submit,
  barge-in (speaking + `speechStart` stops TTS and re-captures), and the
  **synchronous-`speakEnd` non-deadlock** case (TTS-unavailable path).
- **`tests/test_graph_layout.mjs`** (`node --test`) — the memory-graph
  force layout (`static/js/graphLayout.js`): connected nodes spring together,
  `seedPositions` is deterministic per seed and stays in bounds, and edges
  referencing missing nodes don't throw.

---

## `conftest.py` — collection-time dependency stubbing

`tests/conftest.py` is what makes the suite runnable on a bare checkout. It:

1. Puts the project root on `sys.path` (`conftest.py:8`).
2. Stubs **optional** heavy deps with `MagicMock` **only when not installed**
   (`_has_module` guard, `conftest.py:10-28`) — `sqlalchemy.*`, `bcrypt`, `pyotp`,
   `httpx`, `fastapi.*`, `starlette.*`, `pydantic`. Real FastAPI/Starlette/Pydantic
   are deliberately **not** replaced when present, because route tests import their
   subpackages.
3. Replaces `src.database` with a stub module exposing `SessionLocal` and
   `ModelEndpoint` mocks (`conftest.py:30-34`).

```python
# tests/conftest.py:27-34
    if mod_name not in sys.modules and not _has_module(mod_name):
        sys.modules[mod_name] = MagicMock()

if "src.database" not in sys.modules:
    _db = types.ModuleType("src.database")
    _db.SessionLocal = MagicMock()
    _db.ModelEndpoint = MagicMock()
    sys.modules["src.database"] = _db
```

---

## Patterns used throughout

### 1. Pure-function parametrize (`test_web_decider.py`)

The web-need classifier is tested as a pure function over many inputs — no mocking
needed:

```python
# tests/test_web_decider.py:7-16
@pytest.mark.parametrize("msg", [
    "What's the latest news on the EU AI Act?",
    "current price of AMD stock",
    "weather in Stockholm today",
    ...
])
def test_clear_yes(msg):
    assert heuristic_decision(msg) == "yes"
```

It encodes deliberate precedence rules as tests — e.g. self-contained coding work must
**not** trigger web search even with an explicit search ask:

```python
# tests/test_web_decider.py:86-88
def test_no_web_verbs_beat_explicit_search_intent():
    assert heuristic_decision("refactor this and search the web for examples") == "no"
```

…and that coding/schema vocabulary (`update the price field`, `forecast column`,
`cron job`) reads as `"no"` (`:74-83`), while a URL plus explicit search intent reads
`"yes"` (`:52-57`). A long paste short-circuits to `"no"` (`:44-45`).

### 2. `patch` of seam functions (`test_search_immediate_fallback.py`)

The provider-chain builder is tested by patching the settings accessor and the runtime
singleton — proving the no-timeout-penalty fallback:

```python
# tests/test_search_immediate_fallback.py:18-24
def test_skips_searxng_when_sidecar_down():
    with patch("services.search.core._get_search_settings", return_value=_settings()), \
         patch("services.searxng.runtime.get_runtime") as rt:
        rt.return_value.installed = True
        rt.return_value.is_serving.return_value = False
        chain = _build_provider_chain("searxng")
    assert chain == ["duckduckgo"]
```

The same file pins the env-precedence rules: an explicit `SEARXNG_INSTANCE`
(Docker) or a custom `search_url` must never let Apollo skip SearXNG on the
sidecar's behalf, and the `.env.example` boilerplate default `http://localhost:8080`
must **not** shadow the managed sidecar (`:104-124`, caught live during UI
verification). `monkeypatch.setenv/delenv` drives the env cases (`:76-101`).
Runtime errors **fail open** — the chain keeps SearXNG (`:51-56`).

### 3. Dependency-injected lifecycle fakes (`test_searxng_runtime.py`)

`SearxngRuntime` takes injectable `spawn` and `health_check` callables, so its entire
lifecycle is testable with a `FakeProc` and no real process:

```python
# tests/test_searxng_runtime.py:28-44
class FakeProc:
    def poll(self):   return 1 if self.killed else None
    def terminate(self): self.killed = True
    def wait(self, timeout=None): return 0
    def kill(self):   self.killed = True
```

Covered behaviors include: disabled/not-installed are no-ops (`:47-58`); spawn issues
`python -m searx.webapp` with a minimal env containing `SEARXNG_SETTINGS_PATH`
(`:61-81`); an already-serving instance is reused without spawning (`:84-89`);
`stop()` interrupts the boot-wait loop in under 1 s (`:125-176`); a crashed proc
status is `"failed"` (`:179-193`); `maybe_restart` is rate-limited (`:245-250`); and
crucially the sidecar env must **not** inherit Apollo secrets:

```python
# tests/test_searxng_runtime.py:253-276
def test_spawn_env_is_minimal(tmp_path, monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "sk-secret")
    ...
    env = spawned[0].kwargs["env"]
    assert "TAVILY_API_KEY" not in env
    assert "DATA_BRAVE_API_KEY" not in env
    assert env["SEARXNG_SETTINGS_PATH"].endswith("settings.yml")
```

`_http_ok` is tested for fail-closed behavior — only a body starting with `b"OK"`
counts as serving; HTML (a foreign service) or a connection error reads as down
(`:383-415`). An autouse fixture redirects `_LOG_PATH` to `tmp_path` so tests never
write into the repo's `logs/` (`:13-16`).

`test_searxng_config.py` covers path derivation, the two-file `installed` predicate
(venv python + settings.yml), and bad-port fallback to 8893 (`:1-39`).

### 4. `TestClient` + WebSocket round-trips (`test_browser_ws.py`)

The embedded-browser live view is tested with Starlette's `TestClient`, a stubbed
session, and `monkeypatch`. Auth gating is asserted by expecting `WebSocketDisconnect`:

```python
# tests/test_browser_ws.py:282-298
def test_ws_rejects_invalid_session():
    app = _ws_app(ws_validate=lambda token: token == "good")
    with TestClient(app) as c:
        with pytest.raises(WebSocketDisconnect):
            with c.websocket_connect("/api/browser/ws"):
                pass  # no cookie → 1008 close

def test_ws_rejects_without_privilege(stub_live_session):
    app = _ws_app(ws_validate=lambda token: True,
                  ws_authorize=lambda token: False)  # valid session, lacks can_use_browser
    ...
```

A `stub_live_session` fixture (`:246-279`) swaps `embedded_browser.session` for a
`SimpleNamespace` of async stubs that emit one fake frame and record input, so the WS
streams and receives input without launching Chromium (`:301-321`). Input mapping is
unit-tested against recording mouse/keyboard fakes (`:64-119`), and the
`_FrameForwarder` backpressure policy (drop frames while a send is in flight) is
tested directly (`:214-234`). `async def test_…` functions run under the project's
asyncio pytest config.

### 5. Byte-level fixtures (`test_gguf_meta.py`)

The GGUF header reader/classifier is tested by hand-building minimal valid GGUF byte
strings with `struct`, exercising real parsing paths:

```python
# tests/test_gguf_meta.py:38-49
def test_reads_architecture(tmp_path):
    f = tmp_path / "model.gguf"
    f.write_bytes(_make_gguf("llama"))
    assert read_architecture(str(f)) == "llama"
```

It covers truncated files, non-GGUF magic, missing arch key, and type-skipping a
non-string KV before the arch key (`:52-80`). `classify_architecture` is exhaustively
mapped: `diffusion-gemma`/`dream`/`whisper` → `"unsupported"`,
`bert`/`gte`/`nomic-bert`/`snowflake-arctic-embed` → `"embedding"`, and the chat
architectures (`llama`, `qwen2`, `qwen35`, `gemma4`, `phi3`, `qwen3moe`, `lfm2moe`,
`nemotron_h_moe`, `gpt-oss`) → `"chat"` (`:92-175`).

---

## CI workflow (`.github/workflows/ci.yml`)

Triggers on every `pull_request` and on `push` to `main` (`ci.yml:3-7`). One
`ubuntu-latest` job:

1. Checkout (`actions/checkout@v4`).
2. Python 3.12 with pip cache (`ci.yml:17-21`).
3. `pip install -r requirements.txt` (`ci.yml:24-26`).
4. **Compile gate** — `python -m compileall -q app.py companion core routes services
   src scripts/apollo-ralph scripts/check-paperclip-browser` (`ci.yml:29`) catches
   syntax errors before tests run.
5. `python -m pytest -q` (`ci.yml:32`).
6. Node 20 with npm cache → `npm ci` → `npm run test:js` (`ci.yml:34-44`).

The whole pipeline runs with no external services because of the `conftest.py` stubs
and the dependency-injected fakes in the runtime/search tests.

## 2026-07-19 current verification gates

The CI matrix now installs `requirements-dev.txt`, compiles Python sources,
runs runtime-path and module-size guards, then runs the Python suite across
Ubuntu, macOS, and Windows. A separate Ubuntu E2E job installs Chromium and
runs `scripts/run-e2e.sh` in an isolated application-data root. Fresh local
evidence: `APOLLO_STARTUP_SMOKE=1 bash scripts/check.sh` produced 1,934 passed
and 3 skipped Python tests plus 134 JavaScript tests; the browser runner
completed four journeys. Do not use a developer's live `APOLLO_DATA_DIR` for
any test or preview run.
