"""Second-brain orchestrator: turn distilled facts into stored, deduped,
vector-indexed memories.

`distill_and_store` is PURE with respect to infrastructure: all collaborators
(`memory_manager`, `memory_vector`) are injected, so it is unit-testable without
a DB, model, or network.

The thin `distill_session` wrapper is the only side-effecting entry point — it
lazily imports the session manager and LLM caller so this module stays
import-clean and DB-free.
"""
import logging
from typing import Any, Callable, Dict, List, Optional

from .distiller import distill_transcript
from src.observability import report_exception

logger = logging.getLogger(__name__)


def distill_and_store(
    facts: List[str],
    owner: Optional[str],
    source: str,
    session_id: Optional[str],
    memory_manager: Any,
    memory_vector: Any,
) -> Dict[str, int]:
    """Store `facts` as memories, skipping duplicates and indexing new ones.

    - Loads the owner's existing memories once.
    - For each fact: skip if `find_duplicates` reports a match (truthy result —
      the real `MemoryManager` returns a `List[Dict]`, a fake may return a bool).
    - Otherwise create an entry via `add_entry`, tag it with `session_id`,
      append it to the working list, and index it in the vector store when the
      store is healthy.
    - Persists the full list once at the end.

    Returns `{"added": int, "skipped": int}`.
    """
    added = 0
    skipped = 0

    # Load existing memories once. Dedup + save operate on this working list so
    # intra-batch duplicates are also caught.
    entries: List[Dict] = list(memory_manager.load(owner) or [])
    dirty = False

    for fact in facts:
        text = (fact or "").strip()
        if not text:
            continue

        # Truthy => duplicate (handles both bool and non-empty list returns).
        if memory_manager.find_duplicates(text, entries):
            skipped += 1
            continue

        entry = memory_manager.add_entry(
            text=text, source=source, category="fact", owner=owner
        )
        if session_id is not None and isinstance(entry, dict):
            entry["session_id"] = session_id

        entries.append(entry)
        dirty = True
        added += 1

        if getattr(memory_vector, "healthy", False):
            mem_id = entry.get("id") if isinstance(entry, dict) else None
            if mem_id:
                try:
                    memory_vector.add(mem_id, text)
                except Exception as error:
                    # Indexing is best-effort; storage already succeeded.
                    report_exception(logger, "memory_brain_vector_index_failed", error, outcome="best_effort", context={"memory_id": mem_id})

    if dirty:
        memory_manager.save(entries)

    return {"added": added, "skipped": skipped}


def import_conversations(
    conversations: List[Dict],
    owner: Optional[str],
    memory_manager: Any,
    memory_vector: Any,
    llm_caller: Callable[[List[Dict]], str],
    source: str = "import",
) -> Dict[str, int]:
    """Distill each imported conversation into facts and store them.

    Pure w.r.t. infrastructure: `llm_caller(messages) -> str` and the memory
    collaborators are injected. Aggregates the per-conversation counts.
    """
    totals = {"added": 0, "skipped": 0, "conversations": 0}
    for convo in conversations or []:
        if not isinstance(convo, dict):
            continue
        transcript = _transcript_from_messages(convo.get("messages") or [])
        if not transcript.strip():
            continue
        facts = distill_transcript(transcript, llm_caller)
        res = distill_and_store(
            facts,
            owner=owner,
            source=source,
            session_id=None,
            memory_manager=memory_manager,
            memory_vector=memory_vector,
        )
        totals["added"] += res["added"]
        totals["skipped"] += res["skipped"]
        totals["conversations"] += 1
    return totals


def _transcript_from_messages(messages: List[Dict]) -> str:
    """Render a list of {role/sender, content/text} messages as ROLE: text lines."""
    lines: List[str] = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role") or msg.get("sender") or "user"
        content = msg.get("content")
        if content is None:
            content = msg.get("text", "")
        if isinstance(content, list):
            content = " ".join(str(p) for p in content)
        content = str(content).strip()
        if not content:
            continue
        lines.append(f"{str(role).upper()}: {content}")
    return "\n".join(lines)


def distill_session(
    session_id: str,
    owner: Optional[str],
    memory_manager: Any,
    memory_vector: Any,
    llm: Optional[Callable] = None,
) -> Dict[str, int]:
    """Thin, side-effecting wrapper: load a session, distill it, store the facts.

    NOT unit-tested (the pure `distill_and_store` carries the tested logic). The
    session manager and LLM caller are imported lazily so this module stays
    import-clean and DB-free.
    """
    from core.session_manager import SessionManager  # lazy: avoid DB at import
    if llm is None:
        from src.llm_core import llm_call as llm  # lazy: avoid heavy import

    manager = SessionManager()
    session = manager.get_session(session_id)
    if session is None:
        return {"added": 0, "skipped": 0}

    history = getattr(session, "history", None) or []
    messages = [
        {"role": getattr(m, "role", None), "content": getattr(m, "content", None)}
        for m in history
    ]
    transcript = _transcript_from_messages(messages)
    if not transcript.strip():
        return {"added": 0, "skipped": 0}

    url = getattr(session, "endpoint_url", None)
    model = getattr(session, "model", None)
    facts = distill_transcript(transcript, lambda msgs: llm(url, model, msgs))

    return distill_and_store(
        facts,
        owner=owner,
        source="agent",
        session_id=session_id,
        memory_manager=memory_manager,
        memory_vector=memory_vector,
    )
