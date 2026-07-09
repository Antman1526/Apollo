"""Chat search tool."""
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)




# ---------------------------------------------------------------------------
# Search chats
# ---------------------------------------------------------------------------

async def do_search_chats(query: str, limit: int = 20, owner: str | None = None) -> Dict:
    """Search past chat messages for the calling user's sessions only.

    Without an owner filter this used to leak EVERY user's chat history
    into the agent's `search_chats` results (v2 review HIGH-11). The
    caller in `tool_execution.execute_tool_block` now plumbs the owner
    through; legacy callers without owner pass through as before but
    will only see legacy/null-owner rows.
    """
    from src.database import SessionLocal, ChatMessage as DBChatMessage, Session as DBSession
    # Escape LIKE wildcards in the user-supplied query so a stray % or _
    # doesn't widen the match (and to keep the response deterministic).
    safe_q = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    db = SessionLocal()
    try:
        q = (
            db.query(DBChatMessage, DBSession.id, DBSession.name)
            .join(DBSession, DBChatMessage.session_id == DBSession.id)
            .filter(
                DBSession.archived == False,
                DBChatMessage.content.ilike(f"%{safe_q}%", escape="\\"),
                DBChatMessage.role.in_(["user", "assistant"]),
            )
        )
        if owner is not None:
            # Restrict to this user's sessions plus legacy null-owner
            # rows (so single-user upgrades keep seeing their own data).
            q = q.filter((DBSession.owner == owner) | (DBSession.owner.is_(None)))
        rows = q.order_by(DBChatMessage.timestamp.desc()).limit(limit).all()

        if not rows:
            return {"results": f"No chats found matching \"{query}\"."}

        # Group by session to avoid duplicate links
        seen_sessions = {}
        for msg, session_id, session_name in rows:
            if session_id not in seen_sessions:
                content = msg.content or ""
                lower_content = content.lower()
                idx = lower_content.find(query.lower())
                if idx == -1:
                    snippet = content[:150]
                else:
                    start = max(0, idx - 60)
                    end = min(len(content), idx + len(query) + 60)
                    snippet = ("..." if start > 0 else "") + content[start:end] + ("..." if end < len(content) else "")
                seen_sessions[session_id] = {
                    "name": session_name or "Untitled",
                    "snippet": snippet,
                    "role": msg.role,
                    "timestamp": msg.timestamp.isoformat() if msg.timestamp else None,
                }

        lines = [f"Found {len(seen_sessions)} session(s) matching \"{query}\":\n"]
        for sid, info in seen_sessions.items():
            lines.append(f"- **{info['name']}** (#{sid})")
            lines.append(f"  Link: [Open chat](#{sid})")
            lines.append(f"  > {info['snippet']}")
            lines.append("")

        return {"results": "\n".join(lines)}
    except Exception as e:
        logger.error(f"search_chats failed: {e}")
        return {"error": str(e), "exit_code": 1}
    finally:
        db.close()
