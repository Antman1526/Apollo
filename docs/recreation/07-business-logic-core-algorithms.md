# 07 — Business Logic & Core Algorithms

This document describes, from the actual source, the algorithms that make Apollo's agent
behave the way it does: the agent loop's full message lifecycle, the RAG/keyword tool
selection mechanism, the multi-dialect tool-call parser (including the critical
Qwen/Llama-3 `<function=NAME>` normalizer), the memory writer/recall/consolidation/
auto-distill system, local model serving via llama.cpp, endpoint resolution, the
"mixture routing" (Fast Lane) chat router, and the Reference Library's fetch/parse/search
pipeline. All code is quoted verbatim from the working tree with file:line citations.
Uncertainties are flagged inline as `UNCERTAIN:`.

---

## 1. The agent loop (`src/agent_loop.py`, 2331 lines)

### 1.1 Entry point

The whole agent turn is one async generator:

```python
async def stream_agent_loop(
    endpoint_url: str,
    model: str,
    messages: List[Dict],
    headers: Optional[Dict] = None,
    temperature: float = 0.3,
    max_tokens: int = 4096,
    prompt_type: Optional[str] = None,
    max_rounds: int = MAX_AGENT_ROUNDS,
    max_tool_calls: int = 0,
    context_length: int = 0,
    active_document=None,
    session_id: Optional[str] = None,
    disabled_tools: Optional[Set[str]] = None,
    owner: Optional[str] = None,
    relevant_tools: Optional[Set[str]] = None,
    fallbacks: Optional[List[tuple]] = None,
    _is_teacher_run: bool = False,
) -> AsyncGenerator[str, None]:
```
(`src/agent_loop.py:1363-1381`)

It yields raw SSE lines (`"data: {...}\n\n"`) with an event vocabulary of `delta`, `tool_start`,
`tool_output`, `tool_progress`, `agent_step`, `metrics`, `doc_stream_open/delta`,
`doc_suggestions`, `doc_update`, `ui_control`, `web_sources`, and finally `[DONE]`.

Every function in the file, with line numbers:

```
41    def _load_mcp_disabled_map() -> Dict[str, set]:
388   def get_builtin_overrides() -> dict:
401   def _section_text(name: str, default: str) -> str:
409   def _assemble_prompt(tool_names: set, disabled_tools: set = None, compact: bool = False) -> str:
514   def _detect_admin_intent(messages: List[Dict]) -> bool:
526   def _extract_last_user_message(messages: List[Dict]) -> str:
537   def _recent_context_for_retrieval(messages, max_user=3, max_chars=600) -> str:
563   def _build_system_prompt(messages, model, active_document, mcp_mgr, ...) -> List[Dict]:
988   def _build_base_prompt(disabled_tools, mcp_mgr, needs_admin, relevant_tools=None, ...):
1084  def _resolve_tool_blocks(round_response, native_tool_calls, round_num):
1113  def _append_tool_results(...):
1189  def _compute_final_metrics(...):
1271  def _build_actions_snapshot(tool_events: list, limit: int = 8000) -> str:
1289  async def _run_verifier_subagent(...):
1339  def _empty_response_fallback(...):
1363  async def stream_agent_loop(...) -> AsyncGenerator[str, None]:
```
There are no classes in this file — it is a pure function pipeline.

### 1.2 System prompt assembly

Two preamble strings gate two entirely different prompt styles depending on whether the
active endpoint speaks native OpenAI-style function calling (see §1.4):

- `_AGENT_PREAMBLE` / `_AGENT_RULES` (`src/agent_loop.py:61-170`) — the fenced-block style
  used for local/non-tool-native models. It tells the model: *"To use a tool, write a
  fenced code block with the tool name as the language tag."*
- `_API_AGENT_RULES` (`src/agent_loop.py:111-170`) — a much shorter rule set used when the
  model is expected to emit real `tool_calls`, plus the same UI-convention block (clickable
  `[text](#kind-<id>)` anchors for sessions/documents/notes/emails/calendar events/tasks/
  skills/research jobs).

`TOOL_SECTIONS` (`src/agent_loop.py:174-386`) is a `dict[str, str]` keyed by tool name (or
tuple in comments, though in practice each key is a single tool name). Each value is either
a full fenced-block example (starts with `` ``` ``) or a one-liner description (starts with
`- `). Representative entry:

```python
"reference_search": """\
```reference_search
{"query": "<what to look for>", "kind": "api"}
```
Search the LOCAL Reference Library — installed catalogs of free public APIs, programming books, build-from-scratch tutorials, and learning roadmaps. `kind` is optional: "api", "book", "tutorial", or "roadmap". USE THIS FIRST when you need a real free API for live data (weather, currency, geo, …): results include each API's auth/HTTPS/CORS requirements, so pick one with `auth: none` and then fetch it with `web_fetch`/`api_call` instead of guessing an endpoint from memory. Local lookup, instant, no network. NEVER invent an API result — if this returns nothing, say so.""",
```
(`src/agent_loop.py:204-208`)

User overrides for any built-in tool description are supported via a `builtin_tool_overrides`
setting (`get_builtin_overrides`, `src/agent_loop.py:388-398`), resolved per-tool by
`_section_text` (`401-406`) which falls back to the shipped default if no override string is
set. This lets the Skills UI edit how the model is *told* to use a native tool without a
restart (the prompt cache key includes a sha256 signature of the override dict — see below).

`_assemble_prompt(tool_names, disabled_tools, compact)` (`src/agent_loop.py:409-457`) builds
the final string:

```python
def _assemble_prompt(tool_names: set, disabled_tools: set = None, compact: bool = False) -> str:
    disabled = disabled_tools or set()
    included = tool_names - disabled

    if compact:
        tool_list = ", ".join(sorted(included)) if included else "none"
        parts = [
            "You are an AI assistant with tool access.",
            f"Available tools: {tool_list}.",
            _API_AGENT_RULES,
        ]
        return "\n\n".join(parts)

    parts = [_AGENT_PREAMBLE]
    full_blocks = []
    one_liners = []
    for name, _default_section in TOOL_SECTIONS.items():
        if name not in included:
            continue
        section = _section_text(name, _default_section)
        if section.startswith("```") or section.startswith("-"):
            if section.startswith("- "):
                one_liners.append(section)
            else:
                full_blocks.append(section)
    if full_blocks:
        parts.append("\n\n".join(full_blocks))
    if one_liners:
        parts.append("## Additional tools\n" + "\n".join(one_liners))

    all_known = set(TOOL_SECTIONS.keys())
    not_shown = all_known - included - disabled
    if not_shown:
        sample = sorted(not_shown)[:5]
        hint = ", ".join(sample)
        if len(not_shown) > 5:
            hint += f", ... ({len(not_shown) - 5} more)"
        parts.append(f"(Other tools available when needed: {hint})")

    parts.append(_AGENT_RULES)
    return "\n\n".join(parts)
```
(`src/agent_loop.py:409-457`)

Note the "(Other tools available when needed: …)" hint line — even tools that were *not*
selected for this turn get name-dropped (first 5) so the model knows they exist without
paying their full description in context.

`_build_base_prompt(disabled_tools, mcp_mgr, needs_admin, relevant_tools=None,
mcp_disabled_map=None, compact=False)` (`src/agent_loop.py:988-1080`) decides RAG-mode vs
full-fallback-mode:

```python
def _build_base_prompt(disabled_tools, mcp_mgr, needs_admin, relevant_tools=None,
                        mcp_disabled_map=None, compact: bool = False):
    from src.tool_index import ALWAYS_AVAILABLE
    disabled = set(disabled_tools or [])
    if not get_setting("image_gen_enabled", True):
        disabled.add("generate_image")

    if relevant_tools is not None:
        # RAG mode: include always-available + retrieved + admin (if needed)
        tool_names = set(ALWAYS_AVAILABLE) | set(relevant_tools)
        if needs_admin:
            tool_names |= _ADMIN_TOOLS
        agent_prompt = _assemble_prompt(tool_names, disabled, compact=compact)
    else:
        # Fallback: full prompt (RAG unavailable)
        agent_prompt = AGENT_SYSTEM_PROMPT
        if not needs_admin:
            mgmt_tools = set(TOOL_SECTIONS.keys()) - set(ALWAYS_AVAILABLE) - {
                "generate_image", "suggest_document",
                "chat_with_model", "ask_teacher", "list_models",
            }
            agent_prompt = _assemble_prompt(
                set(TOOL_SECTIONS.keys()) - mgmt_tools, disabled, compact=compact
            )
        elif compact:
            agent_prompt = _assemble_prompt(set(TOOL_SECTIONS.keys()), disabled, compact=True)
    ...
    return agent_prompt, skill_index_block
```

After that it injects, in order: a Level-0 skill index (one line per published/draft skill,
returned *separately* and wrapped in `untrusted_context_message` by the caller — never
merged into the trusted system role, because skill `name`/`description` are user-editable
and this is an explicit prompt-injection defense), the integrations prompt
(`src.integrations.get_integrations_prompt()`), and MCP tool descriptions
(`mcp_mgr.get_tool_descriptions_for_prompt(...)`).

`_build_system_prompt(...)` (`src/agent_loop.py:563-978`) is the top-level orchestrator that:

1. Computes a cache key `(frozenset(disabled_tools), bool(mcp_mgr), needs_admin,
   frozenset(relevant_tools), compact, sha256(builtin_overrides))` and reuses
   `_cached_base_prompt` when it matches **and there is no active document** — this is a
   process-level in-memory cache, not per-session.
2. Injects current date/time as a fresh block every request (system-local, `%Z`/UTC offset
   included) so the model can't fall back on its training-cutoff date.
3. Injects active-document context in one of three sub-modes: email draft, PDF-form-backed
   document, or a generic document — each with its own prompt block.
4. Injects email-writing-style context and the skills-index/matched-skills block (both as
   separate untrusted user-role messages, per the injection-defense note above).
5. Inserts the assembled system message after any leading system messages, merges
   consecutive non-protected system messages, and places the doc/skills messages
   immediately before the last user message.

### 1.3 Tool selection — RAG + three independent keyword layers

Tool selection happens *before* the model is called, and decides which tool schemas /
descriptions go into the request. There are three separate, non-communicating mechanisms.

**(a) Admin-intent gate** — `_detect_admin_intent` (`src/agent_loop.py:514-523`), a plain
substring match against `_ADMIN_KEYWORDS` (`agent_loop.py:499-512`, 40+ words: `"session"`,
`"delete"`, `"webhook"`, `"mcp"`, `"schedule"`, `"document"`, `"note"`, …). If it matches, the
prompt includes `_ADMIN_TOOLS` (`agent_loop.py:981-986`, a 14-tool set —
`manage_session`, `manage_skills`, `manage_tasks`, `manage_endpoints`, `manage_mcp`,
`manage_webhooks`, `manage_tokens`, `manage_documents`, `manage_settings`,
`create_session`, `list_sessions`, `send_to_session`, `pipeline`, `ask_teacher`,
`list_models`) regardless of RAG results. `_ADMIN_SCHEMA_NAMES` (`agent_loop.py:490-495`) is
a near-identical but *not identical* frozenset used at the function-schema filtering step —
it lacks `manage_documents`/`manage_settings` that `_ADMIN_TOOLS` has.

**(b) `ToolIndex` — embedding/RAG retrieval + word-boundary keyword hints**
(`src/tool_index.py`, 499 lines). Module docstring: *"Instead of injecting all tool
descriptions into the system prompt, embed them in a ChromaDB collection and retrieve only
the top-K relevant ones per user message."*

Always-on baseline sets:

```python
ALWAYS_AVAILABLE = frozenset({
    "bash", "python", "web_search", "web_fetch", "read_file",
    "api_call",  # For configured integrations (Miniflux, Gitea, Linkding, etc.)
    "list_served_models", "stop_served_model",
    "app_api",
})

ASSISTANT_ALWAYS_AVAILABLE = frozenset({
    "list_email_accounts", "list_emails", "read_email", "send_email", "reply_to_email",
    "bulk_email", "archive_email", "delete_email", "mark_email_read",
    "manage_calendar", "manage_notes", "manage_tasks",
    "manage_memory", "web_search", "read_file",
    "create_document", "update_document",
    "resolve_contact", "search_chats",
    "api_call",
    "ui_control",
})
```
(`src/tool_index.py:26-56`) — the latter is used for scheduled/proactive Personal Assistant
runs, which have no live user turn to key retrieval off of.

`BUILTIN_TOOL_DESCRIPTIONS` (`tool_index.py:63-126`) holds ~60 richer, embedding-oriented
tool descriptions (distinct strings from `TOOL_SECTIONS`'s prompt-facing text).

The `ToolIndex` class (`tool_index.py:129-470`):

- `__init__` — gets a ChromaDB client + embedding client, raises `RuntimeError` if no
  embedder is available; creates/gets a collection named `apollo_tool_index` with
  `metadata={"hnsw:space": "cosine"}`.
- `index_builtin_tools()` (`161-199`) — embeds `f"Tool: {name}\n{desc}"` for every builtin,
  upserts with id `builtin_{name}`, prunes stale entries, and stores a sha256 fingerprint of
  the sorted tool-name list.
- `index_mcp_tools(mcp_mgr, disabled_map=None)` (`201-274`) — skips reindex if
  `mcp_mgr._generation` is unchanged; parses `mcp_mgr.get_tool_descriptions_for_prompt(...)`
  line-by-line to recover per-tool text, embeds with id `mcp_{name}`.
- `retrieve(query, k=8)` (`276-297`) — embeds the query, calls
  `collection.query(query_embeddings=..., n_results=min(k, count), include=["metadatas",
  "distances"])`, returns a de-duplicated ordered list of tool names. Any exception → `[]`.

`_KEYWORD_HINTS` (`tool_index.py:311-445`) is a `dict[frozenset[str], set[str]]` — 20
entries, each a set of trigger phrases mapped to tools to force-include. The full frozenset
mechanism, quoted representatively (see the file for all 20 groups):

```python
_KEYWORD_HINTS = {
    frozenset({"free api", "public api", "reference library", "reference_search", "an api for", "api that"}):
        {"reference_search"},
    frozenset({"email", "mail", "gmail", "googlemail", "message", "send", "reply", "inbox", "unread", "tell"}):
        {"list_email_accounts", "list_emails", "read_email", "send_email", "reply_to_email",
         "bulk_email", "delete_email", "archive_email", "mark_email_read", "resolve_contact", "ui_control"},
    frozenset({"calendar", "event", "meeting", "schedule", "appointment"}):
        {"manage_calendar"},
    frozenset({"note", "todo", "reminder", "remind", "checklist", "remember to"}):
        {"manage_notes"},
    frozenset({"recurring", "every day", "every hour", "every morning", "every evening",
               "every night", "every week", "each morning", "daily task", "background task",
               "scheduled task", "schedule a", "automatically", "auto-summarize",
               "auto summarize", "cron", "periodically", "on a schedule", "set up a task",
               "create a task", "summarize my inbox every", "remind me every"}):
        {"manage_tasks"},
    # ... 15 more groups covering contacts, cross-model delegation, research,
    # settings, document editing, cookbook/serving/downloads, UI panels, themes
}
```
(full list: `src/tool_index.py:311-445`)

`get_tools_for_query(query, k=8, always_include=None)` (`tool_index.py:447-470`) is the
combining entry point:

```python
def get_tools_for_query(self, query: str, k: int = 8, always_include=None) -> Set[str]:
    base = set(always_include or ALWAYS_AVAILABLE)
    retrieved = self.retrieve(query, k=k)
    base.update(retrieved)
    ql = query.lower()
    for keywords, tools in self._KEYWORD_HINTS.items():
        if any(re.search(rf"\b{re.escape(kw)}\b", ql) for kw in keywords):
            base.update(tools)
    if self._SCHEDULE_RE.search(ql):
        base.add("manage_tasks")
    return base
```
This is the *word-boundary* keyword pass (`re.search(rf"\b{kw}\b", ql)`) — deliberately
chosen so short hints like "fix"/"line"/"serve"/"reply"/"unread" don't fire inside unrelated
words ("prefix", "deadline", "observe/reserve", "replying", "unreadable"). A structural
`_SCHEDULE_RE` regex (`tool_index.py:299-308`) independently force-adds `manage_tasks` when
the message contains cron-ish phrasing (`"every \w+"`, `"daily/nightly/hourly/weekly"`,
`"at 7:30 am"`, etc.).

The singleton getter throttles failed-init retries to once per 30s
(`_RETRY_INTERVAL = 30.0`, `tool_index.py:475-499`) so a broken ChromaDB/embedding backend
doesn't get hammered on every request.

**(c) `agent_loop.py`'s own orchestration + fallback layer** — the actual call site
(`agent_loop.py:1403-1470`):

1. `_needs_admin = _detect_admin_intent(messages)`.
2. `_retrieval_query = _recent_context_for_retrieval(messages) or _last_user` — built from
   up to the last 3 *user* turns (not just the latest), so a contextless follow-up ("yes",
   "do it in November") inherits the topic of the preceding turn instead of losing tool
   coverage. See `_recent_context_for_retrieval` (`agent_loop.py:537-561`).
3. If no `relevant_tools` was passed in, get `get_tool_index()`; if healthy, reindex MCP
   tools and call `get_tools_for_query(query, 8)`, both wrapped in
   `asyncio.wait_for(..., timeout=_TOOL_SELECTION_TIMEOUT_SECONDS)` where
   `_TOOL_SELECTION_TIMEOUT_SECONDS = 1.5` (`agent_loop.py:496`). On timeout, falls back to
   `set(ALWAYS_AVAILABLE)` only.
4. **Independent keyword-only fallback** when RAG is unavailable — this re-walks
   `ToolIndex._KEYWORD_HINTS` directly but with **plain substring matching**, not
   word-boundary regex:
   ```python
   if not _relevant_tools and _retrieval_query:
       from src.tool_index import ALWAYS_AVAILABLE, ToolIndex
       _relevant_tools = set(ALWAYS_AVAILABLE)
       ql = _retrieval_query.lower()
       for keywords, tools in ToolIndex._KEYWORD_HINTS.items():
           if any(kw in ql for kw in keywords):
               _relevant_tools.update(tools)
       _relevant_tools.update({"create_document", "manage_memory", "manage_notes"})
   ```
   (`agent_loop.py:1453-1462`)
5. If a document is open, force-adds `{"edit_document", "update_document",
   "suggest_document"}` regardless of which path ran.

So three keyword-matching code paths coexist: (a) `ToolIndex.get_tools_for_query`'s
word-boundary loop, (b) `agent_loop.py`'s substring fallback loop over the *same*
`_KEYWORD_HINTS` dict, and (c) `agent_loop.py`'s separate `_ADMIN_KEYWORDS` substring check
for admin gating. `tool_index.py` and `tool_parsing.py` (§1.5) never import each other — tool
*selection* (what goes into the prompt) and dialect *normalization* (how the model's reply
gets parsed back) are fully independent pipeline stages, connected only indirectly through
`src.tool_schemas.function_call_to_tool_block`, which both native tool-call handling and
`<invoke>`-XML parsing funnel through.

**(d) Per-owner visibility gate** — `src/tool_security.py`, applied *before* any of the
above, at `agent_loop.py:1396-1401`:

```python
def blocked_tools_for_owner(owner: Optional[str]) -> Set[str]:
    """Tools to hide/disable for this owner under public-user policy."""
    if owner_is_admin_or_single_user(owner):
        return set()
    return set(NON_ADMIN_BLOCKED_TOOLS)
```
(`src/tool_security.py:72-76`) — `NON_ADMIN_BLOCKED_TOOLS` is a flat 32-tool deny-list
(`bash`, `python`, `manage_memory`, `send_email`, `manage_calendar`, `vault_*`,
`download_model`/`serve_model`/etc., `src/tool_security.py:14-48`); if any are blocked for
this owner, `mcp_mgr` itself is forced to `None` because MCP tools are dynamically namespaced
and can't be selectively hidden per-tool.

### 1.4 Native function-calling vs fenced-block mode

```python
_API_HOSTS = frozenset([
    "api.openai.com", "api.anthropic.com",
    "openrouter.ai", "api.groq.com",
    "api.mistral.ai", "api.cohere.com",
    "api.deepseek.com", "deepseek.com",
    "api.together.xyz", "api.fireworks.ai",
    "api.perplexity.ai", "api.x.ai",
    "ollama.com", "api.venice.ai",
    "localhost", "127.0.0.1", "host.docker.internal",
])
```
(`src/agent_loop.py:474-487`) — note `localhost`/`127.0.0.1`/`host.docker.internal` are
included so local OpenAI-compatible servers (llama.cpp, vLLM, LM Studio) also get native
tool schemas rather than degrading to fenced-block prompting.

`_is_api_model` is computed via a 3-tier decision (setup phase, `agent_loop.py:1472-1523`):
per-endpoint DB override (`ModelEndpoint.supports_tools`, auto-detected from
`--enable-auto-tool-choice` at Cookbook register time) → keyword-sniff the model name → else
`any(host in endpoint_url for host in _API_HOSTS)`. If `_is_api_model`, the prompt uses
`compact=True` (the short `_API_AGENT_RULES` form) and `FUNCTION_TOOL_SCHEMAS` (OpenAI-style
JSON schemas, `src/tool_schemas.py`) are sent as `tools=` on the request so the model emits
real `tool_calls`. Otherwise, only `mcp_schemas` are sent, and only if the last user message
matches `_MCP_KEYWORDS = frozenset(["browse", "browser", "website", "calendar", "event",
"email", "gmail", "screenshot", "navigate", "click", "miniflux", "rss", "feed"])`
(`agent_loop.py:488-489`) — otherwise the local model relies entirely on copying the fenced
tool-block examples from `TOOL_SECTIONS`.

### 1.5 Tool-block parsing (`src/tool_parsing.py`, 443 lines)

`parse_tool_blocks(text)` (`tool_parsing.py:362-428`) supports five dialects, tried as
mutually-exclusive fallback tiers (each pattern only tried if the previous ones matched
nothing):

1. Fenced code blocks: `` ```<tool_tag>\n...``` ``, matched by
   `_TOOL_BLOCK_RE = re.compile(r"```(" + "|".join(TOOL_TAGS) + r")\s*\n([\s\S]*?)```",
   re.IGNORECASE)` (`tool_parsing.py:22-25`) — built directly from `TOOL_TAGS`
   (`src/agent_tools.py:29-66`), so a tool name must be registered there to be parseable at
   all.
2. `[TOOL_CALL]{...}[/TOOL_CALL]` blocks — some models' native format.
3. XML-style `<invoke name="...">` blocks, optionally wrapped in `<tool_call>` or
   `<function_call>`.
4. `<tool_code>{tool => 'name', args => '...'}</tool_code>` — MiniMax-M2.5 style.
5. DeepSeek's fullwidth-pipe DSML markup (`<｜DSML｜tool_calls>`), normalized to standard
   `<invoke>` form *before* any of the above run.

**Before any pattern matching**, two normalization passes run unconditionally:

```python
text = _normalize_dsml(text)
text = _normalize_function_eq(text)
```
(`tool_parsing.py:376-378`)

`_normalize_dsml` (`tool_parsing.py:57-85`) rewrites DeepSeek's `<｜DSML｜tool_calls>` /
`<｜DSML｜invoke name=...>` / `<｜DSML｜parameter name=...>` markup into standard
`<tool_call>`/`<invoke>`/`<parameter>` tags.

**`_normalize_function_eq` — quoted in full, verbatim** (`src/tool_parsing.py:87-108`):

```python
def _normalize_function_eq(text: str) -> str:
    """Normalize the Qwen/Llama-3 native function-call dialect to <invoke> form.

    Models whose chat template teaches `<function=NAME><parameter=KEY>value
    </parameter></function>` (Qwen 3.x, Llama 3.1+ official templates) emit
    exactly that when driven through the fenced-block prompt — observed live
    from Qwen3-365-A3B, whose reference_search call fell through as plain
    text. Rewrites into the standard <invoke name=...> form so the existing
    parser (with its known-tool guard) handles it; a stray </tool_call>
    closer some models append is dropped when unopened.
    """
    if not isinstance(text, str) or "<function=" not in text:
        return text
    t = text
    t = re.sub(r'<function=["\']?(\w+)["\']?\s*>', r'<invoke name="\1">', t, flags=re.IGNORECASE)
    t = re.sub(r"</function>", "</invoke>", t, flags=re.IGNORECASE)
    t = re.sub(r'<parameter=["\']?(\w+)["\']?\s*>', r'<parameter name="\1">', t, flags=re.IGNORECASE)
    # Some emissions close with </tool_call> without ever opening one; a
    # dangling closer would otherwise confuse the XML wrapper scan.
    if "<tool_call>" not in t.lower():
        t = re.sub(r"</tool_call>", "", t, flags=re.IGNORECASE)
    return t
```

This exists because Qwen 3.x and Llama-3.1+'s **official chat templates teach the model a
different native tool-call syntax** (`<function=NAME><parameter=KEY>value</parameter>
</function>`) than the `<invoke name="NAME"><parameter name="KEY">value</parameter>
</invoke>` form Apollo's parser otherwise expects. Without this normalizer, a
fenced-block-prompted Qwen/Llama-3 model that reverts to its own trained tool syntax
produces output the parser cannot recognize at all — the call silently falls through as
plain chat text (the docstring cites this exact failure observed live on Qwen3-365-A3B's
`reference_search` call). The fix is a pure regex rewrite into the `<invoke>` dialect *before*
the XML `<invoke>` parser tier runs, so one converter feeds the existing parser rather than
requiring a whole separate code path.

`_TOOL_NAME_MAP` (`tool_parsing.py:112-207`) is a large alias table (`"shell"`→`"bash"`,
`"terminal"`→`"bash"`, `"code"`→`"python"`, `"fetch"`→`"web_fetch"`, `"cat"`→`"read_file"`,
`"save"`→`"write_file"`, `"document"`→`"update_document"`, `"edit"`→`"edit_document"`, etc.)
used when a model calls a tool by a plausible-but-wrong name.

Per-pattern parsers, each producing a `ToolBlock(tool_type, content)` namedtuple
(`src/agent_tools.py:68`):

- `_parse_tool_call_block(raw)` (`214-276`) — `[TOOL_CALL]` bodies; tries `--command "..."`,
  then `command => "..."`/`command: "..."`, then `args => {...}` nested-brace extraction,
  then `query`/`path`/`code`/`content`/`text`/`file` key=value patterns, then a last-resort
  "everything after the declaration" fallback.
- `_parse_xml_invoke(inv_match)` (`279-302`) — lowercases the tool name (models often emit
  `<invoke name="Bash">`), extracts all `<parameter name="X">value</parameter>` pairs, then
  delegates to `function_call_to_tool_block(tool_name, json.dumps(params))` from
  `src.tool_schemas` — **the same converter used for native OpenAI `tool_calls`**, so
  `<invoke>`-dialect and native-function-call handling share one content-shaping code path.
- `_parse_tool_code_block(raw)` (`305-359`) — MiniMax `<tool_code>` bodies; strips MCP name
  prefixes (`mcp__`, `cli_mcp_server_`, `desktop_commander_`, `mcp_code_executor_`), then
  either structured-param extraction (via the same `function_call_to_tool_block`) or
  tool-specific freeform fallback text.

`strip_tool_blocks(text)` (`tool_parsing.py:431-443`) is the inverse — used to clean
chat-bubble display text of any tool-call markup across all five dialects, then collapses
3+ blank lines to 2.

**Parameter typing** is not done inside `tool_parsing.py` — every extracted `<parameter>`
value is a raw stripped string. Typing/reconstruction happens downstream in
`function_call_to_tool_block` (`src/tool_schemas.py:1122+`), which `json.loads()`s the
JSON-serialized arg dict and reconstructs the flat text content each tool implementation
expects — e.g. `edit_document`'s `<<<FIND>>>...<<<REPLACE>>>...<<<END>>>` blocks are built
from an `edits: [{find, replace}]` array (`tool_schemas.py:1171-1177`); most JSON-arg tools
(`manage_tasks`, `manage_skills`, `api_call`, `manage_settings`, …) get a plain
`json.dumps(args)` passthrough.

### 1.6 Tool dispatch (`src/tool_execution.py`, `src/tool_implementations.py`)

`src/agent_tools.py` is a **facade module** (139 lines) — it re-exports from the real
sub-modules and holds only shared constants:

```python
MAX_AGENT_ROUNDS = 20
SHELL_TIMEOUT = 60
PYTHON_TIMEOUT = 30
MAX_OUTPUT_CHARS = 10_000
MAX_READ_CHARS = 20_000

TOOL_TAGS = {"bash", "python", "web_search", "web_fetch", "browser", "builtin_browser",
             "read_file", "write_file", "create_document", "update_document",
             "edit_document", ... , "reference_search", "python_session", "app_api"}
             # ~70 entries total, src/agent_tools.py:29-66

ToolBlock = namedtuple("ToolBlock", ["tool_type", "content"])
```
(`src/agent_tools.py:19-68`) — a comment notes a tool name must be registered in **four**
places to work at all: schemas, `TOOL_SECTIONS`, `tool_index` descriptions, and `TOOL_TAGS`
(since the fenced-block regex is built directly from this set).

`execute_tool_block(block, session_id=..., disabled_tools=..., owner=..., progress_cb=...)`
is a thin wrapper (`tool_execution.py:735-820`) around `_execute_tool_block_inner`
(`823-1113`) that adds:

- **Autonomy-mode gating**: if `get_setting("agent_autonomy") == "observe"`, every tool in
  `_MUTATING_TOOLS` (bash/python/write/browser/email/image, etc.) is refused so the agent can
  only read/search/propose.
- **Undo snapshotting** for `write_file` via `services.activity_ledger`.
- **Timing/ledger recording** of every tool call.

`_execute_tool_block_inner` is one large `if/elif` chain on `block.tool_type`, dispatching to
`do_*` functions imported from `src.tool_implementations` (itself a re-export shim over
`src/tools/{_common,_state,documents,chats,skills_tasks,admin,web,notes_calendar,cookbook,
media,research_contacts,vault}.py`). Before dispatch, four guard layers run in order:

1. Misformatted-JSON-in-fence detection (`tool_execution.py:857-882`).
2. Caller `disabled_tools` check (`884-894`).
3. `_ADMIN_TOOLS` admin-only check (`896-900`).
4. `is_public_blocked_tool` deployment-policy check (`902-912`).

`reference_search` and `python_session` are special-cased inline rather than delegated to a
`do_*` function; `python_session` routes to `services.python_kernel.get_manager().run(...)`.
Any `mcp__...`-prefixed tool name routes directly to `mcp.call_tool(tool, args)`. Background
bash (`#!bg` first-line marker) is intercepted before MCP routing and handed to
`src.bg_jobs.launch` — the caller gets a job id back immediately and is automatically
re-invoked with full output once the job finishes, so a long install/build never blocks the
SSE stream.

**Per-tool error containment** — a single tool crashing never kills the whole turn:

```python
try:
    desc, result = await _tool_task
except Exception as _tool_err:
    logger.exception("tool execution raised unexpectedly: %s", block.tool_type)
    desc = f"{block.tool_type}: internal error"
    result = {"error": f"Tool crashed internally: {_tool_err}", "exit_code": 1}
```
(`agent_loop.py:2114-2124`)

**Timeouts and output caps** — the system prompt text says "60s timeout per tool, 10K char
output limit" (`_AGENT_RULES`, `agent_loop.py:71`), but this is stale relative to the code.
The actual bash/python timeout is **1 hour by default**, overridable via env:

```python
DEFAULT_BASH_TIMEOUT = _env_timeout("APOLLO_BASH_TIMEOUT", 60 * 60)     # 1 hour
DEFAULT_PYTHON_TIMEOUT = _env_timeout("APOLLO_PYTHON_TIMEOUT", 60 * 60)
```
(`src/tool_execution.py:178-201`) — the comment explains the change: the old 60s timeout
"starves real workloads (pip install, ffmpeg conversions, etc.)" and made the agent go silent
mid-task. The legacy `SHELL_TIMEOUT = 60` / `PYTHON_TIMEOUT = 30` constants in
`agent_tools.py` still exist but are unreferenced by the execution path (vestigial). Timeout
enforcement itself is `asyncio.wait_for(proc.wait(), timeout=timeout)` inside
`_run_subprocess_streaming` (`tool_execution.py:286-296`); on timeout the process is killed
and a `{"error": "...timed out after Ns — process killed", "exit_code": 124}` result returned.

The **10K char output cap is current and enforced**:

```python
MAX_OUTPUT_CHARS = 10_000
MAX_READ_CHARS = 20_000

def _truncate(text: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    if len(text) > limit:
        return text[:limit] + f"\n... (truncated, {len(text)} chars total)"
    return text
```
(`tool_execution.py:24-25, 218-221`), applied to bash/python stdout+stderr and to
`web_search`/`web_fetch` output; `read_file` uses the larger 20K cap instead.

Other timeout constants: `web_search` internal fetch — `timeout=30`; `web_fetch` — inner
`timeout=10` wrapped in an outer `timeout=30`; the verifier subagent's synthesis call —
`timeout=60`.

### 1.7 Iteration limits and the loop-breaker

`MAX_AGENT_ROUNDS = 20` (`src/agent_tools.py:22`) bounds the main
`for round_num in range(1, max_rounds + 1):` loop (`agent_loop.py:1621`). There is **no
explicit "round limit hit" branch** — running out of rounds simply falls through to the
same post-loop code (`_empty_response_fallback`, metrics, `[DONE]`) as a normal "model
returned no more tool calls" exit. The mechanism that actually prevents most 20-round burns
is a separate stall detector ("Terminus-style loop-breaker", `agent_loop.py:1972-2025`):

- Builds a signature of the current round's tool calls, tracked in a
  `deque(maxlen=6)` (`_recent_call_sigs`) plus a `Counter` of tool-type frequency
  (`_tool_type_counts`).
- A round is "useless" only if it **repeats a recent signature and produced no real text**;
  `_stuck_rounds` increments on that, resets on any new/distinct call or genuine text output.
- Trips when `_stuck_rounds >= 4`, **or** any single tool type has fired `>= 15` times
  (`_runaway`).
- On trip: sets `_force_answer = True`, injects a system message telling the model to stop
  and either answer or state what's blocking it, and `continue`s the loop **without
  executing** that round's tool_blocks.

A separate `max_tool_calls` budget (opt-in, `0` = unlimited) is enforced inside the
per-tool dispatch loop: `if max_tool_calls > 0 and total_tool_calls >= max_tool_calls: yield
budget_exceeded; break`.

Also worth noting: an optional **verifier subagent** (`_run_verifier_subagent`,
`agent_loop.py:1289-1336`) — a second, independent LLM call that judges whether an
"effectful" turn (one that used `create_document`/`update_document`/`edit_document`/`bash`/
`python`/`write_file`) actually accomplished what it claims. Gated by the
`agent_verifier_subagent` setting, capped at `_VERIFIER_MAX_ROUNDS = 2` re-tries. On FAIL it
injects a system message and loops again instead of ending the turn.

Finally, when the whole student turn completes and `not _is_teacher_run`, a **teacher
escalation** hook (`src.teacher_escalation.run_teacher_inline`) can transparently take over
and forward its own events if the student's Tier-1 self-check flagged failure — guarded
against infinite recursion by `_is_teacher_run`.

---

## 2. Memory system (mem0-style writer/recall/consolidation/auto-distill)

Two near-duplicate `MemoryManager` implementations exist: `src/memory.py` (369 lines, the
one the live app imports — adds a `uses` counter) and `services/memory/memory.py` (359
lines, adds `claim_ownerless()` instead — they have diverged). `src/memory_vector.py` and
`services/memory/memory_vector.py` are **byte-identical**. The newer "second-brain" layer
(`services/memory/brain.py`, `distiller.py`, `memory_extractor.py`) sits on top and is what
`src/builtin_actions.py` drives for auto-distill/audit.

### 2.1 Writer path

The `manage_memory` tool (action=`add`) dispatches through `src/ai_interaction.py`:

```python
elif tool == "manage_memory":
    action = content.split("\n")[0].strip()[:40]
    desc = f"manage_memory: {action}"
    result = await do_manage_memory(content, session_id, owner=owner)
```
(`src/ai_interaction.py:1799-1802`), and the add branch of `do_manage_memory`:

```python
elif action == "add":
    ...
    entry = _memory_manager.add_entry(text, source="ai_agent", category=category, owner=owner)
    memories = _memory_manager.load_all()
    memories.append(entry)
    _memory_manager.save(memories)

    if _memory_vector and hasattr(_memory_vector, 'healthy') and _memory_vector.healthy:
        try:
            _memory_vector.add(entry["id"], text)
        except Exception as error:
            report_exception(logger, "ai_interaction_memory_vector_add_failed", error, outcome="best_effort", context={"memory_id": entry["id"]})
    try:
        from src.event_bus import fire_event
        fire_event("memory_added", owner)
    except Exception:
        logger.debug("memory_added event dispatch failed", exc_info=True)

    return {"action": "add", "memory_id": entry["id"],
            "results": f"Memory added: [{category}] {text}"}
```
(`src/ai_interaction.py:970-996`)

The storage primitive:

```python
def add_entry(self, text: str, source: str = "user", category: str = "fact", owner: str = None) -> Dict:
    if not text.strip():
        raise ValueError("Memory text cannot be empty")
    entry = {
        "id": str(uuid.uuid4()),
        "text": text.strip(),
        "timestamp": int(time.time()),
        "source": source,
        "category": category,
        "uses": 0,
    }
    if owner:
        entry["owner"] = owner
    return entry
```
(`src/memory.py:197-212`), persisted atomically (`json.dump` to a `.tmp` file, then
`os.replace`, `src/memory.py:178-195`). Firing `memory_added` is what feeds the consolidation
trigger (§2.3). A near-identical MCP-server tool exists at `mcp_servers/memory_server.py:47-122`
for MCP-facing clients, using the manager classes directly rather than the module-level
`ai_interaction.py` singletons.

Two other write paths feed the same `add_entry`/`save` primitives: live per-turn extraction
and batch session distillation — both covered in §2.4 (auto-distill).

### 2.2 Recall path

`memory_recall_max` (default `3`) caps how many **extended** (non-pinned) memories get
injected per turn:

```python
    # Context budget: caps on memory injection into the prompt preface.
    # Pinned memories were previously unbounded — a large pinned set could
    # eat a small local model's context before the request started.
    "memory_recall_max": 3,
    "memory_pinned_max": 15,
```
(`src/settings.py:50-54`), read at:

```python
recall_k = int(get_setting("memory_recall_max", 3))
relevant = (
    self._hybrid_retrieve(message, extended, k=recall_k)
    if recall_k > 0 else []
)
```
(`src/chat_processor.py:229` context) — `<= 0` disables extended recall; pinned memories
still always inject, capped separately by `memory_pinned_max` (default `15`, newest-wins when
over cap, via `_cap_pinned`).

Retrieval is a **hybrid BM25-keyword + vector-cosine** scorer,
`ChatProcessor._hybrid_retrieve` (`src/chat_processor.py:61-164`):

```python
def _hybrid_retrieve(self, message: str, mem_entries: list, k: int = 5) -> list:
    """Retrieve memories relevant to the message.
    Uses BM25-style keyword scoring + optional vector similarity.
    Recency is a tiebreaker only, never the primary signal.
    """
    ...
    N = len(mem_entries)
    doc_freq = Counter()
    mem_token_cache = {}
    for mem in mem_entries:
        toks = set(_content_tokens(mem["text"]))
        mem_token_cache[mem["id"]] = toks
        for t in toks:
            doc_freq[t] += 1

    def _bm25_score(query_toks, mem_id):
        mem_toks = mem_token_cache.get(mem_id, set())
        if not mem_toks or not query_toks:
            return 0.0
        score = 0.0
        mem_len = len(mem_toks)
        avg_len = max(sum(len(v) for v in mem_token_cache.values()) / N, 1)
        k1, b = 1.5, 0.75
        for qt in query_toks:
            if qt not in mem_toks:
                continue
            df = doc_freq.get(qt, 0)
            idf = math.log((N - df + 0.5) / (df + 0.5) + 1)
            tf = 1
            tf_norm = (tf * (k1 + 1)) / (tf + k1 * (1 - b + b * mem_len / avg_len))
            score += idf * tf_norm
        return score

    has_vector = self.memory_vector and self.memory_vector.healthy
    vector_scores = {}
    if has_vector:
        results = self.memory_vector.search(message, k=min(k * 3, 20))
        mem_by_id = {m["id"]: m for m in mem_entries}
        for r in results:
            if r["memory_id"] in mem_by_id:
                vector_scores[r["memory_id"]] = max(r["score"], 0.0)

    scored = []
    for mem in mem_entries:
        mid = mem["id"]
        vs = vector_scores.get(mid, 0.0)
        kw = _bm25_score(query_tokens, mid)
        kw_norm = min(kw / 6.0, 1.0) if kw > 0 else 0.0

        # Category-aware boost for identity/contact/preference queries
        category = mem.get("category", "fact")
        cat_boost = 1.0
        if any(w in msg_lower for w in ["name", "who am i", "my name"]):
            if category == "identity" or any(w in mem_lower for w in ["name is", "i am", "called"]):
                cat_boost = 1.4
        elif any(w in msg_lower for w in ["phone", "email", "address", "contact"]):
            if category == "contact" or "@" in mem_lower:
                cat_boost = 1.3
        elif any(w in msg_lower for w in ["like", "prefer", "favorite"]):
            if category == "preference":
                cat_boost = 1.2
        kw_norm = min(kw_norm * cat_boost, 1.0)

        ts = mem.get("timestamp", 0)
        days_old = max((now - ts) / 86400, 0)
        recency = 1.0 / (1.0 + days_old * 0.05)

        if has_vector:
            if vs < 0.20 and kw_norm < 0.08:
                continue
            final = (0.55 * vs) + (0.40 * kw_norm) + (0.05 * recency)
        else:
            if kw_norm < 0.08:
                continue
            final = (0.95 * kw_norm) + (0.05 * recency)

        if final > 0.12:
            scored.append((final, mem))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [mem for _, mem in scored[:k]]
```

Vector search uses ChromaDB cosine distance converted to a similarity score
(`score = 1.0 - distance`, `services/memory/memory_vector.py:92-116`). After injection,
`memory_manager.increment_uses(_used_ids)` bumps a `uses` counter on whichever memories were
actually surfaced.

A **separate, older Jaccard-only** relevance function, `MemoryManager.get_relevant_memories`
(`src/memory.py:273-369`), is what the explicit `manage_memory search` tool action and the MCP
server's `search` action actually call — so automatic context-preface injection (BM25+vector
hybrid) and explicit tool-driven search (pure Jaccard) use **two different algorithms**.

### 2.3 Consolidation (dedup/merge)

Two mechanisms at two granularities:

**A. `consolidate_memory`** — an event-scheduled per-owner tidy pass
(`src/builtin_actions.py:342-551`), fired every 5 `memory_added` events:

```python
"consolidate_memory": {"name": "Memory Tidy", "trigger_type": "event",
                        "trigger_event": "memory_added", "trigger_count": 5, ...}
```
(`src/task_scheduler.py:196`). Primary path is LLM-driven keep/drop, id-anchored (never
invents ids not in the input set):

```python
prompt = (
    "You are tidying a user's saved personal memories. Return ONLY raw JSON, no markdown.\n"
    "Remove memories that are empty, broken, trivial conversation filler, duplicates, or obsolete "
    "because a clearer newer memory replaces them. Preserve useful personal facts, preferences, "
    "contacts, project context, and instructions. If memories conflict, keep the clearest/latest "
    "one and drop the obsolete one.\n\n"
    "JSON shape:\n"
    "{\"keep\":[{\"id\":\"existing id\",\"text\":\"cleaned text\",\"category\":\"fact|preference|identity|event|contact|project|instruction\"}],"
    "\"drop\":[{\"id\":\"existing id\",\"reason\":\"short reason\"}]}\n\n"
    f"MEMORIES:\n{json.dumps(items, ensure_ascii=False)}"
)
```
(`src/builtin_actions.py:408-417`). If no endpoint/model resolves, or the call fails, it
falls back to exact-text dedup by normalized key (`" ".join(text.lower().split())`).

**B. `audit_memories`** — a background LLM curator triggered automatically *inside* the live
extractor every `AUDIT_INTERVAL = 5` newly-added memories
(`services/memory/memory_extractor.py:106-107, 386-393`), deliberately conservative:

```python
AUDIT_SYSTEM_PROMPT = (
    "You are a memory database curator. Be CONSERVATIVE: remove only TRUE "
    "duplicates and clearly useless entries. Every distinct fact must survive. "
    "When in doubt, KEEP the entry. Return the cleaned list.\n\n"
    "Rules:\n"
    "1. MERGE only entries that state the SAME fact in different words. If you "
    "are not sure two entries are the same fact, KEEP BOTH. ...\n"
    "2. REMOVE only entries that are genuinely worthless ...\n"
    "3. Keep the original wording. ...\n"
    "4. Preserve the 'id' of the entry you keep when merging.\n"
    "5. Never invent facts. When unsure, KEEP.\n\n"
    "Return a JSON array of objects with fields: id, text, category.\n"
    "Return ONLY valid JSON, no markdown fences."
)
```
(`services/memory/memory_extractor.py:85-104`). A fingerprint short-circuit skips the LLM
call entirely if nothing changed since the last audit. An explicit **over-deletion safety
net** refuses to save a catastrophic cut:

```python
if before_count >= 8 and after_count < before_count * 0.5:
    logger.warning(
        f"Memory audit would cut {before_count} -> {after_count} "
        f"(>50% removed) — refusing as unsafe, keeping originals"
    )
    return {"before": before_count, "after": before_count, "error": "unsafe_removal"}
```
(`services/memory/memory_extractor.py:529-539`). After a successful audit, the vector index
is fully rebuilt (`memory_vector.rebuild(final_entries)`).

**C. Write-time dedup gate** in `distill_and_store` — every candidate fact from batch
distillation is checked with `find_duplicates` (exact lowercase text match,
`src/memory.py:229-235`) before insertion; the live extractor additionally does vector-
similarity dedup at threshold `0.72` before falling back to exact/fuzzy (Jaccard ≥ `0.6`)
text dedup.

### 2.4 Auto-distill — two distinct mechanisms

**A. Live per-turn extraction** — `memory_extractor.extract_and_store`
(`services/memory/memory_extractor.py:225-398`), a background task after each LLM response,
scanning only the last `CONTEXT_WINDOW = 6` messages of the live session:

```python
EXTRACT_SYSTEM_PROMPT = (
    "You are a memory extraction assistant. Analyze the conversation and extract ONLY "
    "durable personal facts about the user that would be useful across many future conversations.\n\n"
    "Good examples: name, job title, city, family members, long-term projects, strong preferences.\n"
    "Bad examples: what they asked about today, temporary moods, generic statements, "
    "things the assistant said, one-off tasks, opinions on the current topic.\n\n"
    "Rules:\n"
    "- MAX 2 facts per conversation — only the most important\n"
    "- Only extract facts the USER stated or clearly implied\n"
    "- Each fact must be a single short sentence (under 15 words)\n"
    "- If a fact is similar to something likely already known, skip it\n"
    "- If nothing durable was revealed, return []\n\n"
    "Return a JSON array of objects with 'text' and 'category' fields.\n"
    "Categories: 'identity', 'preference', 'fact', 'contact', 'project', 'goal'\n\n"
    "Return ONLY valid JSON, no markdown fences."
)
```
(`services/memory/memory_extractor.py:65-80`). A **regex-based, LLM-free fallback** runs
alongside it so identity/preference statements survive even if the extraction model misses
them — `_fallback_memory_candidates` (`144-206`) pattern-matches `\bmy name is\s+...`,
`\bcall me\s+...`, `\bi (?:live in|am from)...`, `\bi (?:prefer|like|love|hate)...`, capped to
2 candidates.

Identity facts are auto-pinned on write:

```python
entry = memory_manager.add_entry(fact_text, source="auto", category=category, owner=_owner)
if category == "identity":
    entry["pinned"] = True
```
(`services/memory/memory_extractor.py:355-357`) — this is the direct link to the recall
path's pinned/extended split in §2.2.

**B. Session distiller** — `services/memory/distiller.py` (batch, whole-transcript, not just
the last 6 messages), driven by the scheduled/manual `auto_distill_sessions` action:

```python
_SYSTEM = (
    "You extract durable, atomic facts from a conversation to store in a personal "
    "knowledge base. Output ONE fact per line, each a short standalone statement "
    "(no first-person, no 'the user asked'). Capture preferences, decisions, "
    "identity, projects, and stable facts. Skip chit-chat, transient context, and "
    "anything not worth remembering later. If there is nothing durable, output NONE."
)
```
(`services/memory/distiller.py:8-14`) — `parse_facts` strips bullet/number markers and skips
lines matching `{"none", "(none)", "n/a", ...}`.

Scheduler entry point `action_auto_distill_sessions` (`src/builtin_actions.py:70-150`) finds
sessions whose `last_message_at` is past a per-owner watermark stored in
`settings["auto_distill_watermarks"]`, caps to 10 sessions per run, and advances the
watermark. Registration:

```python
"auto_distill_sessions": {"name": "Memory Auto-Distill", "schedule": "cron",
                           "cron_expression": "0 */6 * * *", "ship_paused": True, ...}
```
(`src/task_scheduler.py:197`) — every 6 hours, but **ships paused** (opt-in) because "every
run spends utility-model tokens."

A pure regex-only fallback extractor with no LLM call at all,
`MemoryManager.extract_memory_from_chat` (`src/memory.py:40-83`), also exists but appears
unwired from any live call site — legacy/dead fallback path.
`UNCERTAIN:` whether any code path still invokes it; grep found none in the routes/services
tree.

**No mem0-style per-fact ADD/UPDATE/DELETE/NONE classifier.** Apollo's audit/tidy passes
produce a batch `{"keep": [...], "drop": [...]}` (or a cleaned array with preserved ids) —
conceptually ADD/UPDATE/DELETE by set-difference against the original id list, but the model
is never asked to label each candidate individually the way mem0's canonical extractor does.
Dedup ahead of any LLM call (vector similarity ≥0.72, exact-text match, Jaccard ≥0.6) is
purely deterministic/algorithmic — there is no LLM-driven NONE-classification step at write
time.

### 2.5 Config knobs

| Key | Default | Location |
|---|---|---|
| `memory_recall_max` | `3` | `src/settings.py:53` |
| `memory_pinned_max` | `15` | `src/settings.py:54` |
| `memory_pack_sync_dir` | `""` | `src/settings.py:43` — cross-machine sync folder for Memory Sync task |
| `CONTEXT_WINDOW` (extractor) | `6` | `services/memory/memory_extractor.py:83` |
| `AUDIT_INTERVAL` | `5` | `services/memory/memory_extractor.py:106` |
| vector dedup threshold (live extraction) | `0.72` | `services/memory/memory_extractor.py:337` |
| `_is_text_duplicate` Jaccard threshold | `0.6` | `services/memory/memory_extractor.py:209` |
| Memory Tidy trigger count | `5` events | `src/task_scheduler.py:196` |
| Memory Auto-Distill cron | `0 */6 * * *`, ships paused | `src/task_scheduler.py:197` |
| Consolidate safety threshold | refuse if `after < before * 0.5` and `before >= 8` | `services/memory/memory_extractor.py:534` |

---

## 3. Local model serving (`services/localmodels/server_manager.py`, 350 lines)

Module docstring: *"Launch and track local llama-server processes (single warm chat
model)."* (`server_manager.py:1`)

### 3.1 Single-warm-model policy

Two independent "slots" — one for chat models, one for embedding models — so at most one
chat model and one embedding model can be warm simultaneously:

```python
self._chat: Optional[_Proc] = None
self._embed: Optional[_Proc] = None
```
(`server_manager.py:83-84`). Enforcement lives in `ensure_running`:

```python
def ensure_running(self, ref: str) -> str:
    m = self._resolve(ref)
    if m is None:
        self.refresh_catalog()
        m = self._resolve(ref)
    if m is None:
        raise LookupError(f"Unknown local model: {ref!r}")
    if m.kind == "unsupported":
        raise ValueError(
            f"'{m.name}' (architecture: {m.arch or 'unknown'}) is not a "
            "chat-capable model — llama-server cannot serve it"
        )
    with self._lock:
        slot = self._embed if m.kind == "embedding" else self._chat
        if slot and slot.model_id == m.id and slot.proc.poll() is None:
            return slot.base_url
        if slot:
            self._stop_proc(slot)
        proc = self._launch(m)
        if m.kind == "embedding":
            self._embed = proc
        else:
            self._chat = proc
        return proc.base_url
```
(`server_manager.py:142-169`) — pick the slot for the requested kind → if the same model is
already running, reuse it → otherwise stop whatever occupies that slot → launch and occupy
it. The whole sequence is guarded by a `threading.RLock` so concurrent HTTP requests can't
race the slot bookkeeping.

### 3.2 Start/stop lifecycle

Binary + port + context-size resolution, then subprocess spawn:

```python
def _launch(self, m: LocalModel) -> _Proc:
    binary = self.find_binary()
    if not binary:
        ... # raises RuntimeError with a platform-specific install hint
    port = _free_port(self._host)
    cmd = [
        binary, "--model", m.path,
        "--host", self._host, "--port", str(port),
        "-c", str(self._serving_context(m)),
    ]
    if m.kind == "embedding":
        cmd.append("--embedding")
    log_path = os.path.join(tempfile.gettempdir(), f"apollo-llama-{port}.log")
    logf = open(log_path, "w")
    proc = subprocess.Popen(cmd, stdout=logf, stderr=subprocess.STDOUT, text=True)
    logf.close()
    base_url = f"http://{self._host}:{port}"
    self._wait_health(base_url, proc, log_path, timeout=self._health_timeout_for(m))
    return _Proc(m.id, m.name, m.kind, port, proc, base_url, log_path)
```
(`server_manager.py:201-258`, condensed — the source additionally wraps `_wait_health` in a
try/except that `proc.terminate()`s on failure).

Key facts about this spawn:

- **No GPU flags at all** — the `cmd` list is only `--model`, `--host`, `--port`, `-c`, and
  optionally `--embedding`. There is no `-ngl`/`--n-gpu-layers`/`--gpu-layers` anywhere in
  this module — GPU offload is left to llama-server's own binary-level defaults.
- **Port allocation** — `_free_port(host)` binds to port 0 to let the OS pick a free
  ephemeral port, reads it back, closes the socket, and reuses that number for
  `--port`:
  ```python
  def _free_port(host: str) -> int:
      s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
      try:
          s.bind((host, 0))
          return s.getsockname()[1]
      finally:
          s.close()
  ```
  (`server_manager.py:61-67`)
- **`--host` binding** — `self._host` defaults to `"127.0.0.1"` (loopback-only, never
  `0.0.0.0`).
- **No `cwd=` or `env=`** passed to `Popen` — the child inherits the Apollo backend
  process's working directory and full environment unmodified.
- **Context size** is computed, not fixed:
  ```python
  def _serving_context(self, m: LocalModel) -> int:
      """Apollo's prompt packer budgets against the model's KNOWN window, so a
      fixed small -c rejects long chats with HTTP 400. Serve min(known window, cap)
      instead — the cap (APOLLO_LLAMA_CONTEXT, default 16384) keeps the KV cache
      bounded; the configured default stays the floor."""
      cap = max(int(os.getenv("APOLLO_LLAMA_CONTEXT", "16384")), self._context)
      known = _lookup_known(m.name or m.id)  # from src.model_context
      if known:
          return max(self._context, min(known, cap))
      return cap
  ```
  (`server_manager.py:171-199`, condensed) — `self._context` defaults to `4096`.

### 3.3 Health check

```python
def _wait_health(self, base_url, proc, log_path, timeout=None):
    deadline = time.monotonic() + (timeout if timeout else self._health_timeout)
    url = base_url + "/health"
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"llama-server exited early (code {proc.returncode}):\n{_tail(log_path)}")
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if r.status == 200:
                    return
        except (urllib.error.URLError, OSError, TimeoutError):
            time.sleep(0.5)
    raise TimeoutError("llama-server did not become healthy in time")
```
(`server_manager.py:268-284`) — polls `GET {base_url}/health` every 0.5s, success on HTTP
200, also checks for early process exit each iteration (attaching the log tail to the error).
The overall deadline scales with model size:

```python
def _health_timeout_for(self, m: LocalModel) -> float:
    """Measured live: a 8.4GB 14B at -c 16384 needs >180s on this hardware.
    Allow ~40s/GB with the configured timeout as the floor."""
    size_gb = (m.size_bytes or 0) / (1024 ** 3)
    return max(self._health_timeout, size_gb * 40.0)
```
(`server_manager.py:260-266`) — base `self._health_timeout` defaults to `180.0`s.

### 3.4 Auto-detect candidate paths

**Binary** — an explicitly configured path (Settings → AI, or `APOLLO_LLAMA_SERVER` env)
wins outright; if set but the file doesn't exist, `find_binary` returns `None` rather than
silently falling back to a different binary. Otherwise it walks a platform candidate list:

```python
def _bin_candidates() -> list[str]:
    if os.name == "nt":
        ...
        return [
            "llama-server",
            os.path.join(home, "scoop", "shims", "llama-server.exe"),
            os.path.join(local_appdata, "llama.cpp", "llama-server.exe"),
            os.path.join(program_files, "llama.cpp", "llama-server.exe"),
            os.path.join(home, "llama.cpp", "build", "bin", "Release", "llama-server.exe"),
        ]
    return [
        "llama-server",
        os.path.expanduser("~/.local/bin/llama-server"),
        os.path.expanduser("~/bin/llama-server"),
        os.path.expanduser("~/llama.cpp/build/bin/llama-server"),
        "/opt/homebrew/bin/llama-server",
        "/usr/local/bin/llama-server",
    ]
```
(`server_manager.py:23-44`) — Windows also checks Scoop shims, `%LOCALAPPDATA%`, and
`%ProgramFiles%`; macOS/Linux checks Homebrew's two standard prefixes plus `~/.local/bin`
and a manually-built `~/llama.cpp/build` tree. `BINARY_ENV_VAR = "APOLLO_LLAMA_SERVER"`
(`services/localmodels/config.py:9`).

**Model directories** — resolution order is configured dirs (Settings) → `APOLLO_MODELS_DIRS`
env var → built-in defaults:

```python
def _default_dirs() -> list[str]:
    if os.name == "nt":
        return [
            os.path.join(home, "Desktop", "AI_Models"),
            os.path.join(home, "AI_Models"),
            os.path.join(home, ".lmstudio", "models"),
        ]
    return [
        "/Volumes/MainStore/Development/AI_Models",
        os.path.expanduser("~/Desktop/AI_Models"),
    ]
```
(`services/localmodels/config.py:12-27`) — note the macOS default includes a specific
external-volume path (`/Volumes/MainStore/...`), a developer-machine artifact rather than a
generic default.

Directory walk (`scanner.py:69-111`) skips `_SKIP_DIRS = {"cache", ".cache", "llama-cache",
"ollama", ".ollama", "blobs", "tmp", ".git"}`, matches `*.gguf` case-insensitively, skips
AppleDouble `._*` files and `mmproj` projector files, and de-duplicates multi-part GGUF
splits (keeps only `-00001-of-000NN.gguf`). Model id is `"lm_" + sha1(path)[:16]`.

### 3.5 Stop logic

```python
def _stop_proc(self, slot: _Proc) -> None:
    try:
        slot.proc.terminate()
        slot.proc.wait(timeout=10)
    except Exception as error:
        report_exception(...)
        try:
            slot.proc.kill()
        except Exception as cleanup_error:
            report_exception(...)
    if slot is self._chat:
        self._chat = None
    if slot is self._embed:
        self._embed = None
```
(`server_manager.py:286-311`) — SIGTERM, wait up to 10s, SIGKILL on timeout. **No process
group handling** (no `os.killpg`/`preexec_fn=os.setsid`) — only the direct child PID is
signaled. **No psutil usage.** No explicit port cleanup — the port frees itself when the OS
process exits.

### 3.6 Registry / scanner separation

`scanner.py` is pure filesystem discovery with no state of its own — a `LocalModel`
dataclass (`id, name, path, quant, kind, size_bytes, directory, arch`) built from an
`os.walk`. `registry.py` projects that discovered catalog into Apollo's DB-backed
`ModelEndpoint` table (`LOCAL_BASE_URL = "local://llama.cpp"`) so the model picker UI can
list local models like any other endpoint — `sync_managed_endpoint(models)` upserts a single
row keyed on `base_url == LOCAL_BASE_URL`. `lifecycle.rescan()` ties scan → server catalog →
DB sync together, and `startup_scan()` runs it once at process boot, swallowing exceptions so
a bad scan dir never crashes startup.

**HTTP surface** — `routes/localmodels_routes.py`, prefix `/api/local-models`, every route
`require_admin`-gated (the module docstring explicitly calls these more privileged than
normal admin routes because they enumerate the filesystem and spawn/kill OS processes):

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/local-models` | catalog + running status |
| POST | `/api/local-models/scan` | `lifecycle.rescan()` |
| GET | `/api/local-models/voices` | discover Piper TTS voices |
| GET/PUT | `/api/local-models/dirs` | configured scan dirs |
| GET/PUT | `/api/local-models/binary` | configured/resolved llama-server binary |
| POST | `/api/local-models/{id}/start` | `ensure_running(id)` |
| POST | `/api/local-models/{id}/stop` | `stop(id)` |

---

## 4. Endpoint resolution (`src/endpoint_resolver.py`, 419 lines)

Module docstring: *"Consolidates the 4+ copies of normalize_base / resolve_endpoint logic
into one place."* All functions:

```
31   def _first_chat_model(models) -> Optional[str]:
39   def _endpoint_cached_models(ep) -> list:
51   def _endpoint_hidden_models(ep) -> set:
63   def _endpoint_enabled_models(ep) -> list:
78   def _resolve_tailscale_host(hostname: str) -> Optional[str]:
124  def resolve_url(url: str) -> str:
140  def normalize_base(url: str) -> str:
152  def _anthropic_api_root(base: str) -> str:
160  def _ollama_api_root(base: str) -> str:
173  def build_chat_url(base: str) -> str:
184  def build_models_url(base: str) -> str:
195  def build_headers(api_key, base) -> Dict[str, str]:
212  def resolve_endpoint(setting_prefix, fallback_url=None, fallback_model=None, fallback_headers=None, owner=None):
317  def resolve_endpoint_by_id(ep_id, model=None, owner=None):
365  def resolve_chat_fallback_candidates(owner=None) -> list:
375  def resolve_utility_fallback_candidates(owner=None) -> list:
393  def resolve_vision_fallback_candidates(owner=None) -> list:
398  def _resolve_fallback_candidates(setting_key, owner=None) -> list:
```

`_ollama_api_root`, quoted in full:

```python
def _ollama_api_root(base: str) -> str:
    """Return the native Ollama API root, adding /api for ollama.com hosts."""
    base = (base or "").strip().rstrip("/")
    parsed = urlparse(base)
    path = (parsed.path or "").rstrip("/")
    if path.endswith("/api"):
        return base
    if _host_match(base, "ollama.com"):
        root = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else "https://ollama.com"
        return root.rstrip("/") + "/api"
    return base
```
(`src/endpoint_resolver.py:160-170`) — if the base already ends in `/api`, return unchanged
(already a native Ollama root, e.g. `http://localhost:11434/api`); if the host is
`ollama.com` (or a subdomain), rebuild `scheme://netloc` and append `/api`; any other host is
returned unchanged. `build_chat_url`/`build_models_url` then append `/chat` or `/tags`. A
second, non-identical `_ollama_api_root` lives in `src/llm_core.py:175-191` (used by the LLM
dispatch layer on already-built URLs; it additionally trims `/api/chat`, `/api/tags`,
`/api/generate` suffixes back to `/api`), paired with `_is_ollama_native_url`
(`llm_core.py:160-172`, treats `ollama.com` or `localhost`/`127.0.0.1`/port `11434` with an
`/api` path as native Ollama).

Provider detection (imported from `src/llm_core.py`, not reimplemented in the resolver):

```python
def _host_match(url: str, *domains: str) -> bool:
    """Return True if url's hostname equals any of `domains` or is a subdomain of one."""
    host = (urlparse(url).hostname or "").lower().rstrip(".")
    return any(host == d or host.endswith("." + d) for d in domains)

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
(`src/llm_core.py:281-299, 318-335`) — hostname-exact/subdomain matching, not substring, so
`anthropic.com.example` is not misclassified. Unknown hosts fall back to the OpenAI-compatible
default, which the majority of providers implement.

Cloud/OpenAI-compatible URL building and auth headers:

```python
def build_chat_url(base: str) -> str:
    base = resolve_url(base)
    provider = _detect_provider(base)
    if provider == "anthropic":
        return _anthropic_api_root(base) + "/v1/messages"
    if provider == "ollama":
        return _ollama_api_root(base) + "/chat"
    return base + "/chat/completions"

def build_headers(api_key: Optional[str], base: str) -> Dict[str, str]:
    provider = _detect_provider(base)
    headers: Dict[str, str] = {}
    if provider == "anthropic":
        if api_key:
            headers["x-api-key"] = api_key
        headers["anthropic-version"] = "2023-06-01"
        return headers
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    if provider == "openrouter":
        headers.setdefault("HTTP-Referer", "https://github.com/Antman1526/Apollo")
        headers.setdefault("X-OpenRouter-Title", "Apollo")
    return headers
```
(`src/endpoint_resolver.py:173-181, 195-209`) — Anthropic gets `x-api-key` +
`anthropic-version` (never `Authorization: Bearer`); everything else gets Bearer auth;
OpenRouter additionally gets attribution headers via `setdefault` (won't clobber
caller-supplied values).

`resolve_endpoint(setting_prefix, ...)` implements a settings-prefix fallback chain:
`{prefix}_endpoint_id`/`{prefix}_model` → if unset and caller supplied both `fallback_url`
and `fallback_model`, use those immediately (so a background task never jumps to the global
default while the user is mid-conversation with a different model) → `utility` prefix falls
to `default_endpoint_id`/`default_model` when unset → any other prefix falls to `utility_*`,
then `default_*` → DB lookup of the resolved `ModelEndpoint` (filtered `is_enabled==True`,
optionally owner-scoped) → discards a configured `model` if it's since been hidden on the
endpoint, auto-picks `_first_chat_model` of the enabled models otherwise. `_first_chat_model`
skips models matching `_NON_CHAT_MODEL = ("text-embedding", "embedding", "tts-", "whisper",
"dall-e", "moderation", "rerank", "reranker", "clip", "stable-diffusion")` so an
OpenAI-compatible endpoint that lists an embedding model first doesn't get auto-selected as
the chat model.

`ModelEndpoint` (`core/database.py:339-360`) is the persisted row: `id, name, base_url,
api_key (EncryptedText), is_enabled, hidden_models, cached_models, model_type ("llm"|"image"),
supports_tools (nullable bool — auto-detected from `--enable-auto-tool-choice` at Cookbook
register time, togglable in UI), owner (nullable — NULL means shared/legacy visible to
everyone)`.

Fallback-chain resolvers (`resolve_chat_fallback_candidates`,
`resolve_utility_fallback_candidates`, `resolve_vision_fallback_candidates`) each read an
ordered `[{"endpoint_id", "model"}, ...]` list from settings
(`default_model_fallbacks`/`utility_model_fallbacks`/`vision_model_fallbacks`) and resolve
each entry via `resolve_endpoint_by_id`, skipping any that fail to resolve. The primary model
is *not* included in these lists — callers prepend their own current `(url, model, headers)`.

`UNCERTAIN:` `teacher_model`/`teacher_enabled` do **not** go through this resolver at all —
`src/teacher_escalation.py` uses a separate free-text `"model_name"` or
`"model_name@endpoint_name"` spec parser in `src/ai_interaction.py` (`_resolve_model`) that
searches all enabled endpoints for a name match.

---

## 5. Mixture routing — the "Fast Lane" (`services/model_router.py`, 74 lines)

Internally called "mixture routing"; the Settings UI and chat SSE payload label it **"Fast
Lane"** (`routes/model_routes.py:44`: `"light_endpoint_id": ("light_model", "Fast Lane")`;
`routes/chat_routes.py:941`: `_model_info["suffix"] = "Fast lane"`). It is a deterministic,
LLM-free binary classifier — not a general task-complexity router — that decides whether a
chat message is "light" (trivial/conversational) or "heavy", and redirects light messages to
a separately-configured small/fast model. **Chat mode only** — the agent/tool-calling loop
and Deep Research are explicitly excluded, because "the agent loop needs tool-competent
models and is not routed" (module docstring). Opt-in via `mixture_routing_enabled` (default
`False`).

```python
LIGHT_MAX_CHARS = 280

_HEAVY_MARKERS = re.compile(
    r"(?i)\b("
    r"code|write|implement|debug|fix|refactor|analy[sz]e|research|review|"
    r"plan|design|architect|prove|derive|calculate|compute|translate|"
    r"summari[sz]e|compare|explain why|step[- ]by[- ]step|essay|report|"
    r"document|spreadsheet|regex|sql|script|function|error|traceback|"
    r"stack trace"
    r")\b"
)

def classify_message(message: str) -> str:
    """Return "light" or "heavy". Deterministic; conservative toward heavy."""
    msg = (message or "").strip()
    if not msg or len(msg) > LIGHT_MAX_CHARS:
        return "heavy"
    if "```" in msg or "\n\n" in msg:
        return "heavy"
    if _HEAVY_MARKERS.search(msg):
        return "heavy"
    if msg.count("?") > 1:
        return "heavy"
    return "light"

def route_chat(message: str, owner: Optional[str] = None) -> Optional[Tuple[str, str, Dict]]:
    """(url, model, headers) for the light lane, or None to keep the default."""
    try:
        if not get_setting("mixture_routing_enabled", False):
            return None
        if classify_message(message) != "light":
            return None
        from src.endpoint_resolver import resolve_endpoint
        url, model, headers = resolve_endpoint("light", owner=owner)
        if not url or not model:
            return None
        return url, model, headers or {}
    except Exception:
        logger.exception("mixture routing failed (falling back to default)")
        return None
```
(`services/model_router.py`, in full) — "light" requires: non-empty, ≤280 chars, no code
fence or blank line, no heavy-verb match, at most one `?`. A wrong "heavy" call just costs a
few seconds; a wrong "light" call costs answer quality, so the classifier is deliberately
biased toward "heavy."

Caller wiring, `routes/chat_routes.py:924-991`:

```python
_routed_light = None
if chat_mode == "chat" and not do_research:
    try:
        from services.model_router import route_chat
        _routed_light = route_chat(message or "", owner=_user)
    except Exception:
        _routed_light = None

_model_info = {"type": "model_info", "model": _routed_light[1] if _routed_light else sess.model}
if _routed_light:
    _model_info["suffix"] = "Fast lane"
...
if _routed_light:
    _chat_candidates = [_routed_light] + _chat_candidates
    _answered_by = _routed_light[1]
```

The effective fallback chain when Fast Lane fires is: **light model → the session's own
model → the configured `default_model_fallbacks` chain** — a light-lane failure degrades
exactly to old (unrouted) behavior, never to a hard error.

Settings block:

```python
    "mixture_routing_enabled": False,
    "light_endpoint_id": "",
    "light_model": "",
```
(`src/settings.py:44-49`)

---

## 6. Reference Library search/format algorithms (`services/reference_library.py`, 419 lines)

Module purpose, from its docstring: a **fourth store** deliberately separate from memory
(facts about the user), skills (procedures), and documents (the user's own files) — third-
party catalogs the agent consults on demand via `reference_search`, so 1,700 API listings
never dilute memory recall. Covered fully in doc 08 §5 (upstream catalogs / SSRF guard); the
algorithmic pieces are summarized here since they are core business logic.

### 6.1 Fetch

```python
RAW_BASE = "https://raw.githubusercontent.com"
MAX_ENTRIES_PER_SOURCE = 6000
_MAX_FILE_BYTES = 8 * 1024 * 1024

def fetch_markdown(repo: str, ref: str, path: str, *, timeout: int = 30) -> str:
    """Fetch one catalog markdown file through the shared SSRF guard."""
    from src.search.content import _get_public_url
    url = _raw_url(repo, ref, path)
    resp = _get_public_url(url, headers={"User-Agent": "apollo-reference-library"}, timeout=timeout)
    if len(resp.content) > _MAX_FILE_BYTES:
        raise ValueError(f"catalog file too large: {path}")
    return resp.text
```
(`services/reference_library.py:100-109`) — only the specific markdown files that hold each
catalog are fetched (a few hundred KB each), never whole repo tarballs (the comment notes
`developer-roadmap` alone ships tens of MB of site assets that would be pure waste). Reuses
the same SSRF-guarded HTTP path as the skill-pack installer (`src.search.content._get_public_url`,
detailed in doc 08).

### 6.2 Parsers

Four parser functions, dispatched by `_PARSERS = {"api_table": _parse_api_table, "byox":
_parse_byox, "book_list": _parse_book_list, "roadmap": _parse_roadmap}`
(`services/reference_library.py:246-251`), each returning
`[{category, title, url, description, meta}]`:

- `_parse_api_table` (`143-176`) — public-apis's `| [Name](url) | Desc | Auth | HTTPS | CORS |`
  rows under `### Category` headings; extracts `meta.auth/https/cors`.
- `_parse_byox` (`179-194`) — build-your-own-x's `[**Language**: _Title_](url)` list items,
  via `_BYOX_RE = re.compile(r"\[\*\*([^*\]]+)\*\*\s*:\s*_?([^_\]]+?)_?\]\(([^)\s]+)[^)]*\)")`.
- `_parse_book_list` (`197-215`) — free-programming-books's `* [Title](url) - Author (format)`
  under `### Topic`.
- `_parse_roadmap` (`221-243`) — developer-roadmap's flat list of `roadmap.sh` links, scanning
  **every** link on a line (not just the first) because entries pair a main and a beginner
  variant on the same line (`- [Frontend](…) / [Frontend Beginner](…)`), and taking only the
  first would silently drop half the catalog.

### 6.3 Sponsor-row filtering

The public-apis README opens with a sponsor block whose tables look almost like real
entries. Two independent guards, applied together, because "an ad served to the agent as a
'free API' would be a real correctness bug" (source comment):

```python
_SPONSOR_MARKERS = ("utm_campaign=public-apis-repo", "apilayer.com", "run.pstmn.io")

def _parse_api_table(md: str) -> List[Dict[str, Any]]:
    ...
    for line in md.splitlines():
        ...
        m = _LINK_ROW_RE.match(line)
        if not m:
            continue
        title, url = _clean(m.group(1)), m.group(2).strip()
        if not title or not url.startswith("http"):
            continue
        low = line.lower()
        if any(mark in low for mark in _SPONSOR_MARKERS):
            continue
        cols = [c.strip() for c in m.group(3).split("|")]
        if len([c for c in cols if c]) < 3:   # real rows have desc+auth+https+cors
            continue
        ...
```
(`services/reference_library.py:143-176`, condensed) — guard 1 rejects any row whose line
contains a known sponsor-campaign URL fragment; guard 2 rejects rows with fewer than 3
non-empty trailing cells (real entries carry the full API|Description|Auth|HTTPS|CORS shape;
ad rows carry only 3 and always the sponsor tag anyway, so the two checks are redundant-by-
design rather than either being sufficient alone).

### 6.4 Search / ranking / format-for-agent

```python
def search(query: str, *, source=None, kind=None, limit: int = 20) -> List[Dict[str, Any]]:
    """Substring search over title/description/category, newest-relevant first.
    Title matches rank above description matches so "weather" surfaces the
    Weather API rather than everything that merely mentions weather."""
    ...
    like = f"%{q}%"
    rows = db.query(ReferenceEntry).filter(
        ReferenceEntry.title.ilike(like)
        | ReferenceEntry.description.ilike(like)
        | ReferenceEntry.category.ilike(like)
    )
    if source: rows = rows.filter(ReferenceEntry.source == source)
    if kind: rows = rows.filter(ReferenceEntry.kind == kind)
    found = rows.limit(max(1, min(limit, 100)) * 3).all()

    def rank(r) -> int:
        title = (r.title or "").lower()
        if title == ql: return 0
        if title.startswith(ql): return 1
        if ql in title: return 2
        if ql in (r.category or "").lower(): return 3
        return 4

    found.sort(key=rank)
    return [...][: max(1, min(limit, 100))]
```
(`services/reference_library.py:349-399`, condensed) — SQL `ILIKE` substring match across
three columns (no vector/embedding search here, unlike memory), over-fetched 3× the requested
limit then re-ranked client-side by a 5-tier exactness score (exact title → title-prefix →
title-contains → category-contains → description-only), then truncated to the actual limit.

`format_for_agent(results, query)` (`402-419`) renders hits as compact plain text for the
tool result — title, `[category]`, truncated description (180 chars), URL, and (for `kind ==
"api"` entries) an `auth: ... https: ... cors: ...` line pulled from `meta`, which is exactly
the data the agent needs to decide whether it can call the API directly without further
lookup. Empty results return a pointer to install catalogs first: *"Catalogs may not be
installed yet (Settings → AI → Reference Library)."*

Storage dedup happens at install time, not search time — `parse_source` (`254-271`) dedupes
by URL within a source (`deduped.setdefault(e["url"], e)`) since upstream lists often repeat
popular links, and caps each source to `MAX_ENTRIES_PER_SOURCE = 6000` with a logged warning
if a malformed upstream file balloons past it.
