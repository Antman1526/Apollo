"""
context_compactor.py

Auto-compacts conversation history when approaching context window limits.
Summarizes older messages via the same LLM, preserving key context.
"""

import logging
import time
from typing import Any, Dict, List, Optional

from src.model_context import get_context_length, estimate_tokens
from src.llm_core import llm_call_async
from src.endpoint_resolver import resolve_endpoint
from core.models import ChatMessage

logger = logging.getLogger(__name__)

COMPACT_THRESHOLD = 0.85  # Trigger compaction at 85% of context window
COMPACT_CHUNK_TOKENS = 6000  # Max history per summary call (see maybe_compact)
SUMMARY_MAX_TOKENS = 1024
SMALL_CONTEXT_LIMIT = 8192  # Models with context <= this get aggressive trimming

# Cursor-style self-summarization prompt — produces structured, dense summaries
SELF_SUMMARY_SYSTEM_PROMPT = """You are summarizing a conversation to preserve context after compaction. Produce a structured summary that lets the conversation continue seamlessly.

Use this format:

## Conversation Summary
**Turns summarized:** {count}  |  **Compactions so far:** {n}

### User Goal
One sentence describing what the user is trying to accomplish.

### What Was Done
- Bullet points of completed actions, decisions made, and key outputs
- Include specific file paths, function names, variable names, URLs, and config values
- Note any errors encountered and how they were resolved

### Current State
What is the system/code/task state right now? What was the last thing discussed?

### Pending / Next Steps
- What remains to be done
- Any open questions or blockers

### Key Context
- Important constraints, preferences, or decisions that must not be forgotten
- Specific values: model names, ports, paths, credentials references, versions

Keep the summary under 1000 tokens. Be dense — every token should carry information. Do not include pleasantries or meta-commentary."""


def _sanitize_tool_messages(msgs: List[Dict]) -> List[Dict]:
    """Drop orphaned `tool` messages and dangling assistant `tool_calls`.

    OpenAI's API requires every `role:"tool"` message to immediately
    follow an assistant message that carries `tool_calls` (or another
    tool message in the same batch). Front-trimming the history can cut
    the assistant `tool_calls` parent while keeping its tool responses,
    which triggers: "messages with role 'tool' must be a response to a
    preceding message with 'tool_calls'". This pass repairs that:
      - drops `tool` messages with no valid preceding tool_calls
      - drops assistant `tool_calls` messages whose tool responses were
        all trimmed away (some providers reject unanswered tool_calls)
    """
    # Pass 1: drop orphan tool messages.
    cleaned: List[Dict] = []
    in_batch = False  # are we right after an assistant tool_calls (or mid-batch)?
    for m in msgs:
        role = m.get("role")
        if role == "tool":
            if in_batch:
                cleaned.append(m)
            # else: orphan — drop
            continue
        if role == "assistant" and m.get("tool_calls"):
            in_batch = True
        else:
            in_batch = False
        cleaned.append(m)

    # Pass 2: drop assistant tool_calls messages that have NO following
    # tool response (dangling) — walk backwards so we know what follows.
    out: List[Dict] = []
    for i, m in enumerate(cleaned):
        if m.get("role") == "assistant" and m.get("tool_calls"):
            nxt = cleaned[i + 1] if i + 1 < len(cleaned) else None
            if not (nxt and nxt.get("role") == "tool"):
                # Dangling tool_calls — keep the message but strip the
                # tool_calls so it's a plain assistant turn (preserves any
                # text content the model produced alongside the calls).
                m = {k: v for k, v in m.items() if k != "tool_calls"}
                if not (m.get("content") or "").strip():
                    continue  # nothing left worth keeping
        out.append(m)
    return out


def _message_text_token_estimate(text: str) -> int:
    return int(len(text) * 0.3) + 4


def _truncate_text_to_token_budget(text: str, token_budget: int) -> str:
    """Trim a too-large current user message instead of dropping it entirely."""
    if token_budget <= 32:
        return "[Current user message omitted: it exceeded the model context window.]"

    # Match src.model_context.estimate_tokens' rough chars * 0.3 estimate.
    max_chars = max(200, int((token_budget - 16) / 0.3))
    if len(text) <= max_chars:
        return text

    notice = (
        "\n\n[Notice: the pasted message was too large for this model's context "
        "window, so Apollo kept the beginning and end.]"
    )
    keep_chars = max(200, max_chars - len(notice))
    head_len = max(100, int(keep_chars * 0.7))
    tail_len = max(80, keep_chars - head_len)
    return text[:head_len].rstrip() + notice + "\n\n" + text[-tail_len:].lstrip()


def _truncate_message_to_token_budget(msg: Dict[str, Any], token_budget: int) -> Dict[str, Any]:
    """Return a copy of msg whose text content fits inside token_budget."""
    out = dict(msg)
    content = out.get("content", "")
    if isinstance(content, str):
        out["content"] = _truncate_text_to_token_budget(content, token_budget)
        return out

    if isinstance(content, list):
        remaining = token_budget
        new_content = []
        for item in content:
            if not isinstance(item, dict) or item.get("type") != "text":
                new_content.append(item)
                continue
            text = item.get("text", "")
            truncated = _truncate_text_to_token_budget(text, remaining)
            cloned = dict(item)
            cloned["text"] = truncated
            new_content.append(cloned)
            remaining -= _message_text_token_estimate(truncated)
        out["content"] = new_content
    return out


def current_message_shortened(before: List[Dict], after: List[Dict]) -> bool:
    """True when trimming cut into the latest user/assistant turn itself."""
    def last_turn(msgs):
        for m in reversed(msgs or []):
            if m.get("role") != "system" and not m.get("_protected"):
                return m.get("content")
        return None
    return last_turn(before) != last_turn(after)


def context_notice(context_length: int, endpoint_url: str = "") -> str:
    """What to tell the user when their own message had to be shortened."""
    fix = ("Raise Settings → AI → Local Models → Context size to keep all of it."
           if (endpoint_url or "").startswith("local://")
           else "Start a new chat or use a model with a larger context window.")
    return (f"Your message was too long for this model's {context_length:,}-token context "
            f"window, so Apollo kept its beginning and end. {fix}")


def trim_for_context(messages: List[Dict], context_length: int, reserve_tokens: int = 512) -> List[Dict]:
    """Trim system messages to fit within context_length.

    For small-context models, progressively strips:
    1. RAG/memory system messages (keep preset system prompt)
    2. Older conversation turns
    Reserves space for the response.
    """
    budget = context_length - reserve_tokens
    used = estimate_tokens(messages)
    if used <= budget:
        return messages

    logger.info(f"Trimming messages: {used} tokens > {budget} budget (ctx={context_length})")

    # Separate system messages from conversation.
    # Messages marked _protected (e.g. active document) are never trimmed.
    system_msgs = []
    protected_msgs = []
    convo_msgs = []
    for msg in messages:
        if msg.get("_protected"):
            protected_msgs.append(msg)
        elif msg.get("role") == "system":
            system_msgs.append(msg)
        else:
            convo_msgs.append(msg)

    # Protected messages count toward budget but are never dropped
    protected_tokens = estimate_tokens(protected_msgs)
    budget -= protected_tokens

    # Priority: keep first system msg (preset prompt), drop others (memory, RAG, memo)
    essential_system = system_msgs[:1] if system_msgs else []
    extra_system = system_msgs[1:]

    # Try dropping extra system messages one by one (from the end)
    trimmed = essential_system + convo_msgs
    if estimate_tokens(trimmed) <= budget:
        # Dropping extras was enough — try adding back some
        result = list(essential_system)
        for msg in extra_system:
            candidate = result + [msg] + convo_msgs
            if estimate_tokens(candidate) <= budget:
                result.append(msg)
            else:
                break
        return _sanitize_tool_messages(result + protected_msgs + convo_msgs)

    # Still too big — truncate the first system message (but keep more than 500 chars)
    if essential_system:
        sys_text = essential_system[0].get("content", "")
        if len(sys_text) > 2000:
            essential_system[0] = {"role": "system", "content": sys_text[:2000] + "\n[System prompt truncated for context limits]"}
            trimmed = essential_system + convo_msgs
            if estimate_tokens(trimmed) <= budget:
                return _sanitize_tool_messages(essential_system + protected_msgs + convo_msgs)

    # Still too big — drop older conversation turns BUT always keep the current
    # user turn. If a pasted message alone exceeds the model context, truncate
    # that message with a visible notice instead of dropping it; otherwise the
    # model appears to "ignore" large pastes because it never receives them.
    # Hermes-style: recent context matters more than old context.
    PROTECT_RECENT = 10
    current_msg = convo_msgs[-1:] if convo_msgs else []
    prior_convo = convo_msgs[:-1] if convo_msgs else []
    if len(prior_convo) >= PROTECT_RECENT:
        old_msgs = prior_convo[:-(PROTECT_RECENT - 1)]
        recent_msgs = prior_convo[-(PROTECT_RECENT - 1):] + current_msg
        while old_msgs and estimate_tokens(essential_system + old_msgs + recent_msgs) > budget:
            old_msgs.pop(0)
        convo_msgs = old_msgs + recent_msgs
    else:
        convo_msgs = prior_convo + current_msg
        while prior_convo and estimate_tokens(essential_system + prior_convo + current_msg) > budget:
            prior_convo.pop(0)
        convo_msgs = prior_convo + current_msg

    # If the current message itself is too large, shrink only that message.
    if current_msg and estimate_tokens(essential_system + protected_msgs + convo_msgs) > budget:
        prefix = essential_system + protected_msgs + convo_msgs[:-1]
        available_for_current = max(64, budget - estimate_tokens(prefix))
        convo_msgs[-1] = _truncate_message_to_token_budget(convo_msgs[-1], available_for_current)

    result = _sanitize_tool_messages(essential_system + protected_msgs + convo_msgs)
    logger.info(f"Trimmed to {estimate_tokens(result)} tokens ({len(result)} messages)")
    return result


def _is_local(url: str) -> bool:
    from src.model_context import _is_local_endpoint
    return (url or "").startswith("local://") or _is_local_endpoint(url or "")


COMPACT_FAILURES_BEFORE_BACKOFF = 2
COMPACT_BACKOFF_SECONDS = 600.0


def _note_compaction_failure(session) -> None:
    if session is None:
        return
    n = (getattr(session, "_compact_failures", 0) or 0) + 1
    session._compact_failures = n
    if n >= COMPACT_FAILURES_BEFORE_BACKOFF:
        session._compact_skip_until = time.monotonic() + COMPACT_BACKOFF_SECONDS
        logger.warning("Compaction failed %d times in a row; not retrying for %.0f min",
                       n, COMPACT_BACKOFF_SECONDS / 60)


def _chunk_lines(lines: List[str], max_tokens: int) -> List[str]:
    """Group lines into chunks of at most ~max_tokens (a line never splits)."""
    chunks, cur, cur_tokens = [], [], 0
    for line in lines:
        t = estimate_tokens([{"role": "user", "content": line}])
        if cur and cur_tokens + t > max_tokens:
            chunks.append("\n".join(cur))
            cur, cur_tokens = [], 0
        cur.append(line)
        cur_tokens += t
    if cur:
        chunks.append("\n".join(cur))
    return chunks


async def maybe_compact(
    session,
    endpoint_url: str,
    model: str,
    messages: List[Dict],
    headers: Optional[Dict] = None,
) -> tuple:
    """Check context usage and compact if above threshold.

    Returns (messages, context_length, was_compacted).
    """
    context_length = get_context_length(endpoint_url, model)
    used = estimate_tokens(messages)
    pct = (used / context_length) * 100 if context_length else 0

    if pct < COMPACT_THRESHOLD * 100:
        return messages, context_length, False

    logger.info(
        f"Context at {pct:.1f}% ({used}/{context_length} tokens) — compacting"
    )

    # Split into system preface and conversation
    system_msgs = []
    convo_msgs = []
    for msg in messages:
        if msg.get("role") == "system":
            system_msgs.append(msg)
        else:
            convo_msgs.append(msg)

    if len(convo_msgs) < 4:
        return messages, context_length, False

    # Backoff: a model that keeps failing to summarise (too slow, refuses)
    # would otherwise be asked again on every turn. After two consecutive
    # failures, skip compaction for a while and let trim_for_context fit.
    if session is not None:
        until = getattr(session, "_compact_skip_until", 0) or 0
        if until and time.monotonic() < until:
            return messages, context_length, False

    # Split conversation: summarize older half, keep recent half
    split_point = len(convo_msgs) // 2
    older = convo_msgs[:split_point]
    recent = convo_msgs[split_point:]

    # Build the text to summarize, in chunks small enough that each summary
    # call surely fits the model and finishes: a local model given 60k tokens
    # of history in one call took longer than the old 30s timeout, failed,
    # and the older half was dropped with no summary at all.
    convo_lines = [f"{msg['role'].upper()}: {str(msg.get('content', ''))[:2000]}" for msg in older]
    chunks = _chunk_lines(convo_lines, COMPACT_CHUNK_TOKENS)

    # Count prior compactions from existing summary messages
    compaction_count = sum(
        1 for m in system_msgs
        if "[Conversation summary" in m.get("content", "")
    )

    # Use utility model if configured, otherwise fall back to session model
    util_url, util_model, util_headers = resolve_endpoint(
        "utility", fallback_url=endpoint_url, fallback_model=model, fallback_headers=headers,
    )
    compact_url = util_url or endpoint_url
    compact_model = util_model or model
    compact_headers = util_headers if util_url else headers

    prompt = SELF_SUMMARY_SYSTEM_PROMPT.replace(
        "{count}", str(len(older))
    ).replace(
        "{n}", str(compaction_count + 1)
    )
    # Local models are slow at long prompts; give them time rather than
    # failing the whole compaction.
    timeout = 180 if _is_local(compact_url) else 60
    parts = []
    try:
        for i, chunk in enumerate(chunks):
            summary_messages = [
                {"role": "system", "content": prompt},
                {"role": "user", "content": chunk if len(chunks) == 1
                 else f"[Part {i + 1} of {len(chunks)}]\n{chunk}"},
            ]
            parts.append(await llm_call_async(
                compact_url,
                compact_model,
                summary_messages,
                temperature=0.2,
                max_tokens=SUMMARY_MAX_TOKENS,
                headers=compact_headers,
                timeout=timeout,
            ))
    except Exception as e:
        # Keep the conversation intact: trim_for_context will fit it (dropping
        # only what it must) rather than losing half the chat with no summary.
        logger.error(f"Compaction summary failed after {len(parts)} of {len(chunks)} chunk(s): {e}")
        _note_compaction_failure(session)
        return messages, context_length, False
    summary = "\n\n".join(p.strip() for p in parts if p and p.strip())
    if not summary:
        logger.error("Compaction produced an empty summary; keeping the conversation as is")
        _note_compaction_failure(session)
        return messages, context_length, False
    if session is not None:
        session._compact_failures = 0
        session._compact_skip_until = 0

    summary_msg = {
        "role": "system",
        "content": f"[Conversation summary — earlier messages were compacted]\n{summary}",
    }

    compacted = system_msgs + [summary_msg] + recent

    # Update session history to match. Pass len(system_msgs) so the
    # recent_history slice in _update_session_history uses the correct
    # offset — session.history INCLUDES the system messages, but
    # split_point is indexed against convo_msgs which does NOT. Without
    # this, the slice drops the leading system message(s).
    _update_session_history(session, split_point, summary, system_msg_count=len(system_msgs))

    new_used = estimate_tokens(compacted)
    logger.info(
        f"Compacted: {used} -> {new_used} tokens "
        f"({len(older)} messages summarized, {len(recent)} kept)"
    )

    return compacted, context_length, True


def _update_session_history(session, split_point: int, summary: str,
                            system_msg_count: int = 0):
    """Update the in-memory session history after compaction.

    `split_point` is the index in `convo_msgs` (system-stripped). The
    in-memory `session.history` includes leading system messages, so the
    actual recent-history slice starts at `system_msg_count + split_point`.
    Prepending `session.history[:system_msg_count]` to the new history
    preserves persona, preset, and RAG system messages that would
    otherwise be dropped.
    """
    if not session or not hasattr(session, "history"):
        return

    effective_split = system_msg_count + split_point
    if effective_split >= len(session.history):
        return

    # Keep the recent messages, prepend summary AND the leading system
    # messages so the system prompt survives compaction.
    system_prefix = list(session.history[:system_msg_count])
    recent_history = session.history[effective_split:]
    summary_msg = ChatMessage(
        role="system",
        content=f"[Conversation summary]\n{summary}",
        metadata={"compacted": True, "summarized_count": split_point},
    )
    new_history = system_prefix + [summary_msg] + recent_history
    try:
        from core import models as _core_models
        manager = getattr(_core_models, "_session_manager", None)
    except ImportError:
        manager = None
    if manager and getattr(session, "id", None):
        if manager.replace_messages(session.id, new_history):
            return
    session.history = new_history
