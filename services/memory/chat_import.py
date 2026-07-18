"""Parse external chat-export archives (ChatGPT / Claude) into a common shape.

Pure: no network/DB. Each parser returns a list of conversations:

    {"title": str, "messages": [{"role": str, "text": str}, ...]}

Roles are normalized (`human` -> `user`). Empty-text messages are skipped.
Unrecognized shapes return `[]` (never raise); a single malformed conversation
is skipped rather than aborting the whole import.
"""
import logging
from typing import Any, Dict, List

from src.observability import report_exception

logger = logging.getLogger(__name__)

_ROLE_MAP = {"human": "user"}


def _norm_role(role: Any) -> str:
    r = (role or "").strip().lower() if isinstance(role, str) else ""
    return _ROLE_MAP.get(r, r or "user")


def _text_from_parts(content: Any) -> str:
    """Extract text from a ChatGPT `content` object ({"parts": [...]})."""
    if isinstance(content, dict):
        parts = content.get("parts")
        if isinstance(parts, list):
            return "".join(p for p in parts if isinstance(p, str)).strip()
        text = content.get("text")
        if isinstance(text, str):
            return text.strip()
    if isinstance(content, str):
        return content.strip()
    return ""


def _parse_chatgpt_conversation(convo: Dict) -> Dict:
    title = convo.get("title") or convo.get("name") or "Untitled"
    mapping = convo.get("mapping")
    messages: List[Dict] = []
    if isinstance(mapping, dict):
        # Preserve creation order where available; fall back to dict order.
        nodes = list(mapping.values())
        nodes.sort(
            key=lambda n: (n or {}).get("message", {}).get("create_time") or 0
            if isinstance(n, dict) and isinstance(n.get("message"), dict)
            else 0
        )
        for node in nodes:
            if not isinstance(node, dict):
                continue
            msg = node.get("message")
            if not isinstance(msg, dict):
                continue
            author = msg.get("author") if isinstance(msg.get("author"), dict) else {}
            role = _norm_role(author.get("role"))
            text = _text_from_parts(msg.get("content"))
            if not text:
                continue
            messages.append({"role": role, "text": text})
    return {"title": title, "messages": messages}


def parse_chatgpt_export(obj: Any) -> List[Dict]:
    """ChatGPT export: a list of conversations each with a `mapping` dict."""
    if not isinstance(obj, list):
        return []
    out: List[Dict] = []
    for index, convo in enumerate(obj):
        try:
            if not isinstance(convo, dict):
                continue
            parsed = _parse_chatgpt_conversation(convo)
            if parsed["messages"]:
                out.append(parsed)
        except Exception as error:
            report_exception(
                logger,
                "chat_import_conversation_parse_failed",
                error,
                outcome="best_effort",
                context={"format": "chatgpt", "record_index": index},
            )
            continue
    return out


def _parse_claude_conversation(convo: Dict) -> Dict:
    title = convo.get("name") or convo.get("title") or "Untitled"
    chat_messages = convo.get("chat_messages")
    messages: List[Dict] = []
    if isinstance(chat_messages, list):
        for msg in chat_messages:
            if not isinstance(msg, dict):
                continue
            role = _norm_role(msg.get("sender") or msg.get("role"))
            text = msg.get("text")
            if not isinstance(text, str):
                text = _text_from_parts(msg.get("content"))
            text = (text or "").strip()
            if not text:
                continue
            messages.append({"role": role, "text": text})
    return {"title": title, "messages": messages}


def parse_claude_export(obj: Any) -> List[Dict]:
    """Claude export: {"conversations": [{"chat_messages": [...]}, ...]}."""
    if not isinstance(obj, dict):
        return []
    conversations = obj.get("conversations")
    if not isinstance(conversations, list):
        return []
    out: List[Dict] = []
    for index, convo in enumerate(conversations):
        try:
            if not isinstance(convo, dict):
                continue
            parsed = _parse_claude_conversation(convo)
            if parsed["messages"]:
                out.append(parsed)
        except Exception as error:
            report_exception(
                logger,
                "chat_import_conversation_parse_failed",
                error,
                outcome="best_effort",
                context={"format": "claude", "record_index": index},
            )
            continue
    return out


def _looks_like_chatgpt(obj: Any) -> bool:
    return isinstance(obj, list) and any(
        isinstance(c, dict) and "mapping" in c for c in obj
    )


def _looks_like_claude(obj: Any) -> bool:
    return isinstance(obj, dict) and isinstance(obj.get("conversations"), list)


def parse_export(obj: Any) -> List[Dict]:
    """Auto-detect the export format and dispatch. Unknown -> []."""
    if _looks_like_chatgpt(obj):
        return parse_chatgpt_export(obj)
    if _looks_like_claude(obj):
        return parse_claude_export(obj)
    return []
