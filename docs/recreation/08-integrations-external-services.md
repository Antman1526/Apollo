# 08 — Integration Points & External Services

Every external integration Apollo talks to, documented from the actual source: config
knobs, data formats, and failure behavior for each. All code is quoted verbatim with
file:line citations. `UNCERTAIN:` flags anything not confirmed in the tree.

---

## 1. llama.cpp `llama-server` subprocess

Covered in full algorithmic detail in doc 07 §3 (single-warm-model policy, health check,
port allocation, auto-detect paths). Summarized here as the integration surface:

- **Spawn**: `services/localmodels/server_manager.py:_launch` (`server_manager.py:201-258`)
  runs `[binary, "--model", path, "--host", "127.0.0.1", "--port", <free_port>, "-c",
  <context>]` (plus `--embedding` for embedding GGUFs) via `subprocess.Popen(cmd,
  stdout=logf, stderr=subprocess.STDOUT, text=True)`. No `cwd=`/`env=` override — inherits
  the Apollo backend's cwd and full environment. No GPU flags are ever passed; offload
  behavior is left to llama-server's own binary-level defaults.
- **Port allocation**: OS-assigned ephemeral port via a bind-to-0/read-back/close trick
  (`_free_port`, `server_manager.py:61-67`), avoiding a fixed port that could collide with a
  second instance.
- **Health check**: polling `GET {base_url}/health` every 0.5s via `urllib.request.urlopen`
  until HTTP 200, with a per-model timeout scaled ~40s/GB of GGUF size (floor 180s) —
  `_wait_health`/`_health_timeout_for` (`server_manager.py:260-284`).
- **Binary discovery**: `APOLLO_LLAMA_SERVER` env / Settings-configured path first, then a
  platform candidate list (Homebrew paths, Scoop shims, `%LOCALAPPDATA%`, `~/llama.cpp/build`,
  bare `llama-server` on `PATH`) — `_bin_candidates()` (`server_manager.py:23-44`).
- **Model discovery**: configured dirs → `APOLLO_MODELS_DIRS` env → built-in per-platform
  defaults (`services/localmodels/config.py:8-27`), scanned for `*.gguf` files, skipping
  cache/blob directories and projector (`mmproj`) files (`scanner.py:69-111`).
- **Stop**: `SIGTERM` → wait 10s → `SIGKILL` on timeout (`_stop_proc`,
  `server_manager.py:286-311`). No process-group signaling, no psutil.
- **Failure behavior**: a binary-not-found condition raises `RuntimeError` with a
  platform-specific install hint (`brew install llama.cpp` on macOS/Linux,
  `winget install llama.cpp` on Windows). An early process exit during health-wait raises
  `RuntimeError` with the tail of the per-port log file
  (`os.path.join(tempfile.gettempdir(), f"apollo-llama-{port}.log")`) attached, so the caller
  sees the actual llama-server startup error rather than a bare timeout.
- **Config knobs**: `APOLLO_LLAMA_SERVER` (binary path override), `APOLLO_MODELS_DIRS`
  (colon/comma-separated scan roots), `APOLLO_LLAMA_CONTEXT` (KV-cache cap, default `16384`),
  Settings → AI → Local Models (`llama_server_path`, `local_model_dirs`).
- **HTTP surface**: `routes/localmodels_routes.py`, prefix `/api/local-models`, all routes
  `require_admin`-gated (see doc 07 §3.6 table).

---

## 2. Ollama endpoint support

Ollama is treated as one of the resolvable providers inside `src/endpoint_resolver.py` +
`src/llm_core.py`, not a managed sidecar Apollo spawns — Apollo talks to whatever Ollama
instance the user points it at (local `http://localhost:11434`, remote, or Ollama Cloud).

Native-URL detection, `src/llm_core.py:160-172`:

```python
def _is_ollama_native_url(url: str) -> bool:
    """Return True for native Ollama API URLs, including Ollama Cloud."""
    try:
        parsed = urlparse(url or "")
        port = parsed.port
    except ValueError:
        return False
    host = parsed.hostname or ""
    path = (parsed.path or "").rstrip("/")
    if _host_match(url, "ollama.com"):
        return True
    local_ollama_host = host in {"localhost", "127.0.0.1", "0.0.0.0", "::1"} or port == 11434
    return local_ollama_host and (path == "/api" or path.startswith("/api/"))
```

`src/endpoint_resolver.py:_ollama_api_root` (`160-170`, quoted in full in doc 07 §4):
adds `/api` to bare `ollama.com`-family hosts, leaves already-`/api`-suffixed bases alone,
and leaves any other host untouched. `build_chat_url`/`build_models_url`
(`endpoint_resolver.py:173-192`) append `/chat` or `/tags` to that root — i.e. Apollo speaks
Ollama's **native** `/api/chat` and `/api/tags` endpoints, not the OpenAI-compatible `/v1`
shim Ollama also exposes.

A second copy of the same helper in `src/llm_core.py:175-191` operates on already-built URLs
(trimming `/api/chat`, `/api/tags`, `/api/generate` back to `/api` before re-deriving),
because the LLM dispatch layer may be handed a fully-formed endpoint URL rather than a bare
base.

Message-shape adaptation — Apollo's canonical OpenAI-style tool-call messages
(`function.arguments` as a JSON *string*) are not what native Ollama's `/api/chat` expects
(a JSON *object*); a dedicated adapter fixes this on a shallow copy before dispatch:

```python
def _ollama_normalize_tool_messages(messages: List[Dict]) -> List[Dict]:
    """Adapt Apollo' canonical OpenAI-style messages to native Ollama /api/chat. ...
    Given the string [Ollama] fails the whole request with HTTP 400
    "Value looks like object, but can't find closing '}' symbol" ..."""
```
(`src/llm_core.py:200-...`) — without this adapter every tool-calling round against a native
Ollama endpoint would 400.

- **Config**: any `ModelEndpoint.base_url` pointing at an Ollama host is auto-classified via
  `_detect_provider`; no dedicated Ollama env vars beyond whatever base URL the admin
  configures in Settings → Endpoints.
- **Failure behavior**: a malformed tool-call payload against a native Ollama endpoint
  without the adapter surfaces as HTTP 400 from Ollama itself (`"Value looks like object,
  but can't find closing '}' symbol"`) — the adapter exists specifically to prevent this
  class of failure from reaching the user.
- `UNCERTAIN:` No explicit Ollama pull/model-management integration (`ollama pull`, etc.)
  was found in the reviewed files — Apollo only talks to an already-running Ollama server's
  chat/models API, it does not manage the Ollama process or its model cache.

---

## 3. Cloud model endpoints (OpenAI-compatible)

Every non-local, non-Ollama, non-Anthropic endpoint falls through `_detect_provider`'s
default branch and is treated as OpenAI-compatible:

```python
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
(`src/llm_core.py:318-335`) — hostname-exact/subdomain matching (never substring), so a
provider name embedded in an unrelated path/query never misclassifies.

URL building and auth headers (`src/endpoint_resolver.py:173-209`, quoted in full in doc 07
§4): Anthropic gets `x-api-key` + `anthropic-version: 2023-06-01`; everything else
(OpenAI, Groq, Mistral, DeepSeek, Together, Fireworks, Perplexity, xAI, Venice, local
llama.cpp/vLLM/LM Studio, etc.) gets `Authorization: Bearer <key>` against
`{base}/chat/completions` and `{base}/models`; OpenRouter additionally gets
`HTTP-Referer`/`X-OpenRouter-Title` attribution headers via `setdefault`.

`_API_HOSTS` (`src/agent_loop.py:474-487`, quoted in doc 07 §1.4) is the list of hosts that
get **native OpenAI-style function calling** (real `tool_calls`) rather than fenced-block
tool prompting — it explicitly includes `localhost`/`127.0.0.1`/`host.docker.internal` so
local OpenAI-compatible servers aren't penalized just for not being a named cloud vendor.

**Persistence** — `ModelEndpoint` (`core/database.py:339-360`): `id, name, base_url, api_key
(EncryptedText, encrypted at rest), is_enabled, hidden_models (JSON), cached_models (JSON),
model_type ("llm"|"image"), supports_tools (nullable bool, auto-detected from
`--enable-auto-tool-choice` at Cookbook register time, UI-togglable), owner (nullable — NULL
= shared/legacy visible to all users)`.

**Failure behavior**: `resolve_endpoint`'s DB lookup filters `is_enabled == True`; a disabled
or missing endpoint silently falls back to the caller-supplied `(fallback_url,
fallback_model, fallback_headers)` triple rather than raising — chat dispatch layers
(`resolve_chat_fallback_candidates`, `resolve_utility_fallback_candidates`,
`resolve_vision_fallback_candidates`) build an ordered list of `{endpoint_id, model}` entries
from `default_model_fallbacks`/`utility_model_fallbacks`/`vision_model_fallbacks` settings so
a request retries the next configured endpoint on failure before giving up.

**Config knobs**: Settings → Endpoints (add/edit/delete `ModelEndpoint` rows via
`routes/model_routes.py`); `default_endpoint_id`/`default_model`, `utility_endpoint_id`/
`utility_model`, `research_endpoint_id`/`research_model`, `task_endpoint_id`/`task_model`,
`reviewer_endpoint_id`/`reviewer_model`, `light_endpoint_id`/`light_model` (Fast Lane, doc 07
§5) — all resolved through the same `resolve_endpoint(prefix)` fallback chain (utility →
default) described in doc 07 §4.

---

## 4. SearXNG sidecar (search)

Apollo installs SearXNG **natively** (no Docker) into `data/searxng/`:

```python
"""Native lifecycle for the managed SearXNG sidecar: spawn and supervise
`python -m searx.webapp` from the dedicated venv in data/searxng/.

Mirrors services/paperclip/runtime.py: injectable spawn/health for tests,
graceful no-ops when disabled or not installed, never raises into startup.
"""
```
(`services/searxng/runtime.py:1-6`)

### 4.1 Layout and config

```python
SEARXNG_HOME = os.path.join(DATA_DIR, "searxng")
DEFAULT_PORT = 8893

@dataclass(frozen=True)
class SearxngConfig:
    enabled: bool
    port: int
    home: str = SEARXNG_HOME

    @property
    def venv_python(self) -> str:
        sub, exe = ("Scripts", "python.exe") if os.name == "nt" else ("bin", "python")
        return os.path.join(self.home, "venv", sub, exe)

    @property
    def settings_path(self) -> str:
        return os.path.join(self.home, "settings.yml")

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def installed(self) -> bool:
        return os.path.exists(self.venv_python) and os.path.exists(self.settings_path)

def load_config() -> SearxngConfig:
    from src.settings import load_settings
    s = load_settings()
    port = int(s.get("searxng_port", DEFAULT_PORT))
    return SearxngConfig(enabled=bool(s.get("searxng_managed", True)), port=port)
```
(`services/searxng/config.py`, in full) — `data/searxng/src/` holds a git checkout of
`searxng/searxng`, `data/searxng/venv/` a dedicated virtualenv, `data/searxng/settings.yml`
a minimal localhost-only config with the JSON API enabled.

### 4.2 Spawn

```python
self._proc = self._spawn(
    [cfg.venv_python, "-m", "searx.webapp"],
    env=env,
    cwd=cfg.home,
    stdout=_log_fh,
    stderr=_log_fh,
)
```
(`services/searxng/runtime.py:146-153`) — `env` is a **minimal allowlist**, not the full
parent environment:

```python
_PASS = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "TEMP", "TMP",
         "SYSTEMROOT", "WINDIR", "USERPROFILE")
env = {k: os.environ[k] for k in _PASS if k in os.environ}
env["SEARXNG_SETTINGS_PATH"] = cfg.settings_path
```
(`services/searxng/runtime.py:120-123`) — the comment notes the sidecar needs none of
Apollo's secrets. Stdout/stderr go to `logs/searxng.log`, truncated before each spawn if it
exceeds 5 MB (`_LOG_MAX_BYTES = 5 * 1024 * 1024`).

### 4.3 Health check

```python
def _http_ok(url: str, timeout: float = 2.0) -> bool:
    """Fail closed: a foreign service listening on the same port (e.g. nginx
    returning HTML) reads as not-serving so the search chain skips it fast.
    SearXNG's /healthz returns exactly "OK" (verified live)."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.read(16).startswith(b"OK")
    except Exception:
        return False
```
(`services/searxng/runtime.py:31-43`) — hits `GET {url}/healthz`, requires the body to
literally start with `b"OK"` (not just a 200 status), cached with a 2-second TTL
(`_HEALTH_TTL = 2.0`, consulted on every search call).

### 4.4 Start/stop and self-healing

`start()` (`services/searxng/runtime.py:97-193`) is lock-guarded for the reuse-check and
spawn, but the up-to-30-second boot-wait loop runs **outside** the lock (polling
`is_serving()` every 1s via a `threading.Event.wait(1)` that `stop()` can interrupt
immediately) so shutdown latency stays near zero even mid-boot. `stop()` signals the event,
then terminates/waits(10s)/kills outside the lock so a blocking syscall never holds it.

`maybe_restart()` (`services/searxng/runtime.py:227-249`) is the self-healing hook, called
from the search hot path when the sidecar is installed-but-not-serving: schedules a
background restart thread, throttled to once per `_RESTART_COOLDOWN = 300.0` seconds so a
persistently-crashed sidecar doesn't get hammered with restart attempts.

### 4.5 Failure behavior in the search chain

When SearXNG is not serving (disabled, not installed, or down and the cooldown hasn't
elapsed), the search-provider chain skips straight to the fallback provider with **no
timeout penalty** — per the settings comment: *"When the sidecar isn't running, the provider
chain skips straight to the fallback (DuckDuckGo) with no timeout penalty."*
(`src/settings.py:82-86`).

### 4.6 Config knobs

```python
"search_provider": "searxng",
"search_fallback_chain": ["duckduckgo"],
"searxng_managed": True,
"searxng_port": 8893,
```
(`src/settings.py:77-87`)

---

## 5. Reference Library upstream GitHub catalogs

`services/reference_library.py` — full `SOURCES` dict, verbatim:

```python
SOURCES: Dict[str, Dict[str, Any]] = {
    "public-apis": {
        "name": "Public APIs",
        "repo": "public-apis/public-apis",
        "ref": "master",
        "files": ["README.md"],
        "parser": "api_table",
        "kind": "api",
        "license": "MIT",
        "description": "~1,400 free public APIs with auth/HTTPS/CORS noted — "
                       "the agent can look one up and call it directly.",
        "agent_actionable": True,
    },
    "build-your-own-x": {
        "name": "Build Your Own X",
        "repo": "codecrafters-io/build-your-own-x",
        "ref": "master",
        "files": ["README.md"],
        "parser": "byox",
        "kind": "tutorial",
        "license": "CC0-1.0",
        "description": "~400 step-by-step 'build X from scratch' tutorials, "
                       "grouped by what you're building.",
        "agent_actionable": False,
    },
    "free-programming-books": {
        "name": "Free Programming Books",
        "repo": "EbookFoundation/free-programming-books",
        "ref": "main",
        "files": [
            "books/free-programming-books-langs.md",
            "books/free-programming-books-subjects.md",
            "courses/free-courses-en.md",
        ],
        "parser": "book_list",
        "kind": "book",
        "license": "CC-BY-4.0",
        "description": "Free books and courses by language and subject "
                       "(English set; the repo covers 50+ languages).",
        "agent_actionable": False,
    },
    "developer-roadmap": {
        "name": "Developer Roadmaps",
        "repo": "nilbuild/developer-roadmap",
        "ref": "master",
        "files": ["readme.md"],
        "parser": "roadmap",
        "kind": "roadmap",
        "license": "custom (unrecognized by GitHub — check repo before reuse)",
        "description": "80+ interactive learning roadmaps (frontend, backend, "
                       "DevOps, AI, …) from roadmap.sh.",
        "agent_actionable": False,
    },
}
```
(`services/reference_library.py:35-88`)

`UNCERTAIN:` the `developer-roadmap` repo is pinned to `nilbuild/developer-roadmap` rather
than the canonical `kamranahmedse/developer-roadmap` — this may be a fork used for stability/
licensing reasons, or a placeholder; not confirmed from context in this file alone.

### 5.1 Fetch path

```python
RAW_BASE = "https://raw.githubusercontent.com"
_REPO_RE = re.compile(r"^[\w.-]+/[\w.-]+$")
_PATH_RE = re.compile(r"^(?!.*\.\.)[\w./-]+\.md$", re.IGNORECASE)

def _raw_url(repo: str, ref: str, path: str) -> str:
    if not _REPO_RE.match(repo or ""):
        raise ValueError(f"invalid repo: {repo}")
    if not _PATH_RE.match(path or ""):
        raise ValueError(f"invalid file path: {path}")
    safe_ref = re.sub(r"[^\w.-]", "", ref or "main")
    return f"{RAW_BASE}/{repo}/{safe_ref}/{path}"
```
(`services/reference_library.py:24-26, 91-97`) — `repo`/`path`/`ref` are all whitelisted by
regex before being interpolated into the raw.githubusercontent.com URL (path must end in
`.md` and cannot contain `..`), so even a corrupted `SOURCES` entry can't be turned into a
path-traversal or arbitrary-host request. Every fetch goes through the shared SSRF guard
(§5.3) via `src.search.content._get_public_url`. Per-file size cap `_MAX_FILE_BYTES = 8 *
1024 * 1024` (8 MB); per-source entry cap `MAX_ENTRIES_PER_SOURCE = 6000`.

### 5.2 Community skill-pack catalog and MCP presets

`services/connector_catalog.py` — a **curated, static, verified-only** list; installation
always still goes through the existing guarded pipelines (this module never installs
anything, it only pre-fills the frontend forms):

```python
SKILL_PACKS: List[Dict[str, Any]] = [
    {
        "id": "mattpocock-skills",
        "name": "Matt Pocock's Skills",
        "source": "https://github.com/mattpocock/skills",
        "description": "TDD, code review, bug diagnosis, and a few non-coding "
                       "workflows (handoff notes, turning notes into questions).",
    },
    {
        "id": "ecc-skills",
        "name": "ECC Skill Library",
        "source": "https://github.com/affaan-m/ECC",
        "description": "A large SKILL.md library covering planning, security "
                       "review, and language-specific engineering workflows.",
    },
]

MCP_PRESETS: List[Dict[str, Any]] = [
    {"id": "mcp-filesystem", "name": "Filesystem", "command": "npx",
     "args": ["-y", "@modelcontextprotocol/server-filesystem"], "env": {}, ...},
    {"id": "mcp-fetch", "name": "Fetch", "command": "npx",
     "args": ["-y", "@modelcontextprotocol/server-fetch"], "env": {}, ...},
    {"id": "mcp-brave-search", "name": "Brave Search", "command": "npx",
     "args": ["-y", "@modelcontextprotocol/server-brave-search"],
     "env": {"BRAVE_API_KEY": ""}, ...},
]
```
(`services/connector_catalog.py:19-70`, condensed) — surfaced at `GET /api/hub/catalog`
(`routes/hub_routes.py:204-210`, `require_admin`).

**Any** GitHub repo of `SKILL.md` files works via the general pack installer, not just the
two curated entries — `services/skills/pack_installer.py`:

```python
"""Install Agent Skills packs into Apollo's SKILL.md store.

Safe-by-default: prose-only skills install published; skills that ship
executable code (scripts/hooks/MCP config) are quarantined as drafts and never
run during import."""

_CODE_EXTS = (".py", ".js", ".mjs", ".ts", ".sh", ".rb", ".php", ".pl", ".ps1")
_CODE_DIRS = ("scripts", "hooks", "bin")
_CODE_FILES = (".mcp.json",)

def classify_tier(skill_dir: str) -> str:
    """Return 'script' if the skill folder ships executable code / hooks / MCP
    config, else 'prose'. Never executes anything — inspects file names only."""
```
(`services/skills/pack_installer.py:1-31`) — a **script-tier** skill (any code file, `hooks/`,
`scripts/`, `bin/`, or an `.mcp.json`) is installed but marked `status: "draft"` and never
executed by the import itself; a **prose-tier** skill installs `status: "published"`
directly. Frontmatter is parsed with PyYAML, falling back to Apollo's own regex parser if
PyYAML fails (`_parse_frontmatter_robust`, `pack_installer.py:40-59`).

Download path — GitHub API tarball, SSRF-guarded, size-capped, safely extracted:

```python
_MAX_PACK_BYTES = 50 * 1024 * 1024  # 50 MB
_MAX_PACK_MEMBERS = 5000

def _github_tarball_url(repo_url: str, ref: str = "") -> str:
    p = urlparse(repo_url)
    parts = [x for x in p.path.split("/") if x]
    if p.hostname not in ("github.com", "www.github.com") or len(parts) < 2:
        raise ValueError("expected a https://github.com/<owner>/<repo> URL")
    owner, repo = parts[0], parts[1].removesuffix(".git")
    base = f"https://api.github.com/repos/{owner}/{repo}/tarball"
    return f"{base}/{ref}" if ref else base

def safe_extract_tar(tar, dest: str, max_bytes: int) -> str:
    members = tar.getmembers()
    if len(members) > _MAX_PACK_MEMBERS:
        raise ValueError("archive has too many entries")
    total = 0
    for m in members:
        if m.name.startswith("/") or ".." in m.name.split("/"):
            raise ValueError(f"unsafe path in archive: {m.name}")
        total += max(0, m.size)
        if total > max_bytes:
            raise ValueError("archive too large")
    try:
        # filter="data" (Python 3.12+): blocks symlink/hardlink escapes,
        # absolute paths, device nodes — closes the gap the name-only check misses.
        tar.extractall(dest, filter="data")
    except tarfile.FilterError as e:
        raise ValueError(f"unsafe archive member: {e}") from e
    return dest

def fetch_pack(source: str, ref: str = "", *, timeout: int = 30) -> str:
    from src.search.content import _get_public_url
    url = _github_tarball_url(source, ref)
    resp = _get_public_url(url, headers={"Accept": "application/vnd.github+json",
                                         "User-Agent": "Apollo-SkillInstaller"}, timeout=timeout)
    resp.raise_for_status()
    if len(resp.content) > _MAX_PACK_BYTES:
        raise ValueError("pack download too large")
    dest = tempfile.mkdtemp(prefix="apollo-skillpack-")
    with tarfile.open(fileobj=io.BytesIO(resp.content), mode="r:gz") as t:
        safe_extract_tar(t, dest, _MAX_PACK_BYTES)
    return dest
```
(`services/skills/pack_installer.py:190-245`, condensed) — three defenses stacked: member-
count cap (inode exhaustion), path-traversal name check, and Python 3.12's
`tarfile.extractall(filter="data")` vetted-extraction policy (blocks symlink/hardlink escapes
the name check alone would miss).

HTTP surface: `POST /api/skills/packs/preview` (fetch + discover, writes nothing) and
`POST /api/skills/packs/install` (fetch + discover + write with provenance), both
`require_admin`-gated (`routes/skill_pack_routes.py`). Provenance fields written into every
installed `SKILL.md`'s frontmatter: `imported_from`, `imported_ref`, `imported_at`,
`imported_tier`.

**MCP server registration** — `src/mcp_manager.py`'s `McpManager` class supports `stdio` and
`sse` transports:

```python
async def connect_server(self, server_id, name, transport, command=None, args=None,
                          env=None, url=None) -> bool:
    if transport == "stdio":
        res = await self._connect_stdio(server_id, name, command, args or [], env or {})
    elif transport == "sse":
        res = await self._connect_sse(server_id, name, url)
    else:
        logger.error(f"Unknown MCP transport: {transport}")
        res = False
```
(`src/mcp_manager.py:79-100`, condensed). Stdio child processes get a **minimal allowlisted
env**, not the full parent environment (`_stdio_env`, `mcp_manager.py:35-51`, via
`src.subproc_env.build_agent_env`) — no host secrets leak into an MCP child by default; `npx`/
`npm` invocations additionally get `NPM_CONFIG_LOGLEVEL=silent` etc. injected to keep noisy
npm chatter out of the JSON-RPC stream. A connection-error formatter special-cases the
Playwright MCP package to surface an actionable cache-priming hint
(`_format_mcp_connection_error`, `mcp_manager.py:17-32`).

### 5.3 The shared SSRF guard — `src/search/content.py`

Used by the reference library, the skill-pack installer, the persona importer, and general
`web_fetch`. Blocks the standard private/reserved ranges plus loopback/link-local/
metadata-endpoint hostnames, with DNS re-resolution on every redirect hop:

```python
_PRIVATE_NETWORKS = (
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
)

def _is_private_address(addr: ipaddress._BaseAddress) -> bool:
    return any(addr in net for net in _PRIVATE_NETWORKS) or addr.is_private or addr.is_loopback

def _resolve_hostname_ips(hostname: str) -> List[ipaddress._BaseAddress]:
    ips = []
    for family, _, _, _, sockaddr in socket.getaddrinfo(hostname, None):
        if family in (socket.AF_INET, socket.AF_INET6):
            ips.append(ipaddress.ip_address(sockaddr[0]))
    return ips

def _public_http_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False
    host = parsed.hostname.strip().lower()
    if host in ("localhost", "metadata.google.internal", "metadata"):
        return False
    try:
        return not _is_private_address(ipaddress.ip_address(host))
    except ValueError:
        pass
    try:
        ips = _resolve_hostname_ips(host)
    except OSError:
        return False
    # Fail closed: a hostname that resolves to nothing is treated as
    # non-public (an empty all(...) would otherwise return True).
    return bool(ips) and all(not _is_private_address(ip) for ip in ips)

def _get_public_url(url: str, *, headers: dict, timeout: int) -> httpx.Response:
    if not _public_http_url(url):
        raise httpx.RequestError(f"Blocked non-public URL: {url}")
    current = url
    with httpx.Client(headers=headers, timeout=timeout, follow_redirects=False) as client:
        for _ in range(8):
            response = client.get(current)
            if response.status_code not in (301, 302, 303, 307, 308):
                return response
            location = response.headers.get("location")
            if not location:
                return response
            current = urljoin(current, location)
            if not _public_http_url(current):
                raise httpx.RequestError(f"Blocked redirect to non-public URL: {current}")
    raise httpx.RequestError("Too many redirects")
```
(`src/search/content.py:28-89`, in full) — blocks all RFC 1918/loopback/link-local/ULA
ranges, `169.254.0.0/16` (which covers the cloud-metadata IP `169.254.169.254`), plus the
literal hostnames `localhost`/`metadata.google.internal`/`metadata`. Critically,
`follow_redirects=False` is set explicitly and redirects are followed **manually** in an
8-hop loop, re-validating `_public_http_url` on *every* hop target — this closes the classic
SSRF bypass where a public URL 301-redirects to `http://169.254.169.254/...` after the
initial check passes. The "fail closed" comment on `_public_http_url`'s last line is
deliberate: a hostname that fails to resolve at all is treated as non-public rather than
vacuously public (an empty `all()` over zero IPs would otherwise return `True`).

A parallel, purpose-specific SSRF guard exists for CalDAV (`src/caldav_sync.py:51-88`,
`_validate_caldav_ip`/`validate_caldav_url`) — blocks loopback/link-local/multicast/
unspecified IPs and a `_BLOCKED_HOSTS` set (`localhost`, `ip6-localhost`,
`metadata.google.internal`), with private IPs specifically opt-in-able via
`APOLLO_ALLOW_PRIVATE_CALDAV=1` (since a self-hosted CalDAV server on a LAN is a legitimate
use case web-fetch never has).

### 5.4 Caching and failure behavior

Reference Library catalogs are **not TTL-cached on disk** — `install_source(source_id)`
(`services/reference_library.py:280-308`) fetches fresh, parses, and **wholesale replaces**
all `ReferenceEntry` rows for that source in the DB (`db.query(ReferenceEntry).filter(source
== source_id).delete()` then re-insert) — re-installing is the refresh mechanism, triggered
manually from Settings → AI → Reference Library (`POST /api/hub/reference/install`). If
`parse_source` returns zero entries, `install_source` raises `ValueError(f"no entries parsed
from {source_id} — upstream format may have changed")` rather than silently wiping the
existing store — the route handler (`routes/hub_routes.py:249-258`) turns that into HTTP 400/
502 depending on the failure kind. If the upstream fetch itself fails (network error, SSRF
block, oversize), `_get_public_url` raises `httpx.RequestError`, caught by the route as a
generic 502 "Could not fetch or parse that catalog."

`ReferenceEntry` schema (`core/database.py:466-484`): `id (sha1-derived), source, kind
(api|tutorial|book|roadmap), category, title, url, description, meta (JSON — auth/https/cors
for APIs, language for tutorials)`.

---

## 6. Agency-agents persona importer

`services/persona_importer.py` — imports character/persona preset markdown files from **any**
GitHub repo shaped like `msitarzewski/agency-agents` (one markdown file per persona, YAML
frontmatter with `name`/`description`, body is a full "You are X…" system prompt):

```python
"""Import persona/character-preset markdown files from a GitHub repo.

Same shape as agency-agents (msitarzewski/agency-agents): one markdown file
per persona, YAML frontmatter (name/description/...) + a full "You are X…"
system-prompt body — but the source repo is not hardcoded, any repo of
`{name, description}`-fronted markdown files works.

Deliberately reuses the skill-pack installer's already-guarded download path
(`fetch_pack`: SSRF-checked, size-capped, tar-safe) instead of a second
implementation, and its frontmatter parser (PyYAML with a regex fallback).
"""

_SKIP_NAMES = {"readme", "contributing", "license", "changelog", "code_of_conduct"}
MAX_PERSONAS = 400  # generous — agency-agents ships ~230; a hard backstop
```
(`services/persona_importer.py:1-26`)

```python
def discover_personas(pack_root: str) -> List[FoundPersona]:
    """Walk an extracted repo for persona-shaped markdown files."""
    found: List[FoundPersona] = []
    for root, dirs, files in os.walk(pack_root):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d != "node_modules"]
        for fn in sorted(files):
            if not fn.lower().endswith(".md"):
                continue
            stem = fn[:-3].lower()
            if stem in _SKIP_NAMES:
                continue
            ...
            fm, body = _parse_frontmatter_robust(text)
            name = (fm.get("name") or "").strip() if isinstance(fm, dict) else ""
            if not name:
                continue  # not a persona file — no frontmatter name
            description = (fm.get("description") or "").strip() if isinstance(fm, dict) else ""
            found.append(FoundPersona(rel_path=..., name=name, description=description,
                                       system_prompt=body.strip()))
            if len(found) >= MAX_PERSONAS:
                return found
    return found
```
(`services/persona_importer.py:37-67`, condensed) — a `.md` file with no `name:` frontmatter
field is silently skipped (it's repo documentation, not a persona).

```python
def install_personas(source, names, preset_manager, ref="") -> Dict[str, int]:
    """Fetch, filter to the requested persona names, and save each as a user
    template. Dedupes by slugified name against existing templates."""
    pack_root = fetch_pack(source, ref)
    try:
        wanted = set(names)
        personas = [p for p in discover_personas(pack_root) if p.name in wanted]
        existing = {t.get("id") for t in preset_manager.get_user_templates()}
        added, skipped = 0, 0
        for p in personas:
            tid = "persona-" + slugify(p.name)
            if tid in existing:
                skipped += 1
                continue
            preset_manager.save_user_template({
                "id": tid, "name": p.name,
                "system_prompt": p.system_prompt[:10000],  # matches UserTemplateRequest cap
                "temperature": 1.0, "max_tokens": 0,
            })
            existing.add(tid)
            added += 1
        return {"added": added, "skipped": skipped}
    finally:
        shutil.rmtree(pack_root, ignore_errors=True)
```
(`services/persona_importer.py:82-115`) — each imported persona becomes a user chat-preset
template (`id = "persona-" + slugify(name)`), system prompt truncated to 10,000 chars to
match the existing `UserTemplateRequest` field cap; the extracted tarball is always cleaned
up (`finally: shutil.rmtree`) whether install succeeds or fails.

HTTP surface: `POST /api/hub/personas/preview` (discover only, no writes) and
`POST /api/hub/personas/install` (`routes/hub_routes.py:212-237`), both `require_admin`.
Failure mapping: a bad source URL → HTTP 400 (`ValueError`); any other fetch/parse failure →
HTTP 502 "Could not fetch or read that repository."

---

## 7. Paperclip native sidecar

Paperclip is Apollo's companion multi-agent orchestration UI, run as a reverse-proxied
sidecar. Controlled by env vars, not a Settings-panel toggle (it's an operator/deployment
concern):

```python
"""Resolve Paperclip integration settings from environment + a secret file.
Pure-ish: only touches env and an on-disk secret file. No network, no DB.
Mirrors the env-driven style of services/localmodels/config.py."""

_OLLAMA_DOCKER = "http://host.docker.internal:11434/v1"
_OLLAMA_LOCAL = "http://localhost:11434/v1"

@dataclass(frozen=True)
class PaperclipConfig:
    enabled: bool
    mode: str            # docker | native | external | off
    url: str             # server-side base Apollo can reach, no trailing slash
    browser_url: str     # origin the browser iframes directly, no trailing slash
    port: int
    model_endpoint: str  # ollama | apollo | custom
    model_base_url: str
    model_name: str

def load_config() -> PaperclipConfig:
    enabled = _bool("PAPERCLIP_ENABLED", False)
    mode = os.getenv("PAPERCLIP_MODE", "docker").strip().lower()
    port = int(os.getenv("PAPERCLIP_PORT", "3100"))
    default_url = f"http://paperclip:{port}" if mode == "docker" else f"http://localhost:{port}"
    url = os.getenv("PAPERCLIP_URL", default_url).rstrip("/")
    browser_url = os.getenv("PAPERCLIP_BROWSER_URL", f"http://localhost:{port}").rstrip("/")
    endpoint = os.getenv("PAPERCLIP_MODEL_ENDPOINT", "ollama").strip().lower()
    base_url, model_name = _resolve_model(endpoint, mode)
    return PaperclipConfig(enabled=enabled, mode=mode, url=url, browser_url=browser_url,
                            port=port, model_endpoint=endpoint,
                            model_base_url=base_url, model_name=model_name)
```
(`services/paperclip/config.py:1-80`, condensed) — the docstring on the module explains why
`url` (server-side, used for Apollo→Paperclip proxying) and `browser_url` (what the user's
browser iframes) can differ: in Docker mode Apollo reaches Paperclip via the Compose service
name `paperclip`, but the browser can only ever reach `localhost:<port>` — Paperclip's own UI
and `/api` are hard-wired to root paths, so it cannot be embedded under an Apollo subpath.

`PAPERCLIP_MODEL_ENDPOINT` selects which model backend Paperclip's own agents call:
`ollama` (default — `host.docker.internal:11434/v1` in Docker mode, `localhost:11434/v1`
natively), `apollo` (Apollo's own `/v1` proxy, Phase-3 feature), or `custom`
(`PAPERCLIP_MODEL_BASE_URL`/`PAPERCLIP_MODEL_NAME` explicit).

Two secrets are auto-generated and persisted on first use if not set via env:

```python
def resolve_auth_secret() -> str:
    """Return a stable BETTER_AUTH_SECRET, generating + persisting one if unset."""
    return _read_or_make_secret("PAPERCLIP_AUTH_SECRET", "PAPERCLIP_SECRET_FILE",
                                 "~/.apollo/paperclip_secret")

def resolve_proxy_token() -> str:
    """Bearer token guarding Apollo's local-model proxy. Passed to Paperclip's
    opencode agents as OPENAI_API_KEY; validated by routes/lmproxy_routes."""
    return _read_or_make_secret("PAPERCLIP_PROXY_TOKEN", "PAPERCLIP_PROXY_TOKEN_FILE",
                                 "~/.apollo/paperclip_proxy_token")
```
(`services/paperclip/config.py:106-117`) — both write a `secrets.token_hex(32)` value to a
`0o600`-permissioned file under `~/.apollo/` if no env var and no existing file value is
found.

### 7.1 HTTP surface (`routes/paperclip_routes.py`)

Mounted at `/paperclip/*` (HTTP, reverse-proxied) and `/paperclip` (websocket, proxied
separately since websockets bypass Starlette's `BaseHTTPMiddleware`):

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/paperclip/status` | reachability probe (`GET {cfg.url}/api/health`, 2s timeout) + browser-use verifier + agent-workbench status |
| POST | `/api/paperclip/events` | ingest agent activity events for the "Paperclip Floor" UI |
| GET | `/api/paperclip/stream` | SSE stream of Floor events, with replay buffer |
| POST/GET | `/api/paperclip/agent-tokens` | mint/list per-agent lmproxy tokens |
| ANY | `/paperclip/{path:path}` | reverse proxy to `cfg.url` |
| WS | `/paperclip/{path:path}` | websocket proxy |

Event ingestion is exempted from normal session-cookie auth (same pattern as task webhooks)
and proves identity itself:

```python
if events_token:
    provided = request.headers.get("x-paperclip-events-token", "")
    if not hmac.compare_digest(provided, events_token):
        return JSONResponse({"detail": "invalid events token"}, status_code=401)
else:
    # Loopback-only trust is void behind a reverse proxy (client.host
    # becomes the proxy), so refuse proxied requests in tokenless mode.
    client_host = request.client.host if request.client else ""
    if client_host not in ("127.0.0.1", "::1") or request.headers.get("x-forwarded-for"):
        return JSONResponse({"detail": "remote ingest requires PAPERCLIP_EVENTS_TOKEN"}, status_code=401)
```
(`routes/paperclip_routes.py:103-115`) — with `PAPERCLIP_EVENTS_TOKEN` set, a constant-time
`hmac.compare_digest` header check gates ingestion; without it, only literal loopback clients
with no `X-Forwarded-For` header are trusted (so a request that merely *looks* local because
it came through a reverse proxy is correctly rejected).

Batch size is capped: `_MAX_INGEST_BATCH = 100` events per POST, HTTP 413 above that.

### 7.2 Reverse proxy failure behavior

```python
try:
    upstream = await client.send(
        client.build_request(request.method, url, headers=headers, content=body),
        stream=True,
    )
except httpx.ConnectError:
    return JSONResponse({"detail": "Paperclip is not reachable"}, status_code=502)
except httpx.RequestError as exc:
    return JSONResponse({"detail": f"Paperclip request failed: {exc.__class__.__name__}"}, status_code=502)
```
(`routes/paperclip_routes.py:247-258`) — a disabled or unreachable sidecar returns HTTP 503/
502 rather than hanging or 500ing; `/api/paperclip/stream` falls back to emitting a
`paperclip.stream.unavailable` SSE event with `{"reason": "disabled"}` if the sidecar is off
and nothing has ever been ingested, or `paperclip.stream.waiting` if enabled but idle, so the
frontend's Floor view degrades to a preview state instead of hanging on an empty stream.

### 7.3 Config knobs

`PAPERCLIP_ENABLED` (bool, default `False`), `PAPERCLIP_MODE`
(`docker`|`native`|`external`|`off`, default `docker`), `PAPERCLIP_PORT` (default `3100`),
`PAPERCLIP_URL` / `PAPERCLIP_BROWSER_URL` (overrides), `PAPERCLIP_MODEL_ENDPOINT`
(`ollama`|`apollo`|`custom`), `PAPERCLIP_MODEL_BASE_URL` / `PAPERCLIP_MODEL_NAME` (for
`custom`), `PAPERCLIP_AUTH_SECRET` / `PAPERCLIP_SECRET_FILE`, `PAPERCLIP_PROXY_TOKEN` /
`PAPERCLIP_PROXY_TOKEN_FILE`, `PAPERCLIP_EVENTS_TOKEN` (event-ingest auth).

---

## 8. Web fetch / search tools

The agent's `web_search` and `web_fetch` tools (dispatched in `src/tool_execution.py`,
documented in doc 07 §1.6) sit on top of the same search-provider chain
(`search_provider`/`search_fallback_chain` settings → SearXNG-first, DuckDuckGo-fallback) and
the same SSRF-guarded fetch path (`src.search.content._get_public_url`, §5.3 above) used by
the Reference Library and skill-pack installer. Output is capped to `MAX_OUTPUT_CHARS =
10_000` chars (`src/tool_execution.py:24`). Internal per-call timeouts: `web_search` fetch —
30s; `web_fetch` — inner `timeout=10` wrapped in an outer `timeout=30`.

---

## 9. TTS / podcast integrations

No dedicated "podcast pipeline" exists in the reviewed tree — `UNCERTAIN:` a repo-wide
case-insensitive grep for `"podcast"` across all `.py` files returned zero matches, so this
is TTS-only, not a podcast-generation feature.

`services/tts/tts_service.py` — a multi-provider dispatcher, config read fresh from
`data/settings.json` on every call (not cached):

```python
"""Multi-provider TTS service — dispatches to local Kokoro, OpenAI-compatible API, or browser."""

PROVIDER_DISABLED = "disabled"
PROVIDER_BROWSER = "browser"
PROVIDER_LOCAL = "local"       # Kokoro-82M, requires CUDA
PROVIDER_PIPER = "piper"       # CPU-only, works on Apple Silicon
PROVIDER_VOICEBOX = "voicebox" # local Voicebox desktop app
ENDPOINT_PREFIX = "endpoint:"  # OpenAI-compatible /audio/speech via ModelEndpoint
```
(`services/tts/tts_service.py:1-27`, condensed)

Dispatch (`synthesize`, `tts_service.py:225-276`) routes on the configured `tts_provider`:

- **`local`** — Kokoro-82M on GPU. `_KokoroPipeline._init` (`tts_service.py:333-353`)
  requires `torch.cuda.is_available()`; if unavailable, logs a warning and the provider
  reports unavailable rather than erroring the request.
- **`piper`** — ONNX voices loaded on demand and cached by path
  (`_PiperPipeline`, `tts_service.py:383-434`), CPU-only, no CUDA/torch dependency — the
  comment notes this is "the local-TTS path on Apple Silicon." Requires a matching
  `.onnx.json` beside the `.onnx` voice file.
- **`voicebox`** — a local "voice studio" desktop app reached over HTTP
  (`voicebox_url`, default `http://127.0.0.1:17493`), auth via a custom header
  `X-Voicebox-Client-Id: apollo`:
  ```python
  def _synthesize_voicebox(self, text, voice, url=None):
      base = self._voicebox_base(url)
      profile_id = voice or self._voicebox_profile_id(self._voicebox_profiles(url)[0] if ... else None)
      payload = {"text": text, "profile_id": profile_id, "language": "en"}
      r = httpx.post(base + "/generate", json=payload, headers=self._VOICEBOX_HEADERS, timeout=120)
      r.raise_for_status()
      return r.content
  ```
  (`tts_service.py:200-221`, condensed) — falls back to the first available voice profile if
  none is configured; `_voicebox_reachable` (`165-172`) probes `GET {base}/profiles` with a
  2s timeout for availability checks.
- **`endpoint:<id>`** — any OpenAI-compatible `/audio/speech` endpoint, resolved by
  `ModelEndpoint` id:
  ```python
  def _synthesize_api(self, text, endpoint_id, model, voice, speed=1.0):
      ep = db.query(ModelEndpoint).filter(ModelEndpoint.id == endpoint_id).first()
      base_url = ep.base_url.rstrip("/")
      url = base_url + "/audio/speech"
      headers = {"Content-Type": "application/json"}
      if ep.api_key:
          headers["Authorization"] = f"Bearer {ep.api_key}"
      payload = {"model": model, "input": text, "voice": voice,
                 "response_format": "mp3", "speed": speed}
      r = httpx.post(url, json=payload, headers=headers, timeout=60)
      r.raise_for_status()
      return r.content
  ```
  (`tts_service.py:121-155`, condensed)
- **`browser`** — no server-side synthesis at all; the client uses the Web Speech API.
- **`disabled`** — the default; `synthesize()` returns `None` immediately.

Output caching: SHA-256 of `provider|model|voice|speed|text` as the cache key, stored as
`.mp3` or `.wav` under `data/tts_cache/` (format sniffed from the first bytes — ID3 tag or
MPEG frame sync → `.mp3`, else `.wav`). Input text is truncated to 5000 chars before
synthesis (`if len(text) > 5000: text = text[:5000]`).

**Failure behavior**: every provider path catches its own exceptions and returns `None`
rather than raising — `get_stats()` (`tts_service.py:288-321`) reports `available`/`ready`
booleans the frontend uses to gray out the mic/speak button rather than surfacing a raw
error.

**Config knobs**: `tts_enabled` (bool), `tts_provider`
(`disabled|browser|local|piper|voicebox|endpoint:<id>`), `tts_model` (default `"tts-1"`,
used for the `endpoint:` provider), `tts_voice`, `tts_speed`, `voicebox_url` (default
`http://127.0.0.1:17493`) — all in `src/settings.py:65-76`.

HTTP surface: `routes/tts_routes.py` — `GET /stats`, `POST /synthesize`, `POST
/clear-cache`. A parallel `routes/stt_routes.py` (`GET /stats`, `POST /transcribe`) exists for
speech-to-text via `services/stt/stt_service.py`; `UNCERTAIN:` STT provider details were not
investigated in depth as the task scope named TTS specifically.

---

## 10. Email integrations (`routes/email_routes.py`, `email_helpers.py`, `email_pollers.py`)

Standard **IMAP** (receive) + **SMTP** (send) over per-account configuration, no OAuth
integration found in the reviewed files (`UNCERTAIN:` — a dedicated OAuth email flow may
exist elsewhere but was not located; the connection helpers below only support host/user/
password auth).

### 10.1 SMTP send

```python
def _smtp_security_mode(cfg: dict) -> str:
    raw = str(cfg.get("smtp_security") or "").strip().lower()
    if raw in {"ssl", "starttls", "none"}:
        return raw
    port = int(cfg.get("smtp_port") or 465)
    return "starttls" if port == 587 else "ssl"

def _send_smtp_message(cfg, from_addr, recipients, message, timeout=30) -> None:
    host, port = cfg["smtp_host"], int(cfg.get("smtp_port") or 465)
    user, password = cfg.get("smtp_user") or "", cfg.get("smtp_password") or ""
    security = _smtp_security_mode(cfg)
    if security == "ssl":
        with smtplib.SMTP_SSL(host, port, timeout=timeout) as smtp:
            if user and password:
                smtp.login(user, password)
            smtp.sendmail(from_addr, recipients, message)
        return
    with smtplib.SMTP(host, port, timeout=timeout) as smtp:
        if security == "starttls":
            smtp.starttls()
        if user and password:
            smtp.login(user, password)
        smtp.sendmail(from_addr, recipients, message)
```
(`routes/email_helpers.py:43-73`) — security mode auto-infers from port when not explicitly
configured (587 → STARTTLS, else implicit SSL), a sensible default for the two most common
provider conventions.

### 10.2 IMAP receive

```python
def _open_imap_connection(host, port, *, starttls: bool, timeout=_IMAP_TIMEOUT_SECONDS):
    """Open an IMAP connection using the configured security mode."""
    port = int(port or 993)
    if starttls:
        conn = imaplib.IMAP4(host, port, timeout=timeout)
        conn.starttls()
    elif port == 993:
        conn = imaplib.IMAP4_SSL(host, port, timeout=timeout)
    else:
        conn = imaplib.IMAP4(host, port, timeout=timeout)
    conn.sock.settimeout(timeout)
    return conn

def _imap_connect(account_id=None, owner=""):
    # SECURITY: passing `owner` scopes the fallback config lookup so a brand
    # new user doesn't get connected against another user's default mailbox.
    cfg = _get_email_config(account_id, owner=owner)
    conn = _open_imap_connection(cfg["imap_host"], cfg["imap_port"],
                                  starttls=bool(cfg.get("imap_starttls")), timeout=_IMAP_TIMEOUT_SECONDS)
    conn.login(cfg["imap_user"], cfg["imap_password"])
    return conn
```
(`routes/email_helpers.py:626-661`, condensed) — the third branch (plain IMAP4 on a non-993
port with STARTTLS off) exists specifically for local Dovecot / custom-port setups; the
inline comment notes an earlier version incorrectly forced IMAP4_SSL for any non-STARTTLS
port, which broke TLS handshakes against plain local servers.

A connection **pool** wraps this (`_imap()` contextmanager, `email_helpers.py:672-...`) to
avoid paying the TCP+TLS+LOGIN handshake (~30-100ms with Dovecot) on every request, with a
fresh-connect fallback before the pool hooks are wired up (e.g. background pollers starting
before `setup_email_routes()` has run).

### 10.3 Polling

`routes/email_pollers.py` — two independent background loops:
`_auto_summarize_poller` (30-minute cadence, drives inbox auto-summarization) and
`_scheduled_email_poller` (polls the `scheduled_emails` SQLite table for due sends). Both are
started once at app boot via `_start_poller`, with a deferred-start trick for when the event
loop isn't running yet at import time. Outgoing scheduled/reminder mail is tagged with
custom headers for traceability: `X-Apollo-Origin: apollo-ui`, `X-Apollo-Kind` (sanitized to
`[A-Za-z0-9_.-]`, 64 chars), `X-Apollo-Ref` (the source scheduled-email id).

### 10.4 Config knobs

Precedence for account config resolution (per the source comment at
`email_helpers.py:493`): per-account DB row → env vars (`SMTP_HOST`/`IMAP_HOST`/etc.) as the
final fallback. `APOLLO_IMAP_TIMEOUT_SECONDS` env var (clamped 5-300s,
`_coerce_imap_timeout_seconds`) controls the IMAP socket timeout.

### 10.5 Failure behavior

Both `smtplib`/`imaplib` calls are used with their standard exception surface (no custom
retry wrapper found at the connection layer) — callers (route handlers) catch and translate
to HTTP error responses; the pollers individually try/except each tick so one failed
account/message never stops the loop:
```python
except Exception as e:
    logger.error(f"Scheduled poller error: {e}")
```
pattern repeated across `email_pollers.py`.

---

## 11. Calendar integration (CalDAV)

`src/caldav_sync.py` / `src/caldav_writeback.py` — CalDAV via the `caldav` Python library
(PROPFIND discovery + REPORT XML), pulling remote events into a local SQLite mirror
(`source="caldav"`, `id` = a stable SHA-256-derived hash of the remote calendar URL so
repeated syncs map to the same local row).

### 11.1 SSRF guard for user-supplied CalDAV URLs

```python
_BLOCKED_HOSTS = {"localhost", "localhost.", "ip6-localhost", "metadata.google.internal"}

def _private_caldav_allowed() -> bool:
    return os.environ.get("APOLLO_ALLOW_PRIVATE_CALDAV", "0").lower() in {"1", "true", "yes"}

def _validate_caldav_ip(host: str) -> None:
    try:
        ip = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return
    if ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified:
        raise ValueError("CalDAV URL host is not allowed")
    if ip.is_private and not _private_caldav_allowed():
        raise ValueError("Private CalDAV IPs require APOLLO_ALLOW_PRIVATE_CALDAV=1")

def validate_caldav_url(raw_url: str) -> str:
    """Validate and normalize a user-provided CalDAV URL before server-side use."""
    url = (raw_url if isinstance(raw_url, str) else "").strip()
    if not url:
        raise ValueError("CalDAV URL is required")
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("CalDAV URL must start with http:// or https://")
    if not parsed.hostname:
        raise ValueError("CalDAV URL must include a host")
    if parsed.username or parsed.password:
        raise ValueError("Put CalDAV credentials in the username/password fields, not the URL")
    if parsed.fragment:
        raise ValueError("CalDAV URL fragments are not allowed")
    host = (parsed.hostname or "").lower()
    if host in _BLOCKED_HOSTS or host.endswith(".localhost"):
        raise ValueError("CalDAV URL host is not allowed")
    _validate_caldav_ip(host)
    return urlunparse(parsed._replace(fragment="")).rstrip("/")
```
(`src/caldav_sync.py:43-88`, condensed) — unlike the general web-fetch SSRF guard (§5.3),
this one deliberately allows **private** IPs by default-blocked-but-opt-in
(`APOLLO_ALLOW_PRIVATE_CALDAV=1`), because a self-hosted CalDAV server on the user's own LAN
is a legitimate, common setup that a strict public-only guard would break.

### 11.2 Sync

```python
def _sync_blocking(owner, url, username, password) -> dict:
    """The actual sync — synchronous, intended to run in a threadpool."""
    import caldav
    from caldav.lib.error import AuthorizationError, NotFoundError
    client = caldav.DAVClient(url=url, username=username, password=password)
    try:
        principal = client.principal()
        calendars = principal.calendars()
    except (AuthorizationError, NotFoundError) as e:
        result["errors"].append(f"Discovery failed: {e}")
        return result
    ...
```
(`src/caldav_sync.py:110-138`, condensed) — tries principal→calendars discovery first;
falls back to treating the URL as a single calendar directly if the server doesn't support
discovery. The `caldav` import is deliberately lazy (inside the function) so a missing
dependency doesn't break app startup — the integrations form still works, sync just no-ops
with a reported error. Datetime handling normalizes both tz-aware and naive CalDAV datetimes
to UTC-naive with an explicit `is_utc` flag for the DB column (`_to_utc_naive`,
`caldav_sync.py:98-107`); all-day (date-only) events are widened to a full datetime.

Local writeback (create/update/delete against the remote CalDAV server) lives in
`src/caldav_writeback.py`, invoked from `routes/calendar_routes.py` whenever an event's
owning calendar has `source == "caldav"`.

### 11.3 Config and failure behavior

Config stored per-user under the `caldav` key in prefs (`url`, `username`, `password` —
password stored encrypted, decrypted via `decrypt()` when read for sync). Sync is manually
triggered via `POST /api/calendar/sync` (`routes/calendar_routes.py:673-679`,
`sync_caldav_endpoint` → `src.caldav_sync.sync_caldav(owner)`); `UNCERTAIN:` no background
poller for CalDAV was found (unlike email's dedicated poller loops) — sync appears to be
on-demand/UI-triggered only, not scheduled.

Failure isolation per calendar: `_sync_blocking` wraps calendar discovery and per-calendar
event sync in separate try/except blocks (`report_exception(...,
"caldav_calendar_discovery_failed"/"caldav_calendar_sync_failed", outcome="degraded")`), so
one broken calendar in a multi-calendar account doesn't abort the whole sync — errors
accumulate in `result["errors"]` and are returned to the caller alongside whatever did
succeed (`{"calendars": N, "events": N, "deleted": N, "errors": [...]}`).

---

## Summary table

| Integration | Transport | Managed by Apollo? | Guard / Auth |
|---|---|---|---|
| llama.cpp `llama-server` | subprocess + HTTP | Yes (spawn/health/stop) | loopback-only bind |
| Ollama | HTTP (native `/api`) | No (user-hosted) | Bearer or none |
| Cloud LLM endpoints | HTTP (OpenAI/Anthropic-compat) | No | Bearer / `x-api-key`, encrypted at rest |
| SearXNG | subprocess + HTTP | Yes (native install/spawn/health/restart) | localhost-only, minimal env |
| Reference Library sources | HTTPS (raw.githubusercontent.com) | Fetch-on-demand, DB-cached | SSRF guard + repo/path regex |
| Skill packs / MCP presets | HTTPS (GitHub tarball API) | Fetch-on-demand | SSRF guard + tar-safe extract |
| Persona importer | HTTPS (GitHub tarball API, via skill-pack path) | Fetch-on-demand | same as above |
| Paperclip | HTTP/WS reverse proxy | Optional sidecar (docker/native/external) | HMAC events token / session cookie |
| TTS | HTTP (Kokoro local / Piper local / Voicebox local / OpenAI-compat) | Local providers yes, API providers no | Bearer (API), custom header (Voicebox) |
| Email (IMAP/SMTP) | TCP+TLS | No (user-hosted) | password auth, per-account config |
| Calendar (CalDAV) | HTTPS | No (user-hosted) | password auth + SSRF guard (private-IP opt-in) |
| MCP servers | stdio / SSE | User-configured, connection managed by Apollo | minimal-env child process |
