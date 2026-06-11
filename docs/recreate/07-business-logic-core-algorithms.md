# 07 — Business Logic & Core Algorithms

This document covers Apollo's decision-making core: the provider-agnostic LLM layer, local GGUF model lifecycle, context-window resolution, the chat request pipeline, memory extraction/retrieval, the event bus, and the data side of the Paperclip Floor layout.

---

## 1. The LLM Core (`src/llm_core.py`)

One module speaks to every provider through three entry points — `llm_call` (sync), `llm_call_async`, and `stream_llm` (async generator) — plus `*_with_fallback` wrappers that walk an ordered candidate chain.

### 1.1 Provider detection by hostname

```python
# src/llm_core.py
def _detect_provider(url: str) -> str:
    if _is_ollama_native_url(url):
        return "ollama"
    if _host_match(url, "anthropic.com"):
        return "anthropic"
    if _host_match(url, "openrouter.ai"):
        return "openrouter"
    if _host_match(url, "groq.com"):
        return "groq"
    return "openai"
```

`_host_match` compares the parsed **hostname** (exact or subdomain, trailing-dot tolerant) instead of substring-matching the URL, so `anthropic.com.example` or a path containing a provider name can't be misclassified. `_is_ollama_native_url` accepts `ollama.com` (Ollama Cloud) or a local host/`:11434` whose path starts with `/api`. Unknown hosts default to the OpenAI-compatible wire format.

Each provider gets a payload builder: `_build_anthropic_payload` (converts OpenAI messages → Anthropic blocks, hoists `system` into a structured text block with a `cache_control: {"type": "ephemeral"}` breakpoint when `tools` are present or the prompt exceeds 4,000 chars, converts tools and caches the last tool schema), and `_build_ollama_payload` (native `/api/chat`, parses tool-call argument strings back into objects, and only emits `options.num_ctx` when the discovered context differs from `DEFAULT_CONTEXT` — Ollama otherwise silently truncates at 2048).

### 1.2 `local://` URL materialization

Local GGUF models are represented by the sentinel base URL `local://llama.cpp`. The first call materializes it into a live server:

```python
# src/llm_core.py
def materialize_local_url(url: str, model: str) -> str:
    """Turn a `local://llama.cpp...` sentinel into a live llama-server URL.
    Idempotent: starts the model's server if needed (evicting the previous
    warm chat model), then returns its OpenAI-compatible chat endpoint."""
    if not isinstance(url, str) or not url.startswith("local://llama.cpp"):
        return url
    from services.localmodels.server_manager import get_server
    base = get_server().ensure_running(model)
    return base.rstrip("/") + "/v1/chat/completions"
```

The async paths run this through `asyncio.to_thread(...)` because a first load can block ~3 minutes (launch + health) and must not stall the event loop. Unrelated sentinels (e.g. `local://fastembed` for embeddings) pass through untouched.

### 1.3 Dead-host cooldown

A module-level health map prevents one offline upstream from wedging the whole app, while tolerating single transient blips:

```python
# src/llm_core.py
DEAD_HOST_COOLDOWN = 20.0
_HOST_FAIL_THRESHOLD = 2
_dead_hosts: Dict[str, float] = {}   # host -> cooldown expiry ts
_host_fails: Dict[str, int] = {}     # host -> consecutive connect failures
_host_health_lock = threading.Lock() # sync llm_call runs in FastAPI's threadpool
```

`_mark_host_dead` only cools a host after **2 consecutive** connect failures (`ConnectError`/`ConnectTimeout`); any success calls `_clear_host_dead` and resets the counter. While cooled, `llm_call_async` raises `HTTPException(503, "… marked unreachable (cooldown active)")` immediately and `stream_llm` yields an `event: error` SSE chunk — no connect wait. Connect timeouts are deliberately short (`httpx.Timeout(connect=3.0, …)`): "a reachable peer answers SYN in <100ms even on Tailscale."

### 1.4 Retry, caching, message hygiene

- `llm_call_async` retries up to `LLMConfig.MAX_RETRIES = 3` with `RETRY_DELAY = 0.5` s for non-connect request errors; connect failures fail fast into the cooldown path.
- A small SHA-256-keyed response cache (`_get_cache_key` over url/model/messages/temp/max_tokens, capped at 128 entries with pop-half eviction) deduplicates identical calls.
- `_sanitize_llm_messages` strips Apollo-only metadata, drops orphan `role:"tool"` messages, prunes unanswered assistant `tool_calls`, and merges consecutive user messages (preserving multimodal block lists) — repairing the strict alternation/adjacency rules providers like DeepSeek enforce.
- Quirk tables: `_MAX_COMPLETION_TOKENS_MODELS = {"o1","o3","o4","gpt-4.5","gpt-5"}` switches the token-cap field; `_FIXED_TEMPERATURE_MODELS` omits `temperature` for reasoning models; `_THINKING_MODEL_PATTERNS` (`qwen3`, `qwq`, `deepseek-r1`, …) drives `<think>` repair during streaming.

### 1.5 SSE streaming protocol

`stream_llm` yields a uniform SSE chunk protocol regardless of provider:

```text
data: {"delta": "text"}                          — content tokens
data: {"delta": "…", "thinking": true}           — reasoning tokens
data: {"type": "tool_calls", "calls": [...]}     — accumulated native tool calls
data: {"type": "tool_call_delta", ...}           — live args for document tools
data: {"type": "usage", "data": {"input_tokens": N, "output_tokens": N}}
event: error\ndata: {"error": "...", "status": 503}
data: [DONE]
```

Three parser branches: native Ollama line-JSON (`message.thinking`, `message.content`, `done` with `prompt_eval_count`/`eval_count`), Anthropic events (`content_block_start/delta`, `message_start` usage incl. prompt-cache read/write logging, `message_stop` emitting accumulated `tool_use` blocks in OpenAI shape), and OpenAI-compatible `data:` lines. The OpenAI branch accumulates streamed tool calls in `_tc_acc` keyed by index — with a slot-allocation fix for Gemini's compat layer, which omits `index` on parallel calls (a `function.name` starts a new slot at `max(_tc_acc, default=-1) + 1`; arg-only deltas attach to the last slot) and whose opaque `extra_content` (`thought_signature`) is preserved verbatim. llama.cpp `timings` are passed through as `gen_tps`/`prefill_tps` so the UI shows true decode speed.

### 1.6 Fallback chains and activity tracking

All three call shapes have fallback variants taking an ordered list of `(url, model, headers)` candidates:

```python
# src/llm_core.py
def llm_call_with_fallback(candidates, messages, **kwargs) -> str:
    cands = [c for c in (candidates or []) if c and c[0] and c[1]]
    if not cands:
        raise HTTPException(503, "No model endpoint configured")
    last_err = None
    for i, (url, model, headers) in enumerate(cands):
        try:
            return llm_call(url, model, messages, headers=headers, **kwargs)
        except Exception as e:
            last_err = e
            tag = "primary" if i == 0 else "candidate"
            logger.warning(f"[fallback] {tag} {model} failed ({type(e).__name__}); trying next")
    raise last_err if last_err else HTTPException(503, "All fallback candidates failed")
```

`stream_llm_with_fallback(candidates, …)` retries the next candidate only on a **pre-content** failure; once any real output is emitted it never switches (that would duplicate streamed tokens). When a non-primary candidate answers, it injects `{"type": "fallback", "selected_model", "answered_by", "reason": _summarize_stream_error(last_error)}` so a misconfigured primary can't silently masquerade as working. The dead-host cooldown makes repeat attempts at an offline primary effectively instant.

Every real upstream request is also recorded by `note_model_activity(url, model)` into `_model_activity` (a `"{url}|{model}" → timestamp` map); `seconds_since_model_activity` lets status panels report endpoint/model freshness. `list_model_ids(base_chat_url)` enumerates models per provider (static `ANTHROPIC_MODELS` list for Anthropic, `/api/tags` for Ollama, `/models` for OpenAI-compatible) and `normalize_model_id` repairs basename-only matches (e.g. a session storing `qwen3-14b` against an endpoint listing `org/qwen3-14b`).

---

## 2. Local Model Lifecycle (`services/localmodels/`)

### 2.1 Scanner (`scanner.py`)

`scan_dirs(dirs)` walks each configured directory and returns `LocalModel(id, name, path, quant, kind, size_bytes, directory)` records. Filters:

- prunes cache/blob dirs in-place: `_SKIP_DIRS = {"cache", ".cache", "llama-cache", "ollama", ".ollama", "blobs", "tmp", ".git"}`;
- skips macOS AppleDouble files (`fn.startswith("._")`);
- skips multimodal projector files (`_is_projector`: name contains `mmproj`);
- for split GGUFs (`(.+)-(\d+)-of-(\d+)\.gguf`), registers only part 1;
- classifies `kind` as `embedding` when the name matches `_EMBED_HINT = (embed|nomic|bge|gte|e5|minilm)`, else `chat`;
- extracts the quant tag with `_QUANT_RE` (`IQ4_XS`, `Q4_K_M`, `BF16`, …);
- ids are stable content-free hashes: `"lm_" + sha1(path)[:16]`.

Directory resolution (`config.py`): settings key `local_model_dirs` → env `APOLLO_MODELS_DIRS` (pathsep- or comma-separated) → `DEFAULT_DIRS` (`/Volumes/MainStore/Development/AI_Models`, `~/Desktop/AI_Models`).

### 2.2 Registry (`registry.py`)

`sync_managed_endpoint(models)` upserts the single managed `ModelEndpoint` row with `base_url = "local://llama.cpp"`, name `"Local (llama.cpp)"`, writing chat-first, **name-deduped** `cached_models` — the picker is name-based, so the same model present in two configured dirs lists once.

### 2.3 Server manager (`server_manager.py`)

`LocalModelServer` keeps **one warm chat slot** and one independent embedding slot (`--embedding`), guarded by an `RLock`. `ensure_running(ref)` resolves by id or name (rescanning once on miss), returns the existing slot's `base_url` if that exact model is alive, otherwise **evicts** the current occupant (`terminate`, 10 s wait, `kill`) and launches:

```python
# services/localmodels/server_manager.py
port = _free_port(self._host)               # OS-assigned ephemeral port on 127.0.0.1
cmd = [binary, "--model", m.path,
       "--host", self._host, "--port", str(port),
       "-c", str(self._serving_context(m))]
if m.kind == "embedding":
    cmd.append("--embedding")
```

The binary is found via `_BIN_CANDIDATES` (`llama-server` on PATH, `~/.local/bin`, `~/llama.cpp/build/bin`, `/opt/homebrew/bin`, `/usr/local/bin`). Logs go to `tempfile.gettempdir()/apollo-llama-{port}.log` (parent fd closed after spawn to avoid a leak per launch).

**Serving context** is `min(known window, cap)` with the configured default as floor:

```python
# services/localmodels/server_manager.py
def _serving_context(self, m: LocalModel) -> int:
    cap = max(int(os.getenv("APOLLO_LLAMA_CONTEXT", "16384")), self._context)
    known = _lookup_known(m.name or m.id)   # from src/model_context.py
    if known:
        return max(self._context, min(known, cap))
    return cap
```

**Health wait** polls `GET {base}/health` every 0.5 s; timeout scales with model size — `max(self._health_timeout, size_gb * 40.0)` ("a 8.4GB 14B at -c 16384 needs >180s on this hardware. Allow ~40s/GB"). Early process exit surfaces the log tail in the error. `get_server()` is a process-wide singleton.

---

## 3. Context-Window Resolution (`src/model_context.py`)

`get_context_length(endpoint_url, model)` resolves the usable window with this precedence: llama.cpp `GET /slots` `n_ctx` (local endpoints only — the **actual** serving context) → `/v1/models` fields (`context_length`, `context_window`, `max_model_len`, `max_context_length`, `max_seq_len`, plus `meta`/`model_extra` nesting) → `KNOWN_CONTEXT_WINDOWS` → `DEFAULT_CONTEXT = 128000`. For local endpoints a smaller API value wins over the known max (the user set `-c`/`--max-model-len`); for cloud APIs the larger of API/known wins (APIs report low defaults). Results are cached per model id **except** local endpoints, which can restart with a different window under the same id.

The known table uses substring matching with **longest-key-wins** to avoid prefix shadowing:

```python
# src/model_context.py
def _lookup_known(model: str) -> Optional[int]:
    """Picks the LONGEST matching key so a short key never shadows a more
    specific one. Without this, 'o1' (200k) precedes 'o1-mini' (128k) ..."""
    name = model.lower()
    basename = name.split("/")[-1].split(":")[0]  # strip org and :free/:extended
    best_key = best_ctx = None
    for key, ctx in KNOWN_CONTEXT_WINDOWS.items():
        if key in basename or key in name:
            if best_key is None or len(key) > len(best_key):
                best_key, best_ctx = key, ctx
    return best_ctx
```

`estimate_tokens(messages)` uses `chars * 0.3` plus 4 tokens/message overhead — deliberately not the common chars/4, which "underestimates by ~20-30%".

---

## 4. Chat Pipeline (`routes/chat_routes.py` + `routes/chat_helpers.py`)

`POST /api/chat_stream` is the heavyweight path. In order:

1. **Repair & guard**: `_clear_orphaned_session_endpoint` (clears a session whose endpoint row was deleted), `_recover_empty_session_model` (Issue #587 — re-populates `sess.model` from the matching endpoint's `cached_models` when the picker showed a model but the row never persisted), then a hard 400 if `model` is still empty.
2. **Privileges before token spend** — `_enforce_chat_privileges(request, sess)` in `routes/chat_helpers.py` raises 403 if `sess.model` is outside the user's `allowed_models` allowlist, and 429 when the user's count of `role == "user"` messages in the last UTC day reaches `max_messages_per_day`. Admins get `ADMIN_PRIVILEGES` (empty allowlist, zero cap) so it no-ops.
3. **Mode resolution**: `mode` form field (`chat`/`agent`), with **intent auto-escalation** — `_message_needs_tools(message)` promotes chat→agent for todo/reminder/calendar intents, but the promotion withholds `{"bash","python","read_file","write_file","builtin_browser"}` so the model can't shell out for a request that never needed it. Incognito disables `manage_memory`, `search_chats`, `manage_skills`; per-user privilege flags and the admin `disabled_tools` setting add more.
4. **Context build** — `build_chat_context(...)` returns a `ChatContext` dataclass (`preface, rag_sources, web_sources, used_memories, messages, context_length, was_compacted, user, uprefs, preset, preprocessed, auto_opened_docs`). Internally: preset extraction, message preprocessing (CoT/YouTube/vision), `add_user_message`, event firing, memory/RAG/web preface via `chat_processor.build_context_preface`, model-id normalization from the endpoint cache, then `maybe_compact` + `trim_for_context` against the resolved window.
5. **Streaming** — chat mode calls `stream_llm_with_fallback` directly (no tools); agent mode runs `stream_agent_loop` with the disabled-tools set, the active document (explicit id → session lookup → in-memory tool-layer fallback, all owner-scoped), and a tool budget from setting `agent_max_tool_calls`.

**Memory commands** short-circuit the non-streaming path before any LLM work. `await chat_handler.handle_memory_command(sess, message)` (in `src/chat_handler.py`) checks the message against `MemoryManager.process_inline_memory_command`:

```python
# services/memory/memory.py
def process_inline_memory_command(self, message: str) -> Tuple[bool, str]:
    # Pattern for memory commands: "remember: X", "memorize: X", "save: X", etc.
    pattern = r'^(?:remember|memorize|save|note|store)[:\-]?\s+(.+)$'
    match = re.match(pattern, message.strip(), re.IGNORECASE)
    if match:
        return True, match.group(1).strip()
    return False, ""
```

On a match the handler dedups, appends the entry, writes both turns into the session ("Saved to memory: X"), and `/api/chat` returns immediately without calling the LLM.

**Image-generation routing**: `_is_image_generation_session(sess, owner)` bypasses text chat entirely when the model name starts with one of `_IMAGE_MODEL_PREFIXES = ("gpt-image", "dall-e", "chatgpt-image")`, or when the session endpoint matches an enabled `ModelEndpoint` with `model_type == "image"` **and** that endpoint's populated `cached_models` includes the selected model — the cache check stops an image endpoint on the same host from hijacking ordinary text models. The image path emits `tool_start`/`tool_output` SSE events around `do_generate_image` and saves the result with `tool_events` metadata.

**Deep research** rides the same SSE stream: the first vague research request triggers a clarifying-questions round (the session is parked in `research_pending` mode so the *next* message auto-triggers research); the actual run executes as a background task via `research_handler.start_research` with progress polled once per second into `research_progress` events (plus `: heartbeat N` comments when nothing changed), followed by `research_sources`, `research_findings`, and a `research_done` signal. Prior session research (report, findings, source URLs) is threaded back in for continuation.

**Metrics**: providers that send usage produce a `metrics` event with `input_tokens`/`output_tokens`, `context_percent` against the resolved window, and llama.cpp's true `gen_tps` mapped to `tokens_per_second` (`tps_source: "backend"`). When no usage arrives, the route estimates: `estimate_tokens(messages)` for input, `len(full_response) // 4` for output, wall-clock t/s, flagged `"usage_source": "estimated"`.

**Partial-save on disconnect** uses a guarded CancelledError pattern — the save is wrapped in its own `try` so a failure inside it can never mask the cancellation (which previously skipped the outer `finally` and leaked `_active_streams` entries):

```python
# routes/chat_routes.py
except (asyncio.CancelledError, GeneratorExit):
    try:
        if full_response:
            _stopped_content, _stopped_md = clean_thinking_for_save(
                full_response, {"stopped": True, "model": sess.model})
            sess.add_message(ChatMessage("assistant", _stopped_content, metadata=_stopped_md))
            if not incognito:
                session_manager.save_sessions()
    except Exception:
        logger.exception("Failed to save partial response on disconnect …")
    raise
finally:
    _active_streams.pop(session, None)
```

**`_active_streams` bookkeeping**: a module dict `session_id -> {"status","partial","query","is_research","mode"}` updated through `_stream_set` (which uses `.get()` to dodge a KeyError race with a sibling `finally` pop). The whole generator is wrapped in `_safe_stream` whose own `finally` pops the entry, and the stream runs **detached** — `agent_runs.start(session, _safe_stream())` survives the tab closing; the `StreamingResponse` merely subscribes. Companion endpoints: `GET /api/chat/resume/{session_id}` (re-subscribe), `POST /api/chat/stop/{session_id}` (the Stop button must call this; closing SSE no longer cancels), `GET /api/chat/stream_status/{session_id}` (reports `{"status": "streaming", "detached": true}` when only the detached run remains).

Reasoning tokens (`thinking: true`) are forwarded for the live indicator but never folded into the saved reply; thinking-model output is stripped via `clean_thinking_for_save` before persistence.

---

## 5. Memory Extraction & Retrieval (`services/memory/`)

Storage is two-layer: a JSON entry store managed by `MemoryManager` (`services/memory/memory.py` — entries `{id, text, source, category, owner, …}` with owner scoping and legacy migration) and a **ChromaDB** vector index (`services/memory/memory_vector.py`, collection `apollo_memories`, cosine space). Embeddings come from a shared `EmbeddingClient` (`src/embeddings.py`) whose zero-config fallback is **local fastembed** (ONNX, ~50 MB, cached under `data/fastembed_cache`); the store keeps pre-computed vectors since "ChromaDB does not manage embedding". Scores are converted back from distance: `similarity = 1.0 - distance`.

**Extraction** (`memory_extractor.extract_and_store`) runs as a fire-and-forget background task after each response: take the last `CONTEXT_WINDOW` messages, strip non-text multimodal blocks, ask the session's model for JSON facts at `temperature=0.1, max_tokens=500` (with regex-based `_fallback_memory_candidates` if the LLM path fails), then triple-dedup each candidate — vector `find_similar(threshold=0.72)` first (errors fall through rather than aborting the batch, since `.healthy` is only set at init), then exact-text `find_duplicates`, then fuzzy `_is_text_duplicate(threshold=0.6)`.

**Retrieval** uses semantic search via `MemoryVectorStore.search(query, k=8)` where available, alongside `MemoryManager.get_relevant_memories` — a keyword-category heuristic that classifies the query (identity/contact/preference/task/fact word lists), special-cases identity memories (full-name regex, "my name is"-style markers), and ranks by text similarity with `threshold=0.05, max_items=8`. The memories actually injected are surfaced to the UI as the `memories_used` SSE event.

---

## 6. Event Bus Threshold Triggers (`src/event_bus.py`)

`fire_event(event_name, owner)` schedules `_handle_event` on the running loop (safe from sync or async contexts). Ownerless events (internal code paths with no request middleware) are routed to the **first admin account** by `_resolve_event_owner` — otherwise built-in tasks would fire once per account. The handler implements counter-threshold triggering over `ScheduledTask` rows with `trigger_type == "event"`:

```python
# src/event_bus.py
for task in tasks:
    threshold = task.trigger_count or 1
    task.trigger_counter = (task.trigger_counter or 0) + 1
    if task.trigger_counter >= threshold:
        task.trigger_counter = 0
        # Persist the trigger before handing off — `next_run <= now` makes the
        # trigger survive a process restart after the counter has reset.
        task.next_run = datetime.utcnow()
        db.commit()
        await _task_scheduler.run_task_now(task.id)
    else:
        db.commit()
```

So a task with `trigger_event = "chat.message"` and `trigger_count = 5` runs on every fifth message. `fire_message_event` in `routes/chat_helpers.py` feeds this (and the webhook manager) on each non-incognito send.

---

## 7. Paperclip Floor Layout — Data-Side Summary

(Rendering detail in doc 05 §4; this is the algorithm contract.)

**Inputs**: a stream of events with types in `FLOOR_EVENT_TYPES = {agent.status, heartbeat.run.queued, heartbeat.run.status, heartbeat.run.log, heartbeat.run.event, activity.logged}` (`services/paperclip/events.py`), reduced by `applyFloorEvent` into per-agent records `{id, name, role, status, zone, task, thinking, transcript[], tools[], messages[], doneAt?, lastX?, lastY?}`.

**Zone mapping** (`zoneForStatus` in `static/js/paperclip.js`): status strings collapse into five zones — `working` (running/working/in_progress/active/thinking), `review`, `blocked` (blocked/error/failed/crashed), `done` (done/complete/completed/success), default `backlog`.

**Zone → position rules** (`workspacePoint`):

| Zone | Position rule |
|---|---|
| `backlog`, `working` | The agent's **own desk** — `deskAssignments` hands out `OFFICE_DESKS` slots in first-seen order, wrapping with a `+5x/+4y` lap offset; agent stands/sits at the chair (`desk.y + 3`). |
| `review`, `blocked` | The matching `SHARED_STATIONS` corner (Review Table 76,18 / Help Bar 14,74), fanned by a fixed 5-entry spread keyed by per-zone arrival index. |
| `done` | The exit door: `EXIT_SPOT {x:7, y:84}` offset `+5x/−3y` per queued agent. |

**Exit-departure state machine**: entering `done` stamps `doneAt`; any later non-done event (status change, log, tool event) clears it. While `now − doneAt ≤ EXIT_LINGER_MS` (20 s) the agent lingers at the door with a `"… Heading out!"` callout; past that the layout filters the agent out of the rendered scene entirely — though its desk persists and the Board view still lists it. New activity brings it back (the next event clears `doneAt` and re-zones it).

**Conversations decay** after `CONVERSATION_WINDOW_MS = 45 s`; the newest one physically moves the sender next to the receiver (`to.x ± 13, to.y + 4`). Movement is consumed exactly once per change: `commitWorkspaceLayout` persists each rendered position into `lastX/lastY`, and the next layout walks from there only if the target differs.
