# Apollo — Performance, Optimization & Caching

Apollo runs on a single host, often next to a multi-gigabyte llama-server, so its
performance work concentrates on three fronts: never paying a model load or KV-cache
allocation twice (warm slots, bounded context), never letting a dead upstream stall the
hot path (caches, cooldowns, parallel probes), and never doing wasted work in the UI
render loop (HTML diff skip, bounded queues, capped bubbles).

## 1. Single warm llama-server slot + eviction

`services/localmodels/server_manager.py` keeps **one warm chat model and one warm
embedding model** per process. Requesting a different chat model evicts the current one
— RAM/VRAM are the scarce resource, not process count:

```python
# services/localmodels/server_manager.py (ensure_running)
with self._lock:
    # Embedding GGUFs get an independent slot (served with --embedding)
    # so they can run alongside a chat model.
    slot = self._embed if m.kind == "embedding" else self._chat
    if slot and slot.model_id == m.id and slot.proc.poll() is None:
        return slot.base_url          # already warm — free
    if slot:
        self._stop_proc(slot)         # evict the previous model
    proc = self._launch(m)
```

Everything is under one `threading.RLock` so `stop()` cannot race `ensure_running`'s
slot bookkeeping. The server is a process-wide singleton (`get_server()` behind
`_SERVER_LOCK`). `stop_proc` is terminate-wait(10s)-kill.

### 1.1 Serving context: `min(known window, APOLLO_LLAMA_CONTEXT)`

```python
# services/localmodels/server_manager.py (_serving_context)
"""Apollo's prompt packer budgets against the model's KNOWN window, so a
fixed small -c rejects long chats with HTTP 400 ("request exceeds the
available context size"). Serve min(known window, cap) instead — the
cap (APOLLO_LLAMA_CONTEXT, default 16384) keeps the KV cache bounded;
the configured default stays the floor."""
cap = max(int(os.getenv("APOLLO_LLAMA_CONTEXT", "16384")), self._context)
known = _lookup_known(m.name or m.id)   # src.model_context
if known:
    return max(self._context, min(known, cap))
return cap
```

The tension: the prompt packer budgets tokens against the model's advertised window, so
launching `llama-server -c 4096` for a 128k model makes long chats 400 out; but serving
the full advertised window allocates an enormous KV cache. `min(known, 16384)` is the
compromise — bounded memory, no packer/server disagreement below the cap.

### 1.2 Health timeout: 40s per GB

```python
# services/localmodels/server_manager.py (_health_timeout_for)
"""Big GGUFs (external drives, MoE models) plus large -c values take
far longer than the base timeout to load. Measured live: a 8.4GB 14B
at -c 16384 needs >180s on this hardware. Allow ~40s/GB with the
configured timeout as the floor."""
size_gb = (m.size_bytes or 0) / (1024 ** 3)
return max(self._health_timeout, size_gb * 40.0)
```

The base `health_timeout` is 180s; an 8.4 GB 14B Q4 at 16k context measured past 180s,
hence the size-scaled formula (8.4 GB → ~336s allowance). `_wait_health` polls
`/health` every 0.5s, and if the process exits early the raised error embeds the tail
of `/tmp/apollo-llama-<port>.log` so the cause is in the exception, not a second log
hunt.

### 1.3 Model directory ordering

`services/localmodels/config.py` resolves scan roots as *settings → `APOLLO_MODELS_DIRS`
env → built-in defaults*, and order matters: the scanner walks dirs in order and
**dedupes by path-hash id, first hit wins** — so listing the fast drive first means a
model present on both an external SSD and the Desktop resolves to the fast copy:

```python
# services/localmodels/config.py
DEFAULT_DIRS = [
    "/Volumes/MainStore/Development/AI_Models",   # fast external SSD first
    os.path.expanduser("~/Desktop/AI_Models"),
]
```

The scanner (`services/localmodels/scanner.py`) also prunes cache/blob dirs in place
(`_SKIP_DIRS = {"cache", ".cache", "llama-cache", "ollama", ...}`) so `os.walk` never
descends into HuggingFace blob stores, and registers only part 1 of split GGUFs.

## 2. Picker cache + rescan sync

The model picker reads `ModelEndpoint.cached_models` (a JSON list), so local models
appear instantly with no scan on the request path. A rescan re-syncs the single managed
endpoint, deduping by **name** (the picker is name-based — the same GGUF on two drives
must list once), chat models first:

```python
# services/localmodels/registry.py (sync_managed_endpoint)
names: list[str] = []
seen: set[str] = set()
for kind in ("chat", "embedding"):
    for m in models:
        if m.kind == kind and m.name not in seen:
            seen.add(m.name)
            names.append(m.name)
```

`services/localmodels/lifecycle.py:rescan()` is the one orchestration point: scan dirs
→ `get_server().set_catalog(models)` (the serving catalog and the picker can't drift)
→ `sync_managed_endpoint(models)`. `startup_scan()` runs it on a daemon thread from
`app.py` so boot isn't delayed.

## 3. Endpoint probing: parallel, cached, with failure backoff

`routes/model_routes.py` learned this the hard way — the docstring records the
regression: sequential 3s-timeout probes against many endpoints (some offline) tied up
the threadpool 15–30s per cycle until the server degraded. Now:

- **Per-user response cache**: `_models_cache` keyed by owner, `_MODELS_CACHE_TTL = 30`
  seconds; `/api/models` answers instantly from `cached_models` while a background
  refresh keeps it warm. Any endpoint CRUD calls `_invalidate_models_cache()`.
- **Single-flight**: `_refresh_inflight` guard so overlapping refreshes no-op.
- **Failure backoff**: endpoints that failed 3+ consecutive probes are skipped for 300s.
- **Bounded parallelism**: probes run in a `ThreadPoolExecutor` with a 2s timeout each.

```python
# routes/model_routes.py (_refresh_caches_bg)
_probe_failures = {}  # ep_id → (last_fail_ts, consecutive_fails)
...
ts, fails = _probe_failures.get(ep.id, (0, 0))
if fails >= 3 and (now - ts) < 300:
    continue                          # back off dead endpoints
...
with ThreadPoolExecutor(max_workers=min(8, len(to_probe))) as pool:
    futures = [pool.submit(_probe_one, ep) for ep in to_probe]
    for fut in as_completed(futures):
        ep, ids, err = fut.result()
        if ids:
            ep.cached_models = json.dumps(ids)
            _probe_failures.pop(ep.id, None)
        else:
            prev = _probe_failures.get(ep.id, (0, 0))
            _probe_failures[ep.id] = (_time.time(), prev[1] + 1)
```

`local://` managed endpoints are never HTTP-probed (`_is_local_managed` check) — their
catalog comes from the filesystem scan. A related cooldown lives in `src/llm_core.py`
(dead-host map, 2 strikes → 20s cooldown, documented in doc 12 §3), and the shared
`httpx.AsyncClient` there keeps provider connections warm:
`httpx.Limits(max_connections=100, max_keepalive_connections=30, keepalive_expiry=30.0)`
— repeat API calls skip the 100–500ms TCP+TLS handshake.

## 4. Floor (Paperclip office) render performance

The Floor re-renders on a tick, so `static/js/paperclip.js` makes no-op ticks free and
bounds everything that grows.

### 4.1 Identical-HTML innerHTML skip

```javascript
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

Besides the DOM cost, rewriting identical markup would visually restart every CSS walk
and bubble animation each tick.

### 4.2 Walk-once movement consumption

Movement is a delta between the committed previous frame and the new layout. After
rendering, `commitWorkspaceLayout` advances `lastX/lastY`, *consuming* the move so the
next tick computes `moving: false` and the walk animation never replays:

```javascript
// static/js/paperclip.js
for (const agent of agents) {
  agent.moving = agent.x !== agent.fromX || agent.y !== agent.fromY;
}
...
function commitWorkspaceLayout(state, layout) {
  for (const rendered of layout.agents) {
    const agent = state.agents.get(rendered.id);
    if (agent) { agent.lastX = rendered.x; agent.lastY = rendered.y; }
  }
}
```

(Contract pinned by `tests/test_paperclip_floor_ui.mjs` — "agents walk exactly once per
move, not on every render tick".)

### 4.3 Bubble caps

Speech bubbles are hard-capped per frame so a busy floor stays readable and cheap:
recent interactions `.slice(0, 6)`, then **2 conversations** (`.slice(0, 2)`), **2
murmurs** from heads-down working agents (`.slice(0, 2)`, newest first), and **3 status
callouts** for review/blocked/done agents (`.slice(0, 3)`).

### 4.4 Event hub: bounded, drop-don't-backpressure

`services/paperclip/events.py` feeds `/api/paperclip/stream`. Three bounds:

```python
# services/paperclip/events.py
def __init__(self, history: int = 200):
    self._recent: deque = deque(maxlen=history)     # 200-entry replay buffer
...
def publish(self, events):
    for event in events:
        self._seq += 1
        ...
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(entry)
            except asyncio.QueueFull:               # slow subscriber: drop,
                pass                                # never block publishers

def subscribe(self) -> asyncio.Queue:
    queue: asyncio.Queue = asyncio.Queue(maxsize=500)
```

A subscriber that can't keep up loses events instead of back-pressuring the collector.
The SSE route subscribes *before* snapshotting the replay buffer, then uses the
monotonic **seq watermark** to drop events that appear in both
(`routes/paperclip_routes.py`: `if seq <= last_seq: continue`) — no duplicates, no gap.

### 4.5 lmproxy activity pulse debounce

Token streams through `/lmproxy/v1/*` would flood the Floor with one event per chunk.
`routes/lmproxy_routes.py` pulses at most once per agent per `pulse_interval` (10s):

```python
# routes/lmproxy_routes.py (_pulse)
now = time.monotonic()
if now - last_pulse.get(agent_id, float("-inf")) < pulse_interval:
    return
last_pulse[agent_id] = now
```

The pulse itself is wrapped in `try/except` — *"never fail the proxy over a viz
event"*.

## 5. Local embeddings: fastembed (ONNX)

`src/embeddings.py` prefers a configured remote embedding endpoint, with **local
fastembed (ONNX, ~50MB) as the zero-config fallback** (`FastEmbedClient` wrapping
`fastembed.TextEmbedding`). Performance/portability touches: an unreachable Ollama
fast-fails to fastembed rather than hanging RAG; the cache lives under
`data/fastembed_cache` (`FASTEMBED_CACHE_PATH` overrides, also plumbed through
docker-compose); on Windows it forces HuggingFace to *copy* instead of symlink (broken
`model.onnx` symlinks otherwise fail to load) and prunes dangling `.onnx` symlinks so
fastembed re-fetches real files. The model defaults to
`sentence-transformers/all-MiniLM-L6-v2` (`FASTEMBED_MODEL`).

## 6. SQLite engine settings

`core/database.py` keeps the engine deliberately simple — SQLite with the threading
check disabled (FastAPI handlers run across threadpool threads), and referential
integrity enforced per-connection via an event listener on the Engine **class** so
every engine in the process gets it:

```python
# core/database.py
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./data/app.db")
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

@event.listens_for(Engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    if isinstance(dbapi_connection, sqlite3.Connection):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()
```

The `isinstance(sqlite3.Connection)` guard makes the pragma a no-op under Postgres
(`DATABASE_URL` is env-switchable). Hot-path DB work is kept off the request where
possible — e.g. the API-token `last_used_at` update is fire-and-forget via
`asyncio.to_thread` (`app.py`), and the bearer-token cache avoids a DB query +
linear bcrypt scan per request, refreshing only when a token is created/revoked.

## 7. Static asset caching

Two-tier (full detail in doc 11 §8): source files (`.js/.css/.html`) are served with
`Cache-Control: no-cache` by `_RevalidatingStatic` in `app.py` — browsers revalidate
every load and get cheap 304s; `static/index.html` additionally pins hot modules with
manual `?v=` tokens (e.g. `paperclip.js?v=paperclip-floor-20260611d`). Content-hashed
generated images go the opposite way:
`Cache-Control: public, max-age=31536000, immutable`, since bytes for a given filename
never change.
