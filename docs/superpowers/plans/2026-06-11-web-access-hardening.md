# Web Access Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix all findings from the deep audit of the web-access feature — 4 critical (Docker regression, incognito leak, unpinned supply chain, dead admin default), 8 medium (watchdog, update flow, context-blind decider, DDG backoff, sidecar logging, failure visibility, non-admin UX, splash text), and the low-priority hygiene items.

**Architecture:** All changes are surgical additions to the existing web-access feature on branch `feature/local-model-web-access`. No new subsystems. Backend fixes are TDD'd; frontend fixes are read-verified + syntax-checked.

**Tech Stack:** Same as the feature: Python/FastAPI/httpx/pytest, vanilla JS, bash/PowerShell.

**Conventions:** Tests via `venv/bin/python -m pytest <file> -v` from `/Users/Antman/Apollo`. Commit after each green task. JS syntax check: `node --input-type=module --check < <file>`.

**Key facts for implementers:**
- Verified-good SearXNG commit (smoke-tested on this machine): `4dd0bf48670727f6ae1086ffa72e76f6eb869741`
- Session history: `sess.history`, items have `.role` / `.content` (content may be a list of `{"type":"text","text":...}` parts — see `routes/chat_helpers.py:137-145` for the extraction idiom)
- Frontend admin flag: `window._isAdmin` (settings.js:21)
- web_sources SSE event: `routes/chat_routes.py:662-663`
- Deep research fan-out: `src/deep_research.py:_search_and_extract` (~line 502, `asyncio.gather`)

---

## BATCH 1 — CRITICAL (must land before merge)

### Task 1: Docker / explicit-env precedence fix

**Files:**
- Modify: `services/search/providers.py` (`_get_search_instance`, lines 43–55)
- Modify: `services/search/core.py` (`_searxng_definitely_down`, lines 95–110)
- Test: `tests/test_search_immediate_fallback.py` (extend)

**The bug:** Docker compose sets env `SEARXNG_INSTANCE=http://searxng:8080` and bundles SearXNG, but the managed-sidecar branch shadows it: managed default true + sidecar not installed in container → chain skips SearXNG → silent DDG-only for all Docker users.

**Rule after fix:** explicit `search_url` setting > explicit `SEARXNG_INSTANCE` env (i.e. `os.environ.get("SEARXNG_INSTANCE")` is set at all) > managed sidecar **if installed** > `SEARXNG_INSTANCE` constant default.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_search_immediate_fallback.py`)

```python
def test_explicit_env_beats_managed_sidecar(monkeypatch):
    """Docker sets SEARXNG_INSTANCE; the managed sidecar must not shadow it."""
    from services.search.providers import _get_search_instance
    monkeypatch.setenv("SEARXNG_INSTANCE", "http://searxng:8080")
    with patch("services.search.providers._get_search_settings",
               return_value=_settings()):
        assert _get_search_instance() == "http://searxng:8080"


def test_explicit_env_prevents_chain_skip(monkeypatch):
    """With an explicit env instance, never skip searxng on the sidecar's behalf."""
    monkeypatch.setenv("SEARXNG_INSTANCE", "http://searxng:8080")
    with patch("services.search.core._get_search_settings", return_value=_settings()):
        chain = _build_provider_chain("searxng")
    assert chain[0] == "searxng"


def test_managed_but_not_installed_falls_to_env_default(monkeypatch):
    """No env, managed on, sidecar NOT installed -> constant default, not 8893."""
    from services.search.providers import _get_search_instance, SEARXNG_INSTANCE
    monkeypatch.delenv("SEARXNG_INSTANCE", raising=False)
    with patch("services.search.providers._get_search_settings",
               return_value=_settings()), \
         patch("services.searxng.runtime.get_runtime") as rt:
        rt.return_value.installed = False
        assert _get_search_instance() == SEARXNG_INSTANCE
```

NOTE: the runtime has no `installed` attribute yet — Step 3 adds one (delegating to its config). Also check existing tests `test_instance_url_prefers_managed_sidecar` / `test_skips_searxng_when_sidecar_down`: they patch `get_runtime` without `installed`; a `MagicMock` auto-attr is truthy so they keep passing, but VERIFY and set `rt.return_value.installed = True` explicitly in them for clarity.

- [ ] **Step 2: Run — expect the env tests to FAIL** (current code returns the sidecar URL / skips the chain).

- [ ] **Step 3: Implement**

`services/searxng/runtime.py` — add to `SearxngRuntime`:

```python
    @property
    def installed(self) -> bool:
        return self._cfg_provider().installed
```

`services/search/providers.py`:

```python
def _get_search_instance() -> str:
    """Active search API URL.

    Precedence: explicit search_url setting > explicit SEARXNG_INSTANCE env
    (deployment-level, e.g. Docker compose) > managed sidecar when actually
    installed > built-in default constant.
    """
    settings = _get_search_settings()
    url = (settings.get("search_url") or "").strip()
    if url:
        return url.rstrip("/")
    env_url = (os.environ.get("SEARXNG_INSTANCE") or "").strip()
    if env_url:
        return env_url.rstrip("/")
    if settings.get("searxng_managed", True):
        try:
            from services.searxng.runtime import get_runtime
            rt = get_runtime()
            if rt.installed:
                return rt.url
        except Exception:
            pass
    return SEARXNG_INSTANCE
```

`services/search/core.py` — `_searxng_definitely_down` gains two early outs:

```python
    settings = _get_search_settings()
    if (settings.get("search_url") or "").strip():
        return False
    if (os.environ.get("SEARXNG_INSTANCE") or "").strip():
        return False  # explicit deployment instance (e.g. Docker) — let HTTP decide
    if not settings.get("searxng_managed", True):
        return False
    try:
        from services.searxng.runtime import get_runtime
        rt = get_runtime()
        if not rt.installed:
            return True   # managed-but-absent: skip with no probe at all
        return not rt.is_serving()
    except Exception:
        return False
```

Add `import os` to core.py if missing.

- [ ] **Step 4: Run the whole fallback test file + search tests.** All green.
- [ ] **Step 5: Commit** — `fix(search): explicit SEARXNG_INSTANCE env beats managed sidecar (Docker regression)`

### Task 2: Incognito suppresses web search

**Files:**
- Modify: `routes/chat_routes.py` (both endpoints, right after the `resolve_web_access` calls at ~:290 and ~:485)
- Test: `tests/test_chat_web_access_wiring.py` (extend)

- [ ] **Step 1: Failing test**

```python
@pytest.mark.asyncio
async def test_incognito_suppresses_web():
    """Incognito must not leak queries to search engines (mirrors RAG gating)."""
    from src.web_decider import resolve_web_access, apply_incognito
    use_web, allow_ws, decision = await resolve_web_access(
        "always", "chat", "latest news", None, None)
    use_web, decision = apply_incognito(True, use_web, decision)
    assert use_web is False
    assert decision == "incognito-off"
```

- [ ] **Step 2: Implement** — append to `src/web_decider.py`:

```python
def apply_incognito(incognito: bool, use_web, decision):
    """Incognito chats must not send queries to search engines.

    Mirrors the RAG/memory suppression in build_chat_context. Pre-search is
    forced off; agent tools stay (the user explicitly invokes those).
    """
    if incognito and use_web:
        return False, "incognito-off"
    return use_web, decision
```

Then in `routes/chat_routes.py`, streaming endpoint (right after the existing `resolve_web_access` block — `incognito` is parsed at ~:382):

```python
        use_web, _web_decision = apply_incognito(incognito, use_web, _web_decision)
```

Import alongside resolve_web_access (`from src.web_decider import resolve_web_access, apply_incognito`). The non-streaming `/api/chat` endpoint has no incognito field (verify — it's a form-less JSON ChatRequest; check whether ChatRequest has incognito. If it doesn't, skip the non-streaming change and say so).

- [ ] **Step 3: Frontend belt-and-braces** — in `static/js/chat.js`, where `_webMode` is computed, force `'off'` when the incognito toggle is active:

```javascript
      const _incog = el('incognito-toggle');
      if (_incog && _incog.checked) _webMode = 'off';
```

(Place after the transient-override line. Read the surrounding code to confirm the toggle id — it's used nearby at the `incognito` form append.)

- [ ] **Step 4: Tests + syntax check + commit** — `fix(chat): incognito chats never send web search queries`

### Task 3: Pin the SearXNG checkout

**Files:**
- Modify: `scripts/setup-searxng.sh`, `scripts/setup-searxng.ps1`
- Modify: `README.md` (the line documenting the installer, mention the pin)

- [ ] **Step 1: Bash** — replace the clone/pull block:

```bash
# Pinned, not HEAD — same discipline as the Docker image pin. This commit was
# smoke-tested end-to-end (healthz + JSON search) on 2026-06-11. Override with
# SEARXNG_GIT_REF at your own risk.
REF="${SEARXNG_GIT_REF:-4dd0bf48670727f6ae1086ffa72e76f6eb869741}"

echo "==> Fetching SearXNG source (pinned: ${REF:0:9})"
if [ ! -d "$SRC/.git" ]; then
  git clone https://github.com/searxng/searxng "$SRC"
fi
git -C "$SRC" fetch --quiet origin || echo "(fetch failed -- using existing objects)"
git -C "$SRC" checkout --quiet "$REF"
```

NOTE: the pinned checkout needs full history or at least the pinned object — `--depth 1` of HEAD won't contain an older sha. Use a full clone (searxng is ~100MB) or `git fetch origin <ref> --depth 1` then checkout FETCH_HEAD; pick whichever you verify works, and verify it by running the script fresh in a temp dir: `HOME_DIR_OVERRIDE`? The script hardcodes paths — test by moving `data/searxng/src` aside, running the script, confirming `git -C data/searxng/src rev-parse HEAD` equals the pin, then restoring (the existing install at that exact sha can simply be kept — rerun the script over it to confirm idempotency instead).

- [ ] **Step 2: PowerShell** — mirror the same logic in `setup-searxng.ps1` ($env:SEARXNG_GIT_REF default, fetch + checkout, no PS-incompatible syntax).

- [ ] **Step 3: Validate `SEARXNG_PORT` as an integer in both scripts** (audit Low item) — bash: `case "$PORT" in (*[!0-9]*|'') echo "invalid SEARXNG_PORT"; exit 1;; esac`; PowerShell: `[int]::TryParse(...)` guard.

- [ ] **Step 4: Run the bash script against the existing checkout** (idempotency + pin): expect `rev-parse HEAD` == pin and exit 0. Commit — `fix(searxng): pin installer checkout to smoke-tested commit; validate port`

### Task 4: Minimal sidecar environment

**Files:**
- Modify: `services/searxng/runtime.py` (`start()`, the `env = dict(os.environ)` line)
- Test: `tests/test_searxng_runtime.py` (extend)

- [ ] **Step 1: Failing test**

```python
def test_spawn_env_is_minimal(tmp_path, monkeypatch):
    """The sidecar must not inherit Apollo's secrets (API keys etc.)."""
    monkeypatch.setenv("TAVILY_API_KEY", "sk-secret")
    monkeypatch.setenv("DATA_BRAVE_API_KEY", "sk-secret2")
    spawned = []

    def spawn(*a, **kw):
        p = FakeProc(*a, **kw)
        spawned.append(p)
        return p

    calls = {"n": 0}

    def check(u, t=2.0):
        calls["n"] += 1
        return calls["n"] > 1

    rt = SearxngRuntime(cfg_provider=lambda: _cfg(tmp_path), spawn=spawn, health_check=check)
    assert rt.start() is True
    env = spawned[0].kwargs["env"]
    assert "TAVILY_API_KEY" not in env
    assert "DATA_BRAVE_API_KEY" not in env
    assert env["SEARXNG_SETTINGS_PATH"].endswith("settings.yml")
    assert "PATH" in env
```

- [ ] **Step 2: Implement** — replace the env construction in `start()`:

```python
            # Minimal env: the sidecar needs none of Apollo's secrets.
            _PASS = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "TEMP", "TMP",
                     "SYSTEMROOT", "WINDIR", "USERPROFILE")
            env = {k: os.environ[k] for k in _PASS if k in os.environ}
            env["SEARXNG_SETTINGS_PATH"] = cfg.settings_path
```

- [ ] **Step 3: Run all sidecar tests; then a LIVE check** — start the real sidecar once via a snippet (`SearxngRuntime` with real config; or simply boot the app) and confirm `/healthz` still returns OK with the minimal env (SearXNG may need none of the rest — verify, and if it fails to boot, check what it's missing and add to the allowlist with a comment). Stop it after.
- [ ] **Step 4: Commit** — `fix(searxng): spawn sidecar with minimal allowlisted env`

### Task 5: Make the admin web-access default real

**Files:**
- Modify: `src/web_decider.py` (`resolve_web_access` settings-fallback branch)
- Modify: `src/settings.py` (comment only), `static/index.html` (dropdown options), `static/js/settings.js` (hint), `static/app.js` (seed initial mode from server)
- Test: `tests/test_web_decider_resolve.py` (extend)

**Design (decided during planning):**
- Setting `web_access_mode` accepted values become `manual | off | auto | always` (add `off` to the dropdown).
- Backend fallback (client omitted `web_access`): `manual` → legacy passthrough (unchanged). `off`/`auto`/`always` → that mode, **except** when the client sent explicit legacy intent (`use_web` or `allow_web_search` truthy "true") — explicit legacy flags always win (protects old API clients if an admin sets `auto`).
- Frontend: when NO `webmode_*` key exists in localStorage, seed from the server setting: `off`→off, `auto`→auto, `always`→always, `manual`→auto (today's effective default, keeps fresh-install behavior identical).

- [ ] **Step 1: Failing tests**

```python
@pytest.mark.asyncio
async def test_settings_fallback_respects_explicit_legacy_flags(monkeypatch):
    monkeypatch.setattr("src.settings.load_settings",
                        lambda: {"web_access_mode": "auto"})
    # Old client explicitly asked for web — never override with the decider.
    use_web, allow_ws, decision = await resolve_web_access(
        None, "chat", "write a poem", "true", None)
    assert use_web == "true"
    assert decision is None


@pytest.mark.asyncio
async def test_settings_off_applies_when_no_legacy_intent(monkeypatch):
    monkeypatch.setattr("src.settings.load_settings",
                        lambda: {"web_access_mode": "off"})
    use_web, allow_ws, decision = await resolve_web_access(
        None, "chat", "latest news", None, None)
    assert use_web is False
    assert decision == "off"
```

- [ ] **Step 2: Implement backend** — in `resolve_web_access`, replace the fallback branch:

```python
    if mode not in ("off", "auto", "always"):
        from src.settings import load_settings
        cfg = (load_settings().get("web_access_mode") or "manual").strip().lower()
        _legacy_intent = (str(use_web).lower() == "true"
                          or str(allow_web_search).lower() == "true")
        if cfg not in ("off", "auto", "always") or _legacy_intent:
            return use_web, allow_web_search, None
        mode = cfg
```

- [ ] **Step 3: Frontend seed** — in `static/app.js` `setupWebToggle` (or near the settings fetch at ~:1370 — read both; the seed must run before the first send): when `loadToggleState()` has neither `webmode_chat` nor `webmode_agent` nor legacy `web_chat`/`web_agent`, fetch the settings (reuse the existing settings fetch result if reachable; otherwise a one-off `fetch(API_BASE + '/api/auth/settings')`) and `saveWebMode('chat', seed); saveWebMode('agent', seed)` with the mapping above, then re-apply the button. Guard all failures silently (default stays 'auto').
  Also add the `off` option to the `#web-access-mode` select in `static/index.html` and retitle: `manual` → "Manual (legacy clients only)", and a short hint span under the row: "Default for new browsers and API clients; your in-chat toggle always wins."

- [ ] **Step 4: Tests + syntax checks + commit** — `fix(chat): admin web_access_mode seeds new clients and governs API fallback`

---

## BATCH 2 — MEDIUM

### Task 6: Sidecar watchdog (lazy restart)

**Files:** `services/searxng/runtime.py`, test extend `tests/test_searxng_runtime.py`

- [ ] **Step 1: Failing test**

```python
def test_maybe_restart_respawns_dead_proc(tmp_path):
    spawned = []

    def spawn(*a, **kw):
        p = FakeProc(*a, **kw)
        spawned.append(p)
        return p

    calls = {"n": 0}

    def check(u, t=2.0):
        calls["n"] += 1
        return calls["n"] in (2,)  # healthy once after first spawn, then dead

    rt = SearxngRuntime(cfg_provider=lambda: _cfg(tmp_path), spawn=spawn, health_check=check)
    rt.start()
    assert len(spawned) == 1
    spawned[0].killed = True          # simulate crash
    rt._health_cache = None
    assert rt.maybe_restart() is True  # schedules a restart
    import time as _t
    for _ in range(50):                # restart happens on a background thread
        if len(spawned) >= 2:
            break
        _t.sleep(0.05)
    assert len(spawned) == 2


def test_maybe_restart_rate_limited(tmp_path):
    rt = SearxngRuntime(cfg_provider=lambda: _cfg(tmp_path),
                        spawn=FakeProc, health_check=lambda u, t=2.0: False)
    rt._last_restart_attempt = None
    assert rt.maybe_restart() is True
    assert rt.maybe_restart() is False  # within the cooldown window
```

- [ ] **Step 2: Implement** — add to `SearxngRuntime`:

```python
_RESTART_COOLDOWN = 300.0  # seconds between automatic restart attempts


    def maybe_restart(self) -> bool:
        """Schedule a background restart of a crashed sidecar, at most once
        per cooldown window. Returns True if a restart was scheduled.

        Called from the search hot path (via _searxng_definitely_down) when
        the managed sidecar is installed but not serving — so it must never
        block and never raise.
        """
        try:
            cfg = self._cfg_provider()
            if not cfg.enabled or not cfg.installed:
                return False
            now = time.monotonic()
            if self._last_restart_attempt is not None and \
                    (now - self._last_restart_attempt) < _RESTART_COOLDOWN:
                return False
            self._last_restart_attempt = now
            threading.Thread(target=self.start, name="searxng-restart",
                             daemon=True).start()
            return True
        except Exception:
            return False
```

(init `self._last_restart_attempt: float | None = None` in `__init__`.) Then in `services/search/core.py` `_searxng_definitely_down`, where it returns `not rt.is_serving()` — when that is about to return True (sidecar installed but down), call `rt.maybe_restart()` first (fire-and-forget) so the NEXT search finds it back up:

```python
        down = not rt.is_serving()
        if down:
            rt.maybe_restart()
        return down
```

- [ ] **Step 3: All sidecar + fallback tests green. Commit** — `feat(searxng): lazy watchdog — auto-restart crashed sidecar (5 min cooldown)`

### Task 7: Update flow actually updates

**Files:** `routes/search_routes.py` (`searxng_install` worker), test extend `tests/test_searxng_routes.py`

- [ ] **Step 1:** In the install worker `_run()`, BEFORE running the script: if the runtime owns a live process, stop it (`from services.searxng.runtime import get_runtime; get_runtime().stop()`); append a log line "stopping sidecar for update". After success the existing `get_runtime().start()` now actually starts the new code (it no longer no-ops on the old process). Mind the reuse path: if an EXTERNAL (not-spawned-by-us) SearXNG is serving, `stop()` won't kill it and `start()` will reuse it — acceptable; log it.
- [ ] **Step 2:** Test: patch get_runtime; POST install; assert `stop` called before the thread target runs the script (structure the worker so stop happens inside `_run`, testable by invoking the captured thread target directly with subprocess patched).
- [ ] **Step 3: Commit** — `fix(searxng): stop sidecar before update so new version takes effect`

### Task 8: Context-aware decider for follow-ups

**Files:** `src/web_decider.py`, `routes/chat_routes.py` (streaming endpoint), tests extend

**Design:** `resolve_web_access` gains `prev_message: str = ""`. In auto+chat, if `heuristic_decision(message)` is `no` AND the message looks like a short follow-up (< 120 chars and starts with a continuation cue: and/what about/how about/also/but/then/ok/same/now/more — case-insensitive), re-run the heuristic on `prev_message + "\n" + message`; a `yes` there upgrades to `ambiguous` handling (i.e. searched only via tie-break or treated as yes? **Decision: treat combined-yes as yes** — the prior turn establishes live context).

- [ ] **Step 1: Failing tests**

```python
@pytest.mark.asyncio
async def test_follow_up_inherits_web_context():
    with patch("src.web_decider._ask_utility_model", new=AsyncMock(return_value=None)):
        use_web, _, decision = await resolve_web_access(
            "auto", "chat", "and what about tomorrow?", None, None,
            prev_message="weather in Stockholm today")
    assert use_web is True
    assert decision == "auto-search"


@pytest.mark.asyncio
async def test_follow_up_without_web_context_stays_no():
    use_web, _, decision = await resolve_web_access(
        "auto", "chat", "and what about the second function?", None, None,
        prev_message="refactor this function to use a dict")
    assert use_web is False
```

- [ ] **Step 2: Implement** — in `web_decider.py`:

```python
_FOLLOW_UP_RE = re.compile(
    r"^(and|what about|how about|also|but|then|ok(ay)?|same|now|more|any update)\b", re.I)


def _is_short_follow_up(message: str) -> bool:
    msg = (message or "").strip()
    return bool(msg) and len(msg) < 120 and bool(_FOLLOW_UP_RE.match(msg))
```

`decide_use_web(message, prev_message="")`: after a `no` verdict, if `_is_short_follow_up(message)` and prev_message, re-classify `f"{prev_message}\n{message}"` — `yes` → True, `ambiguous` → existing tie-break path. `resolve_web_access(..., prev_message="")` threads it through. In `routes/chat_routes.py` streaming endpoint, extract the previous user message before the resolve call:

```python
        _prev_user_msg = ""
        try:
            for _m in reversed(getattr(sess, "history", []) or []):
                if getattr(_m, "role", "") == "user":
                    _c = _m.content
                    if isinstance(_c, list):
                        _c = next((i.get("text", "") for i in _c
                                   if isinstance(i, dict) and i.get("type") == "text"), "")
                    _prev_user_msg = str(_c)[:500]
                    break
        except Exception:
            pass
```

CAREFUL: the current message may already have been appended to history by this point in the flow — check where `add_user_message` happens (it's inside `build_chat_context`, which runs AFTER the resolve call, so history's last user message IS the previous turn — verify by reading, and if the order ever differs in the non-streaming endpoint, skip prev there).

- [ ] **Step 3: All decider tests green. Commit** — `feat(chat): follow-up questions inherit web context from the previous turn`

### Task 9: DDG backoff under deep research + no double-retry on 429

**Files:** `src/deep_research.py` (`_search_and_extract`), `services/search/core.py` (retry loops), tests where feasible

- [ ] **Step 1:** In `_search_and_extract`, bound the fan-out with a semaphore + jitter:

```python
        _sem = asyncio.Semaphore(2)

        async def _bounded_search(q):
            async with _sem:
                res = await self._search(q)
                await asyncio.sleep(0.4 + random.random() * 0.6)  # ~0.4–1.0s spacing
                return res

        search_tasks = [_bounded_search(q) for q in queries]
```

(`import random` if missing; keep `return_exceptions=True` on the gather.)
- [ ] **Step 2:** In `services/search/core.py`, both provider retry loops (`searxng_search_results` ~line 181 and `comprehensive_web_search` ~line 290): on `RateLimitError`, `break` out of the attempt loop for that provider instead of retrying immediately (an instant retry of a 429 is counterproductive). The except clause already distinguishes the type in `searxng_search_results`; in `comprehensive_web_search` the catch is generic `Exception` — add a targeted `except RateLimitError` arm before it (import exists at top).
- [ ] **Step 3:** Test the core change: patch `_call_provider` to raise `RateLimitError` and count calls — expect exactly 1 attempt for that provider. Run search tests. Commit — `fix(search): back off DDG — jittered research fan-out, no instant 429 retry`

### Task 10: Sidecar logging + status log tail

**Files:** `services/searxng/runtime.py`, `routes/search_routes.py` (status), `tests` extend

- [ ] **Step 1:** Runtime: replace DEVNULL with an append handle to `logs/searxng.log` (`from src.constants import BASE_DIR`; `LOGS_DIR = os.path.join(BASE_DIR, "logs")`, `os.makedirs(..., exist_ok=True)`); truncate the file at spawn when > 5 MB. Close the handle in `stop()`/after process exit (keep a ref on self). Test: spawn kwargs `stdout` is not DEVNULL and points at a file object whose name ends `searxng.log` (use tmp_path by monkeypatching the log path — make the path a module-level `_LOG_PATH` for patchability).
- [ ] **Step 2:** Status endpoint: add `"runtime_log_tail": <last 20 lines of logs/searxng.log if exists>` (read defensively).
- [ ] **Step 3: Commit** — `feat(searxng): sidecar logs to logs/searxng.log, tail in status endpoint`

### Task 11: Search-failure visibility in chat

**Files:** `routes/chat_routes.py` (streaming, near the web_sources yield at ~:662), `static/js/chat.js` (web_sources/stream handler), test extend `tests/test_chat_web_access_wiring.py` if route-testable, else frontend-only verify

- [ ] **Step 1:** Backend: after `web_sources = ctx.web_sources` (~:643), when the resolved web intent was on but nothing came back, emit a failure event:

```python
            if (_web_decision in ("always", "auto-search") or str(use_web).lower() == "true") \
                    and not web_sources:
                yield f"data: {json.dumps({'type': 'web_search_failed'})}\n\n"
```

(Names `_web_decision`/`use_web` are in scope in this generator — verify; if the yield block lives in a nested function without them, thread the flag through the same way `web_sources` is.)
- [ ] **Step 2:** Frontend: in the SSE dispatcher in `static/js/chat.js` where `web_sources` is handled (~:1751), add a `web_search_failed` case: `spinner.updateMessage('Web search unavailable')` plus `uiModule.showToast('Web search failed — answering without live results', 3000)` (check uiModule reference shape in that scope; reuse however web_sources case accesses helpers).
- [ ] **Step 3: Syntax check, backend chat tests green. Commit** — `feat(chat): surface web-search failures instead of silently answering stale`

### Task 12: Settings UX — non-admin row, install log, splash text, success toast

**Files:** `static/js/settings.js`, `static/index.html`, `static/app.js`

- [ ] **Step 1:** Non-admin: in `refreshSearxngStatus`, when the status fetch returns 401/403 (check `res.status`), replace the row content with the static text "Managed by your admin — searches fall back to DuckDuckGo when SearXNG is unavailable" and hide the Install button. (Admin flag alternative: `window._isAdmin === false` → skip the fetch entirely.)
- [ ] **Step 2:** Install log: while `s.installing` (and on `install_ok === false`), render `s.log_tail` (and `runtime_log_tail` on failure) into a small scrollable `<pre id="searxng-install-log">` under the row (create it in index.html next to the status row, hidden by default). Show a toast on completion: success → "SearXNG installed and running"; failure → "SearXNG install failed — see log".
- [ ] **Step 3:** Splash text (`static/app.js` `_toolSplashes.web`): replace with tri-state text: `'Cycles Off → Auto → Always. Auto lets Apollo decide per message and searches privately via your local SearXNG (DuckDuckGo fallback). Always searches every message.'` Also fire the splash only when cycling to 'always' OR on the FIRST auto-fired search: expose `window._showToolSplash = _showToolSplash;` next to `window._setWebMode`, and in chat.js's web_sources handler call `if (holder._webMode === 'auto' && window._showToolSplash) window._showToolSplash('web');` (the splash self-limits to 2 showings via SPLASH_COUNT_KEY, so this is safe to call repeatedly).
- [ ] **Step 4:** Syntax checks; commit — `feat(ui): settings install log + non-admin explainer; tri-state splash`

---

## BATCH 3 — LOW / HYGIENE

### Task 13: Health probe asserts it's really SearXNG

**Files:** `services/searxng/runtime.py` (`_http_ok`), test extend

- [ ] `_http_ok` for the healthz URL: read the body and require it to start with `b"OK"` (SearXNG's healthz returns `OK`; verified live during Task 4 of the original plan). Keep the generic `<500` behavior as a fallback ONLY if body-read fails? No — fail closed: a foreign service on 8893 should read as not-serving so the chain skips fast. Update/extend runtime tests (FakeProc health fakes are injected, so only `_http_ok` itself needs a small new test with a stubbed urlopen via monkeypatch).
- [ ] Commit — `fix(searxng): health probe requires a SearXNG OK body, not any HTTP service`

### Task 14: "starting" status state

**Files:** `services/searxng/runtime.py` (`status()`), `static/js/settings.js` (labels), test extend

- [ ] `status()`: between the `is_serving()` and `_failed` checks, add: if `self._proc is not None and self._proc.poll() is None` → `"starting"`. settings.js labels map gains `starting: '⟳ starting…'`. Test: spawned-but-not-yet-healthy runtime reports "starting" not "failed"/"stopped".
- [ ] Commit — `feat(searxng): report 'starting' while the sidecar boots`

### Task 15: Toggle state visibility polish (mobile + labels)

**Files:** `static/style.css`, `static/index.html`

- [ ] CSS: give the always state its own marker so touch users can distinguish auto/always without the title tooltip: `#web-toggle-btn.active:not(.web-auto)::after { content: '•'; ... }` (same positioning block as the 'A' badge; reuse/extract a shared rule). Verify no conflict with the existing `.web-auto::after`.
- [ ] index.html: dropdown option labels per Task 5's design (done there) — here verify only; plus the hint text under the web-access row if Task 5 didn't add it.
- [ ] Commit — `style(ui): distinguish always vs auto on the web toggle for touch users`

### Task 16: Final verification + docs + PR update

- [ ] Full suite: `venv/bin/python -m pytest tests/ -q 2>&1 | tail -3` — green.
- [ ] Live e2e re-run (mirrors original Task 14): boot app → sidecar serves → in-process search tagged searxng → `pkill -f searx.webapp` → wait >2s → search again → DDG immediately AND (new) confirm the watchdog respawned the sidecar within the cooldown logic by calling `_searxng_definitely_down()` once and checking `pgrep -f searx.webapp` after a few seconds. Incognito check: in-process `apply_incognito(True, True, 'always')` → `(False, 'incognito-off')`. Docker-path check: `SEARXNG_INSTANCE=http://fake:8080 venv/bin/python -c "from services.search.providers import _get_search_instance; print(_get_search_instance())"` → `http://fake:8080`. Clean up all processes.
- [ ] README: add one sentence each for the pinned installer, the watchdog, incognito behavior, and `logs/searxng.log`.
- [ ] Push; comment on PR #2 summarizing the hardening batch.

---

## Self-Review (done at plan time)

- **Finding coverage:** security #1→Task 3, #2→Task 4, #3(info)→comment folded into Task 7's file, port→Task 3; resilience K1→Task 1, K2→Task 6, N1→Task 10, N2→Task 14, N3→Task 13, N4→Task 8, N5→Task 9, N6→Task 3, N7→Task 7; UX H1→Task 5, H2→Task 2, M1→Task 12, M2→Task 11, M3→Task 12, M4→Task 12, L1→Tasks 5/15, L2→Task 12, L3→Task 15, L4→Task 12. N8/N9 are accepted non-issues.
- **Order matters:** Task 1 before 6 (watchdog hooks into `_searxng_definitely_down`'s new shape); Task 5's backend before its frontend seed; Task 10 before 12 (log tail rendered).
- **Risk notes for implementers flagged inline:** history-append ordering (Task 8), generator scope for the failure event (Task 11), pinned-sha fetch depth (Task 3), live env-allowlist boot check (Task 4).
