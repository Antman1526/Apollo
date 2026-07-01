# Second-Brain Over Your Chats — Design Spec

**Date:** 2026-06-30
**Status:** Design (authored autonomously during a self-paced build loop — flagged decisions marked ⚠ for user review)
**Branch:** `feature/skill-pack-installer` worktree (Phase 3 of the skills/features roadmap)

## 1. Summary

Turn Apollo's conversation history into a **self-maintaining second brain**: distill
chats into atomic, owner-scoped **memories** that are automatically embedded into
the existing ChromaDB RAG index, so the agent recalls facts from past
conversations. Plus a one-time **importer** for external ChatGPT/Claude chat-export
archives, so a user's entire LLM history becomes searchable memory.

This is an **orchestration over existing Apollo systems** — Memory store, the
ChromaDB `apollo_memories` collection, the cron task scheduler, `llm_call`, and
uploads. Very little new infrastructure.

## 2. Why memories (not notes), and what's new

The grounding confirmed two stores: **Notes** (Google-Keep-style, user-facing
todos/checklists) and **Memory** (owner-scoped durable facts that auto-sync to the
vector index and are recalled by the agent — `routes/memory_routes.py:69`,
`services/memory/memory_vector.py:65`). Distilled chat facts belong in **Memory**:
they're semantic knowledge for retrieval, not checklists. Each distilled memory
carries `source="agent"` (or `"import"`) and `session_id` linking back to its
source conversation.

**Already exists (reuse):** chat enumeration + history
(`core/session_manager.py:334,583`), `MemoryManager.add_entry` +
`find_duplicates` (`services/memory/memory.py`), `memory_vector.add/search`,
`ScheduledTask` + `compute_next_run` (`src/task_scheduler.py`), `llm_call`
(`src/llm_core.py:805`), upload (`routes/upload_routes.py:17`).

**Genuinely new:** (a) a distiller that turns a transcript into atomic facts,
(b) a "distill a session → memories" action + optional scheduled sweep,
(c) a ChatGPT/Claude export parser → memories.

## 3. Goals / non-goals

### Goals
- **Distill on demand:** a "Distill to memory" action on a chat session → extract
  atomic facts → write as deduped, vector-indexed memories linked to the session.
- **Scheduled sweep (optional, thin):** a recurring job distills recently-active
  sessions not yet distilled, so the brain maintains itself.
- **Import external history:** upload a ChatGPT (`conversations.json`) or Claude
  export → extract facts → memories tagged `source="import"`.
- **Traceability:** every distilled/imported memory keeps a `session_id` (or an
  import provenance tag) so a fact can be traced to its origin.
- **Dedup:** reuse `find_duplicates` so re-distilling a session doesn't pile up
  duplicate facts.
- **Fully local:** distillation uses the user's configured model via `llm_call`;
  embeddings are the existing local FastEmbed path. Nothing leaves the machine.

### Non-goals (deferred — ⚠ decisions for user review)
- **⚠ Knowledge-graph / linked-notes visualization.** This is the only net-new UI
  and the highest-cost item. v1 uses the existing `session_id` backlink for
  traceability and the existing memory browser for viewing; a graph panel
  (edges, D3/Cytoscape) is a separate follow-on. *If the user specifically wants
  the graph "wow," it becomes its own phase.*
- Auto-linking memories to each other (backlink graph) beyond the source-session
  link.
- Real-time distillation on every message (too costly); distillation is
  on-demand or scheduled.
- Editing/merging the distilled memory set with an LLM ("brain tidy/consolidate")
  — a natural follow-on using the same scheduler, deferred from v1.

## 4. Architecture

### New components
1. **`services/memory/distiller.py`** (new) — the distillation logic:
   - `build_distill_prompt(transcript) -> messages` — pure; constructs the
     system+user messages instructing extraction of atomic, self-contained facts
     (one fact per line, no chit-chat, no first-person "the user asked").
   - `parse_facts(llm_text) -> list[str]` — pure; splits the model output into
     clean fact strings (handles bullet/numbered/JSON-ish output, drops empties).
   - `distill_transcript(transcript, llm_caller) -> list[str]` — pure; wires the
     two with an **injected** `llm_caller(messages)->str` so it's unit-testable
     with a fake. No network/DB in this module.
2. **`services/memory/chat_import.py`** (new) — export parsers:
   - `parse_chatgpt_export(json_obj) -> list[Conversation]` and
     `parse_claude_export(json_obj) -> list[Conversation]`, each yielding
     `{title, messages:[{role,text}]}`. Pure; format-detection + traversal
     (ChatGPT's `mapping` tree, Claude's flat items). Unit-tested with fixtures.
3. **`services/memory/brain.py`** (new) — the orchestrator (DB/side-effecting):
   - `distill_session(session_id, owner, resolve_model, memory_manager,
     memory_vector) -> result` — load messages, build transcript, call `llm_call`,
     parse facts, dedup via `find_duplicates`, write each as a memory
     (`source="agent"`, `session_id`), `memory_vector.add`. Returns counts.
   - `import_export(conversations, owner, ...) -> result` — same write path with
     `source="import"`.
4. **Routes** in `routes/memory_routes.py` (extend): `POST /api/memory/distill-session`
   `{session_id}` and `POST /api/memory/import-chat-export` `{upload_id}` (reads the
   uploaded export file), owner-gated like the existing memory routes.
5. **Scheduled sweep (thin):** register a `ScheduledTask` (or reuse the existing
   task loop) that periodically calls `distill_session` for sessions with
   `last_message_at` newer than their last distill. Tracked via a per-session
   marker (a memory-manager sidecar or a `distilled_at` note in session meta).
6. **Frontend:** a "Distill to memory" button on a session (chat header/context
   menu) and an "Import chat export" action in the memory modal
   (`static/js/memory.js`), mirroring existing memory fetch patterns.

### Reused (unchanged)
`llm_call` (distill), `MemoryManager` + `memory_vector` (write+index),
`session_manager` (read chats), `ScheduledTask`/scheduler (sweep), upload
(receive export files), the memory browser UI (view results).

## 5. Data flow

```
Distill a session:
  session_id → session_manager loads messages → build transcript
    → llm_call(resolved model, distill prompt) → parse_facts
    → for each fact not find_duplicates(): memory_manager.add_entry(source=agent,
      session_id) + memory_vector.add(id, fact)
    → facts are now RAG-recalled by the agent

Import external history:
  upload ChatGPT/Claude export → parse_(chatgpt|claude)_export → conversations
    → (optionally distill each, or store messages) → memories (source=import)

Scheduled sweep:
  cron tick → sessions with new activity since last distill → distill_session each
```

## 6. Error handling & guards

- **Model unavailable / llm_call fails:** the distill action returns a clear
  error; no partial/garbage memories written (parse only on a successful call).
- **Empty/again:** dedup ensures re-distilling a session is idempotent-ish (no
  duplicate facts); a session with no distillable content returns "0 facts".
- **Malformed export:** the parser reports unrecognized format rather than
  raising; partial parse of a large export skips bad conversations.
- **Cost control:** distillation is on-demand or scheduled (never per-message);
  the scheduled sweep is opt-in and rate-bounded (N sessions per tick).
- **Privacy:** everything is local (local model via `llm_call` to the configured
  endpoint, local embeddings); imported archives are the user's own data.
- **Owner scoping:** all writes carry `owner`; memories are per-user like today.

## 7. Testing

- **`distiller.py`** (pure): `build_distill_prompt` includes the transcript;
  `parse_facts` handles bullet/numbered/blank/`- `/`1.`-prefixed lines and dedupes
  whitespace; `distill_transcript` with a fake `llm_caller` returns the parsed
  facts. No network.
- **`chat_import.py`** (pure): fixtures for a minimal ChatGPT `conversations.json`
  (mapping tree) and a Claude export → assert conversations + messages extracted
  in order; unrecognized JSON → empty/reported, not raised.
- **`brain.py`** (orchestrator): inject fake `memory_manager`/`memory_vector`/
  `llm` and a small in-memory session; assert facts are deduped and written with
  `source`/`session_id`, and `memory_vector.add` called per new fact.
- **Routes:** `distill-session` and `import-chat-export` happy-path with the
  orchestrator mocked; owner gating.
- Real end-to-end (a real session distilled by a real local model) verified
  manually, like the installer's Task 8.

## 8. Implementation notes

- Keep `distiller.py` and `chat_import.py` pure (injected `llm_caller`, no DB) so
  they're unit-testable; confine DB/vector side effects to `brain.py`.
- Reuse `find_duplicates` for dedup rather than inventing a similarity check.
- The scheduled sweep should reuse the existing task loop; if wiring a new task
  type is heavy, ship the on-demand distill first and add the sweep as a thin
  follow-up (v1 can be manual-only).
- ⚠ Graph UI intentionally omitted; revisit as its own phase if desired.
