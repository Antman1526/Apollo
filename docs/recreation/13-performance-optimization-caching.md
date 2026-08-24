# Apollo — Performance Optimization & Caching

Scope: the caching and performance patterns actually present in the Apollo
codebase at `/Users/Antman/Apollo` (FastAPI + SQLite/SQLAlchemy + ChromaDB +
JSON-file state). Every pattern below is grounded in source with `path:line`
references; the closing section gives honest scalability limits.

Apollo is a single-process, single-user-oriented desktop app, so its
performance work is mostly about **avoiding repeated expensive work on hot
paths** (settings reads, health probes, search, tool routing) and
**overlapping I/O** (parallel fetch, SSE streaming) rather than horizontal
scaling.

---

## 1. The 2-second settings TTL cache

`src/settings.py:17-22, 192-207`

`get_setting()` is called on nearly every chat turn and every preprocess step.
Without a cache it re-parses `data/settings.json` from disk each call. A tiny
TTL cache fixes that:

```python
_CACHE_TTL = 2.0                                          # :20
_settings_cache: tuple[float, dict] | None = None
_features_cache: tuple[float, dict] | None = None

def load_settings() -> dict:
    global _settings_cache
    now = time.monotonic()
    if _settings_cache and (now - _settings_cache[0]) < _CACHE_TTL:
        return _settings_cache[1]                          # cache hit
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            saved = json.load(f)
        ...
        merged = {**DEFAULT_SETTINGS, **saved}
    except (FileNotFoundError, json.JSONDecodeError, ValueError):
        merged = dict(DEFAULT_SETTINGS)
    _settings_cache = (now, merged)                        # populate
    return merged
```

Design choices visible in the code:

- **2s TTL** is short enough that a human editing `settings.json` (or the
  Settings UI) sees the change "within a couple seconds," but long enough to
  collapse a burst of reads during a single request into one disk parse.
- **`time.monotonic()`** (not wall clock) so a system clock adjustment can't
  make the cache appear infinitely fresh or stale.
- **Write path invalidates immediately.** `save_settings()` /
  `save_features()` call `_invalidate_caches()` (`:24-27, 214, 297`), so a
  programmatic write is reflected instantly rather than waiting out the TTL.
- `load_features()` (`:275-290`) uses the **same** pattern with its own
  `_features_cache`.

Trade-off: this is a per-process in-memory cache. In a multi-process
deployment, one worker editing settings would not invalidate another worker's
cache until its TTL lapsed (≤2s). Acceptable for the single-process desktop
target.

---

## 2. Search result cache (file-based, LRU + TTL)

`services/search/cache.py` + `services/search/core.py`

### Cache store (`services/search/cache.py:11-57`)

On-disk cache under `services/cache/search/` and `…/content/`, keyed by
SHA-256, with an in-memory index for LRU bookkeeping:

```python
SEARCH_CACHE_DIR  = CACHE_DIR / "search"
CONTENT_CACHE_DIR = CACHE_DIR / "content"
CACHE_MAX_ENTRIES = 1000                                   # :15

search_cache_index: Dict[str, datetime] = {}              # key -> last-write time
cache_metrics = {"hits": 0, "misses": 0, "evictions": 0}  # :26

def generate_cache_key(data: str) -> str:
    return hashlib.sha256(data.encode("utf-8")).hexdigest()
```

`cleanup_cache()` (`:34-57`) evicts on two policies at once: **age** (entries
older than `max_age` are deleted) and **count** (oldest entries beyond
`CACHE_MAX_ENTRIES = 1000` are dropped, LRU by stored timestamp). Eviction is
counted into `cache_metrics`.

### Cache read/write on the search path (`services/search/core.py:160-234`)

```python
cache_key = generate_cache_key(f"{query}|{count}|{time_filter}")   # :160
cache_file = SEARCH_CACHE_DIR / f"{cache_key}.cache"

if cache_file.exists():                                            # :164  read
    cached_data = json.load(open(cache_file, ...))
    expiry = datetime.fromisoformat(cached_data["expiry"]) ...
    if expiry and datetime.now() < expiry:
        logger.debug(f"Search cache hit for query: {query}")
        _record_query(query, bool(results), cache_hit=True)
        return cached_data["data"]                                # hit → return
    else:
        cache_file.unlink(missing_ok=True)                        # expired → purge
...
# on a successful live search:
expiry = datetime.now() + _cache_duration_for_query(query)        # :218  write
json.dump({"timestamp": ..., "expiry": expiry.isoformat(),
           "data": results}, open(cache_file, "w"))
search_cache_index[cache_key] = datetime.now()
cleanup_cache(SEARCH_CACHE_DIR, search_cache_index, timedelta(hours=1))   # :227
```

**Adaptive TTL by query intent** (`services/search/query.py:137-141`):

```python
def _cache_duration_for_query(query: str) -> timedelta:
    """News queries -> 30 minutes, reference queries -> 24 hours."""
    if <looks like news/recency>:
        return timedelta(minutes=30)
    return timedelta(hours=24)
```

So time-sensitive ("news") queries cache for 30 min; evergreen reference
queries cache for 24h. The cache key includes `count` and `time_filter`, so
the same text with different result counts / time windows are distinct entries.
`invalidate_search_cache()` (`core.py:240-264`) clears all entries or a single
query on demand.

---

## 3. SearXNG health cache (2s TTL on the hot path)

`services/searxng/runtime.py:22, 71-78`

`is_serving()` is consulted on **every search call** to decide whether to hit
the managed SearXNG sidecar or skip straight to the DuckDuckGo fallback. A live
HTTP probe on every search would add latency to each one, so it's cached:

```python
_HEALTH_TTL = 2.0  # seconds — is_serving() is consulted on every search call   # :22

def is_serving(self) -> bool:
    """Health-checked, cached (2s TTL — called on every search)."""
    now = time.monotonic()
    if self._health_cache and (now - self._health_cache[0]) < _HEALTH_TTL:
        return self._health_cache[1]                               # cached
    ok = self._health(self._health_url(), 2.0)                     # GET /healthz
    self._health_cache = (now, ok)
    return ok
```

Notes:

- The probe (`_http_ok`, `:30-41`) reads only the first 16 bytes and requires
  the body to start with `b"OK"` (SearXNG `/healthz` returns exactly `OK`).
  It **fails closed**: a foreign service on the same port reads as not-serving,
  so the provider chain skips it fast (a 2s probe timeout, not a 30s hang).
- Lifecycle transitions (`start`/`stop`/restart) explicitly clear
  `self._health_cache = None` (`:109, 173, 201`) so status flips immediately
  rather than lagging the TTL.
- This is the performance counterpart to the immediate-DDG-fallback design:
  when the sidecar is down, the chain pays at most one 2s probe per 2s window,
  not per request.

---

## 4. Tool-index pre-warm at startup

`app.py:1075-1090` (the `_warmup_tool_index` startup task)

The agent routes user queries to tools via a ChromaDB collection
(`apollo_tool_index`, `src/tool_index.py:56`). The first semantic query has to
lazily load the local embedding model — slow. Apollo pre-warms it off the
request path during startup so the user's first turn is as fast as later ones:

```python
async def _warmup_tool_index():
    try:
        from src.tool_index import get_tool_index
        idx = await asyncio.to_thread(get_tool_index)             # build singleton
        if idx:
            await asyncio.to_thread(idx.get_tools_for_query, "warmup", 8)  # force embed load
            logger.info("[startup] Tool index pre-warmed")
    except Exception as e:
        logger.warning(f"Tool index warmup failed (non-critical): ...")

_startup_tasks.append(asyncio.create_task(_warmup_tool_index()))
```

Two important details:

- The blocking work (building the index singleton, running a throwaway
  `"warmup"` query that forces the embedding model to load) is dispatched via
  `asyncio.to_thread`, so it does **not** block the event loop / app startup.
- It's **non-critical**: failure is logged and swallowed; the app still serves.
- Strong references to fire-and-forget startup tasks are held in
  `app.state._startup_tasks` (`app.py:1045-1048`) so Python's GC can't cancel
  them mid-flight.

A sibling `_warmup_endpoints()` (`app.py:1092-1108`) plus a `_keepalive_loop`
(`:1110-1120`) similarly pre-warm/keep model endpoints reachable. The
`ToolIndex` itself is a process singleton (`src/tool_index.py:456-463`,
`get_tool_index()` caches `_tool_index`), so the warm-up cost is paid once.

The same warm-once philosophy applies to the local-model catalog: a background
thread runs `startup_scan` (`app.py:887-891`) so the GGUF catalog is warm on
first request without delaying startup.

---

## 5. SSE streaming

Apollo streams long-running responses to the browser over Server-Sent Events
(`media_type="text/event-stream"`) using FastAPI `StreamingResponse`, so the
client renders tokens/progress incrementally instead of waiting for the whole
response. Confirmed call sites:

- `routes/shell_routes.py:768, 785, 788-790, 880` — streaming shell / agent
  output, including an empty-stream short-circuit
  (`StreamingResponse(empty(), media_type="text/event-stream")`, `:768`).
- `routes/paperclip_routes.py:191-193, 256` — streamed Paperclip endpoints.

The performance benefit is latency-to-first-byte: the user sees output as the
model produces it, and the connection multiplexes a long agent run into a
single HTTP response rather than polling.

---

## 6. Single warm llama.cpp model (one-slot swap)

`services/localmodels/server_manager.py:116-143, 230-259`

Apollo runs **at most one warm chat model** at a time. Each chat model is an
external `llama-server` process; keeping several resident would exhaust RAM/VRAM
on a desktop. `ensure_running()` implements a swap: if the requested model is
already the warm slot, reuse it; otherwise stop the current one and launch the
new one — all under a lock so the slot bookkeeping can't race.

```python
def ensure_running(self, ref: str) -> str:
    m = self._resolve(ref)
    ...
    with self._lock:
        slot = self._embed if m.kind == "embedding" else self._chat
        if slot and slot.model_id == m.id and slot.proc.poll() is None:
            return slot.base_url                          # already warm → reuse
        if slot:
            self._stop_proc(slot)                         # evict current model
        proc = self._launch(m)                            # cold-start the new one
        if m.kind == "embedding": self._embed = proc
        else:                     self._chat  = proc
        return proc.base_url
```

Supporting performance/robustness details:

- There are exactly two slots: one **chat** (`self._chat`) and one optional
  **embedding** (`self._embed`, served with `--embedding`, `:181-182`), so an
  embedding GGUF can co-reside with a chat model. RAG still defaults to
  fastembed, so the embedding slot has no implicit caller yet (`:129-132`).
- Cold-start health-wait scales with model size: `_health_timeout_for`
  (`:204-210`) allows ~40s/GB ("a 8.4GB 14B at -c 16384 needs >180s on this
  hardware") so big GGUFs on external drives aren't killed prematurely.
- Serving context (`_serving_context`, `:145-166`) is `min(known window, cap)`
  with `cap = APOLLO_LLAMA_CONTEXT` (default 16384) to bound the KV cache while
  still admitting long chats.
- An OpenAI-compatible proxy (`app.py:809-826`, `_warm_chat_base_url`) forwards
  agent calls to whichever GGUF is currently warm, so callers don't track the
  swapping base URL.

This is a deliberate single-warm-model trade: model switches incur a full
stop+cold-start (seconds to minutes for large models), but steady-state memory
stays bounded to one resident model.

---

## 7. Parallel web fetch (ThreadPoolExecutor)

`services/search/core.py:270-411` (`comprehensive_web_search`)

After ranking search results, Apollo fetches the top N page bodies **in
parallel** instead of serially — page fetch is I/O-bound (network latency), so
a thread pool overlaps the waits:

```python
def comprehensive_web_search(query, max_pages=3, max_workers=4, ...):
    ...
    with ThreadPoolExecutor(max_workers=max_workers) as executor:       # :397
        future_to_url = {
            executor.submit(fetch_webpage_content, url, 8, retry_attempt=0): url
            for url in filtered_urls
        }
        for future in as_completed(future_to_url):                      # :402
            url = future_to_url[future]
            try:
                result = future.result()
                if result["success"] and result["content"] \
                   and len(result["content"]) >= min_content_length:
                    fetched_content.append(result)
            except Exception as e:
                logger.error(f"Exception while fetching {url}: {str(e)}")
```

`max_workers=4` by default; results are consumed via `as_completed` so a slow
page doesn't block faster ones, and per-URL failures are isolated (logged, not
fatal). The provider call itself (`searxng_search_results`, §2) sits in front of
this with its own result cache, so a repeated comprehensive search can skip both
the provider round-trip and the parallel fetch.

The provider chain retries each provider twice (`for attempt in range(2)`,
`:306`) but **breaks immediately on a 429 `RateLimitError`** (`:314-321`) — an
instant retry of a rate-limit is counterproductive, so it falls through to the
next provider in the chain (e.g. DuckDuckGo) without burning the retry budget.

---

## 8. DB / query patterns

- **Connection reuse + cross-thread sharing.** The SQLAlchemy engine is created
  once with `check_same_thread: False` (`core/database.py:41-44`) and a single
  `sessionmaker` (`SessionLocal`, `:47`), so FastAPI's threadpool shares the
  SQLite connection without per-thread reconnect overhead.
- **Composite indexes on hot queries.** The schema declares indexes precisely
  where the app reads: `ix_sessions_active (archived, last_accessed)` and
  `ix_sessions_search (name, archived)` (`:124-127`),
  `ix_messages_session_time (session_id, timestamp)` (`:191-193`) for
  history fetches, `ix_scheduled_tasks_due (status, next_run)` (`:561-564`) for
  the scheduler poll, and `ix_task_runs_task (task_id, started_at)`
  (`:617-619`). These keep the common list/sort/poll queries off full-table
  scans.
- **`PRAGMA foreign_keys=ON`** (`:54-59`) is set per-connection so cascade
  deletes run in the DB rather than as N+1 ORM round-trips.
- **JSON blobs over join tables for low-value lists.** `ModelEndpoint`
  serializes `hidden_models` / `cached_models` as JSON `Text` (`:346-347`)
  instead of relational rows — fewer queries for data that's always read whole.
- **Atomic JSON writes** (`core/atomic_io.py`) use tmp-file + `fsync` +
  `os.replace`; the cost is one fsync per config save (rare), buying crash
  safety on the live-state files.

---

## 8b. Service-worker precache (versioned)

`static/sw.js` precaches the app shell so repeat loads are instant and the UI
works offline-ish. The cache is **versioned by a single constant** that is
bumped whenever the precache list or SW logic changes:

```js
// static/sw.js:9-10
// Bump CACHE_NAME whenever the precache list or SW logic changes.
const CACHE_NAME = 'apollo-v329';
```

The `activate` handler deletes every cache whose key isn't the current
`CACHE_NAME` (`sw.js:88`), so bumping the version is what forces clients to drop
stale precached assets and re-fetch — e.g. after shipping new
`voiceCall.js`/`vad.js`, the version bump is what guarantees the browser picks
up the new files instead of serving the cached old ones. The performance
trade-off: a served-from-cache shell (fast, offline-tolerant) at the cost of a
one-version lag that the bump exists to flush.

---

## 8c. TTS disk cache

`services/tts/tts_service.py` caches synthesized audio on disk under
`data/tts_cache/` (`:27-28`), keyed by a SHA-256 of
`(text, provider, model, voice, speed)` (`_cache_key`, `:69-71`). On
`synthesize(..., use_cache=True)` a hit reads the stored `.mp3`/`.wav` bytes and
returns immediately — no provider round-trip — logging `TTS cache hit`
(`:226-231`); a miss synthesizes and writes the result back, choosing the
extension by sniffing the audio magic bytes (ID3/MPEG frame → `.mp3`, else
`.wav`) (`_put_cache`, `:80-82, 258-260`). Because the key includes voice/model/
speed, changing any of them is a distinct entry rather than a stale hit. This is
the dominant latency win for repeated speech (e.g. call-mode re-reading the same
reply, or a fixed prompt), turning a network TTS call into a local file read.
`clear_cache()` (`:84-89`) and the `cache_entries`/size stats (`:279-290`) let
the UI manage it.

---

## 8d. Memory-graph endpoint caps (node + neighbor bounds)

`GET /api/memory/graph` (`routes/memory_routes.py:119-136`) builds an
owner-scoped knowledge graph over the user's memories, and it is bounded so a
large memory store can't blow up the response or the embedding work:

```python
# routes/memory_routes.py:136
return build_graph(mems, neighbor_fn, threshold=0.6, max_neighbors=4, max_nodes=300)
```

`build_graph` (`services/memory/graph.py:7-9`) first sorts memories by timestamp
descending and **truncates to `max_nodes=300`** *before* doing any neighbor
work, so only the 300 most-recent memories become nodes. Crucially the neighbor
lookup (`neighbor_fn` → `memory_vector.search(text, k=6)`) runs **only for the
kept nodes** — pinned by
`tests/test_memory_graph.py::test_max_nodes_caps_and_neighbor_fn_only_called_for_kept_nodes`
— so the expensive per-node vector search is O(300), not O(all memories). Each
node's semantic edges are further capped at `max_neighbors=4` and thresholded at
`0.6`, and symmetric edges are deduped. The endpoint also **degrades**: if the
vector store is absent or `unhealthy`, `neighbor_fn` returns `[]` and the graph
falls back to session-only edges (no vector search, no crash). Net: a fixed,
small compute budget regardless of memory-store size.

---

## 8e. Distillation is on-demand, not per-message

The second-brain distillation (`services/memory/brain.py`) runs an LLM pass to
extract durable facts from a chat session, and it is deliberately **not** wired
into the per-message hot path. It fires only when explicitly invoked —
`POST /api/memory/distill-session` for a chosen session, or
`POST /api/memory/import-chat-export` for an uploaded export — so the extra LLM
call and the ChromaDB indexing never tax an ordinary chat turn. Within a
distill run the cost is further bounded by `MemoryManager.find_duplicates`
(a re-distill of the same session doesn't re-index existing facts) and by the
`healthy`-gate on vector indexing (an unhealthy vector store stores the row but
skips the embed). This keeps steady-state chat latency independent of the
memory subsystem; distillation cost is paid only when the user asks for it.

---

## 9. Honest scalability limits

These are real ceilings, stated plainly:

- **SQLite is single-writer.** Every write to `data/app.db` serializes; under
  concurrent writers they block. This is the dominant write-throughput ceiling.
  It's the right call for the single-user desktop target, and `DATABASE_URL`
  can be repointed at Postgres (the SQLite-specific code is all guarded), but
  out of the box there is no write concurrency.
- **One warm model.** `ensure_running` evicts the current chat model on every
  switch (§6). Multi-user / multi-model concurrent serving is not supported by
  this manager — a second model request stops the first. Model switches pay a
  full cold-start (tens of seconds to minutes for large GGUFs).
- **In-process, per-process caches.** The settings cache (§1) and SearXNG
  health cache (§3) live in process memory; they don't coordinate across
  workers. Multi-worker deployments get up-to-TTL staleness on settings and
  duplicated health probes. The ChromaDB embedded store (§4) is in-process HNSW
  — no network, but no sharing or horizontal scaling either.
- **File-based search cache** is bounded to `CACHE_MAX_ENTRIES = 1000` with
  age + LRU eviction (`services/search/cache.py:15, 34-57`); it's a cache, not
  a store, and is happily lossy. Cleanup runs inline on each cache write
  (`core.py:227`), so a write also pays the eviction scan.
- **Migrations run on every boot.** `init_db()` (`core/database.py:1516`) runs
  the full guarded-`ALTER` chain at startup; cheap now, but grows with each
  added migration.
- **Startup pre-warms are best-effort.** The tool-index / endpoint warm-ups
  (§4) swallow failures — performance degrades gracefully (first request pays
  the cold cost) rather than blocking boot.

Net: the architecture is tuned for a responsive **single-user desktop**
experience — cache the hot reads, probe sparingly, overlap I/O, keep exactly
one model resident — and explicitly trades multi-tenant/concurrent throughput
away to do it.

## 10. 2026-07-19 cache-location refresh

Search cache and analytics now live below the resolved application data root:
`search_cache/{search,content}` and `search/{search_analytics.json,
search_engine_error.log}`. This fixes packaged read-only mounts and keeps cache
eviction behavior unchanged. The cache remains file-backed with in-memory LRU
metadata, so it is process-local and intentionally optimized for the desktop
single-user profile rather than distributed coordination.
