# Second-Brain Over Chats — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Distill Apollo chat sessions into deduped, vector-indexed memories (recalled by the agent via existing RAG), plus import external ChatGPT/Claude export archives as memories.

**Architecture:** Pure, unit-tested logic (`distiller.py` = transcript→facts via an injected LLM caller; `chat_import.py` = export JSON→conversations) plus a side-effecting orchestrator (`brain.py`) that reuses `MemoryManager`, `memory_vector`, `session_manager`, and `llm_call`. Thin routes + UI. No new infra.

**Tech Stack:** Python 3.11 / FastAPI, `pytest`. Worktree `/Users/Antman/Apollo-skills-wt`; tests: `/Users/Antman/Apollo/venv/bin/python -m pytest <path> -q`.

**Spec:** `docs/superpowers/specs/2026-06-30-second-brain-design.md`

**Reused signatures (verified):**
- `src/llm_core.py`: `llm_call(url, model, messages, temperature=1.0, max_tokens=0) -> str`
- `services/memory/memory.py`: `MemoryManager.add_entry(text, source="user", category="fact", owner=None) -> dict`; `.load(owner)`, `.load_all()`, `.save(list)`, `.find_duplicates(text, entries) -> bool/list`
- `services/memory/memory_vector.py`: `MemoryVectorStore.add(memory_id, text)`, `.healthy`
- `core/session_manager.py`: `get_session(session_id) -> Session` (with `.history` messages: `{role, content}`)

---

## Task 1: Distiller (pure)

`build_distill_prompt(transcript)`, `parse_facts(llm_text)`, and
`distill_transcript(transcript, llm_caller)` — turn a chat transcript into a list
of atomic fact strings. No network/DB (LLM injected).

**Files:** Create `services/memory/distiller.py`; Test `tests/test_distiller.py`

- [ ] **Step 1: Failing test**

```python
from services.memory.distiller import build_distill_prompt, parse_facts, distill_transcript


def test_build_prompt_includes_transcript():
    msgs = build_distill_prompt("USER: I use Postgres 16.\nASSISTANT: Noted.")
    assert msgs[0]["role"] == "system"
    assert "Postgres 16" in msgs[-1]["content"]


def test_parse_facts_handles_bullets_numbers_blanks():
    text = "- User uses Postgres 16\n1. Prefers dark mode\n\n  \n* Lives in Berlin\nplain fact"
    facts = parse_facts(text)
    assert "User uses Postgres 16" in facts
    assert "Prefers dark mode" in facts
    assert "Lives in Berlin" in facts
    assert "plain fact" in facts
    assert "" not in facts and all(f == f.strip() for f in facts)


def test_parse_facts_drops_none_marker():
    assert parse_facts("NONE") == []
    assert parse_facts("(no durable facts)") == []


def test_distill_transcript_uses_injected_llm():
    calls = {}
    def fake_llm(messages):
        calls["msgs"] = messages
        return "- Fact A\n- Fact B"
    facts = distill_transcript("USER: hi", fake_llm)
    assert facts == ["Fact A", "Fact B"]
    assert "hi" in calls["msgs"][-1]["content"]
```

- [ ] **Step 2: Run → fails** (`ModuleNotFoundError`).

- [ ] **Step 3: Implement**

```python
# services/memory/distiller.py
"""Distill a chat transcript into atomic, durable facts (memories).

Pure: the LLM is injected as `llm_caller(messages) -> str`, so this is
unit-testable without a model or DB.
"""
import re

_SYSTEM = (
    "You extract durable, atomic facts from a conversation to store in a personal "
    "knowledge base. Output ONE fact per line, each a short standalone statement "
    "(no first-person, no 'the user asked'). Capture preferences, decisions, "
    "identity, projects, and stable facts. Skip chit-chat, transient context, and "
    "anything not worth remembering later. If there is nothing durable, output NONE."
)

_SKIP = {"none", "(none)", "n/a", "(no durable facts)", "no durable facts"}


def build_distill_prompt(transcript: str) -> list:
    return [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": f"Conversation:\n\n{transcript}\n\nDurable facts:"},
    ]


def parse_facts(llm_text: str) -> list:
    out = []
    for line in (llm_text or "").splitlines():
        s = line.strip()
        s = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", s)  # strip bullet/number markers
        s = s.strip()
        if not s or s.lower() in _SKIP:
            continue
        out.append(s)
    return out


def distill_transcript(transcript: str, llm_caller) -> list:
    if not (transcript or "").strip():
        return []
    text = llm_caller(build_distill_prompt(transcript))
    return parse_facts(text)
```

- [ ] **Step 4: Run → passes.**
- [ ] **Step 5: Commit** `feat(brain): pure chat-transcript distiller`

---

## Task 2: Chat-export parsers (pure)

`parse_chatgpt_export(obj)` and `parse_claude_export(obj)` → list of
`{"title": str, "messages": [{"role","text"}]}`. Format detection; never raise on
a bad conversation.

**Files:** Create `services/memory/chat_import.py`; Test `tests/test_chat_import.py`

- [ ] **Step 1: Failing test**

```python
from services.memory.chat_import import parse_chatgpt_export, parse_claude_export, parse_export


def test_parse_chatgpt_mapping_tree():
    obj = [{
        "title": "DB choices",
        "mapping": {
            "a": {"message": {"author": {"role": "user"}, "content": {"parts": ["Use Postgres?"]}}},
            "b": {"message": {"author": {"role": "assistant"}, "content": {"parts": ["Yes."]}}},
        },
    }]
    convos = parse_chatgpt_export(obj)
    assert len(convos) == 1
    assert convos[0]["title"] == "DB choices"
    roles = [m["role"] for m in convos[0]["messages"]]
    assert "user" in roles and "assistant" in roles
    assert any("Postgres" in m["text"] for m in convos[0]["messages"])


def test_parse_claude_flat_items():
    obj = {"conversations": [{
        "name": "Trip",
        "chat_messages": [
            {"sender": "human", "text": "Book Berlin"},
            {"sender": "assistant", "text": "Done"},
        ],
    }]}
    convos = parse_claude_export(obj)
    assert convos[0]["title"] == "Trip"
    assert convos[0]["messages"][0]["role"] == "user"
    assert "Berlin" in convos[0]["messages"][0]["text"]


def test_parse_export_autodetects_and_tolerates_garbage():
    assert parse_export({"nonsense": 1}) == []          # unknown → empty, no raise
```

- [ ] **Step 2: Run → fails.**

- [ ] **Step 3: Implement** (handle ChatGPT `mapping` dict + `content.parts`; Claude `conversations[].chat_messages[]` with `sender` human→user; `parse_export` sniffs which). Return `[]` on unrecognized shapes; wrap per-conversation parsing in try/except. Normalize `sender`/`author.role`: `human`→`user`. Skip empty-text messages.

- [ ] **Step 4: Run → passes.**
- [ ] **Step 5: Commit** `feat(brain): ChatGPT/Claude export parsers`

---

## Task 3: Brain orchestrator (injected deps)

`distill_session(...)` and `import_conversations(...)` — load/transcribe, distill,
dedup, write memories + vector index. All collaborators injected so it's testable
without DB.

**Files:** Create `services/memory/brain.py`; Test `tests/test_brain.py`

- [ ] **Step 1: Failing test** (inject fakes)

```python
from services.memory.brain import distill_and_store


def test_distill_and_store_dedups_and_indexes():
    added, indexed = [], []
    fake_mm = type("MM", (), {
        "load": lambda self, owner=None: [{"text": "User uses Postgres 16"}],
        "load_all": lambda self: [{"text": "User uses Postgres 16"}],
        "find_duplicates": lambda self, text, entries: any(e["text"] == text for e in entries),
        "add_entry": lambda self, text, source="user", category="fact", owner=None: added.append(text) or {"id": f"id{len(added)}", "text": text},
        "save": lambda self, allm: None,
    })()
    fake_vec = type("V", (), {"healthy": True, "add": lambda self, mid, text: indexed.append((mid, text))})()

    facts = ["User uses Postgres 16", "Prefers dark mode"]  # first is a dup
    res = distill_and_store(facts, owner="me", source="agent", session_id="s1",
                            memory_manager=fake_mm, memory_vector=fake_vec)
    assert added == ["Prefers dark mode"]        # dup skipped
    assert indexed == [("id1", "Prefers dark mode")]
    assert res["added"] == 1 and res["skipped"] == 1
```

- [ ] **Step 2: Run → fails.**

- [ ] **Step 3: Implement** `distill_and_store(facts, owner, source, session_id, memory_manager, memory_vector)`: load owner memories once; for each fact, skip if `find_duplicates`; else `add_entry(text=fact, source=source, category="fact", owner=owner)`, attach `session_id`, append to all, `memory_vector.add(id, fact)` when `healthy`; `save` once at end; return `{"added","skipped"}`. Also add thin `distill_session(session_id, owner, resolve_model, memory_manager, memory_vector, llm=llm_call)` that loads the session via `session_manager`, builds a transcript (`ROLE: content` lines), calls `distill_transcript(transcript, lambda m: llm(url, model, m))`, then `distill_and_store(..., source="agent", session_id=...)`. Keep the session-loading in a small wrapper so the pure `distill_and_store` stays DB-free (as tested).

- [ ] **Step 4: Run → passes.**
- [ ] **Step 5: Commit** `feat(brain): distill+store orchestrator with dedup and indexing`

---

## Task 4: Routes

`POST /api/memory/distill-session {session_id}` and
`POST /api/memory/import-chat-export {upload_id}` in `routes/memory_routes.py`,
owner-gated like existing memory routes.

**Files:** Modify `routes/memory_routes.py`; Test `tests/test_brain_routes.py`

- [ ] **Step 1** `grep -n "_owner\|memory_manager\|memory_vector\|def setup_memory_routes\|resolve_endpoint\|endpoint_resolver" routes/memory_routes.py` to confirm what's in scope (owner helper, the manager/vector handles, how a model endpoint is resolved for `llm_call`).
- [ ] **Step 2** Add the two routes calling `brain.distill_session` / `brain.import_conversations`. For distill, resolve the session's own `endpoint_url`/`model` (from the Session row) for `llm_call`; for import, read the uploaded export via the upload handler, `json.load`, `parse_export`, then distill or store each conversation.
- [ ] **Step 3** Test with `brain` mocked: assert routes call the orchestrator with the right args and are owner-gated (bypass auth as existing memory-route tests do).
- [ ] **Step 4** `import routes.memory_routes` clean; run route tests → pass.
- [ ] **Step 5** Commit `feat(brain): distill-session and import-chat-export routes`

---

## Task 5: Frontend

- [ ] Add a "Distill to memory" action on a chat session (chat header/context menu) → `POST /api/memory/distill-session {session_id}` → toast added/skipped counts. Add an "Import chat export" action in the memory modal (`static/js/memory.js`) → file upload → `POST /api/memory/import-chat-export {upload_id}`. Mirror existing memory fetch patterns. `grep -n` for the memory modal + an existing `fetch('/api/memory...` call to match style.
- [ ] Commit `feat(brain): UI for distill-to-memory and chat-export import`

---

## Task 6: End-to-end verification (manual)

- [ ] Launch worktree app on port 7862. Have a real chat, click "Distill to memory" → confirm atomic memories appear in the memory browser with `source=agent` and are returned by memory search. Import a small ChatGPT export → confirm memories created with `source=import`. Confirm the agent recalls a distilled fact in a later chat.

---

## Self-Review

**Spec coverage:** distill on-demand (Tasks 1,3,4) · export import (Tasks 2,3,4) · dedup via find_duplicates (Task 3) · vector indexing (Task 3) · source/session_id provenance (Task 3) · local-only (reuses llm_call + local embeddings) · routes owner-gated (Task 4) · UI (Task 5). Scheduled sweep = deferred to a thin follow-up per spec (v1 manual). Graph UI = deferred per spec (⚠). 
**Placeholders:** pure tasks (1-3) carry full code; routes/UI (4-5) carry endpoints + grep anchors for the two live-wiring lookups. 
**Names:** `build_distill_prompt`/`parse_facts`/`distill_transcript`, `parse_chatgpt_export`/`parse_claude_export`/`parse_export`, `distill_and_store`/`distill_session`/`import_conversations` consistent across tasks/tests.
