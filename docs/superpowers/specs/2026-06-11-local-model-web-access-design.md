# Internet Access for Local Models — Design

**Date:** 2026-06-11
**Status:** Approved
**Owner:** Antman / Claude

## Goal

Local GGUF models can search and read the web automatically during normal chat,
with Deep Research, agent web tools, and crawl4ai working end to end — strictly
local-first: queries only ever go to the user's own SearXNG instance, with
DuckDuckGo as an immediate fallback. No third-party search API keys.

## Decisions (user-confirmed)

- **Primary provider:** self-hosted SearXNG, run **without Docker** as an
  Apollo-managed sidecar (same pattern as the Paperclip sidecar).
- **Fallback:** DuckDuckGo, immediate (no timeout penalty when SearXNG is
  absent or down).
- **Chat UX:** auto-search-when-needed via a hybrid decider (heuristic +
  optional utility-model tie-breaker).
- **Scope:** chat web search + fetch, Deep Research enabled, agent web tools
  verified, crawl4ai installed.
- **Privacy:** strict local-first; no keyed providers.

## 1. SearXNG sidecar (no Docker)

New `services/searxng/` manager modeled on `services/paperclip/runtime.py`:

- **Setup script** `scripts/setup-searxng.sh`: creates a dedicated venv,
  installs SearXNG from its git repo, generates a minimal `settings.yml`
  (secret key, JSON output format enabled, rate limiter off — localhost only).
- **Runtime manager** `services/searxng/runtime.py`: starts SearXNG on
  `127.0.0.1:<port>` at Apollo boot when installed and enabled, health-checks,
  restarts on crash, stops on shutdown. Settings flag `searxng_managed: true`.
- **Settings UI:** Settings → Search shows sidecar status (running / not
  installed / failed) with an install button that runs the setup script.
- **Wiring:** `search_provider` stays `searxng`; `search_url` auto-points to
  the sidecar.

### DuckDuckGo immediate fallback

`search_fallback_chain: ["duckduckgo"]` is verified/pinned (not assumed).
DDG takes over in three situations:

1. **Sidecar not installed/running** — the search service consults the sidecar
   manager status first and skips straight to DDG with no timeout penalty.
2. **SearXNG request fails/times out** — short connect timeout (~2s) on the
   localhost call, then immediate DDG retry of the same query in-request.
3. **SearXNG returns zero results** — fall through to DDG before giving up.

A log line + small UI badge ("via DuckDuckGo") indicates fallback was used.
Verify the provider chain in `services/search/core.py` short-circuits fast
when SearXNG is unreachable; tighten the timeout if it doesn't.

## 2. Auto-search in normal chat

New tri-state setting `web_access_mode`: `manual` (current behavior) /
`auto` (new default once enabled) / `always`.

In `auto`, a decider (`src/web_decider.py`) runs before each chat message:

1. **Heuristic pass (instant):** recency words ("latest", "today", "news",
   "price", "score", dates), explicit asks ("search", "look up"), URLs in the
   message → yes. Clearly-no list (pasted-code questions, pure math, creative
   writing) → no.
2. **Tie-breaker (ambiguous only):** one short yes/no completion from
   `utility_model` *only if* it lives on a separate always-on endpoint. The
   decider must never trigger a local-model swap (guard: skip the utility call
   if it would target the llama.cpp slot). No utility model → heuristic's lean
   decides.

On "yes," the existing pre-search path (`src/chat_processor.py` `use_web`
branch) runs `comprehensive_web_search` and injects results as untrusted
context — so the model answers with sources even without tool-calling.
Capable models additionally keep `web_search`/`web_fetch` tools.

**UI:** the web toggle becomes a persistent three-way control (off / auto /
always). A "searched the web" indicator with sources appears when auto-search
fired.

## 3. Scope enablement & verification

- Enable the `deep_research` feature flag; verify a full research run
  (search → crawl → cited report) against a local model.
- Install `crawl4ai` into Apollo's venv (adapter already exists at
  `services/research/crawl4ai_adapter.py`).
- Verify agent mode's `web_search` / `web_fetch` / browser tools end to end
  with a local model.

## 4. Privacy & error handling

- SearXNG binds to localhost only; existing SSRF guards
  (`src/search/content.py`) remain in force for `web_fetch`.
- No keyed providers configured; Brave/Tavily settings UI untouched but unused.
- Failure order: SearXNG (localhost, ~2s budget) → DuckDuckGo (immediate,
  same request) → clear in-chat "web search unavailable" notice. Day one,
  before the sidecar is installed, everything works through DDG; once SearXNG
  is up it becomes primary automatically.

## 5. Testing

- Unit tests: decider heuristics (yes/no/ambiguous fixtures); sidecar manager
  (start/health/fallback); fallback chain short-circuit when SearXNG is down.
- Integration test: chat with `web_access_mode=auto` injects search context on
  a recency question and skips it on a pure-code question.
- Manual verification checklist (real network, run once with user): deep
  research run, agent tools, crawl4ai extraction.
