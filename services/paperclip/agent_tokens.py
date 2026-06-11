"""Per-agent tokens for the local-model proxy (Phase 3.4).

Each Paperclip agent can get its own bearer token for ``/lmproxy/v1/*``
instead of the shared ``PAPERCLIP_PROXY_TOKEN``. The proxy maps token →
agent, which buys two things:

- the Floor can show *which* agent is generating (activity pulses), and
- per-agent usage accounting becomes possible later.

The agent's ``opencode-local`` adapter honors a per-agent ``OPENAI_API_KEY``
override, so pasting a minted token into that field routes the agent through
its own identity. Tokens are local secrets stored like the shared proxy
token (a 0600 JSON file under ``~/.apollo``).
"""
from __future__ import annotations

import json
import logging
import os
import secrets
import threading
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_PATH = "~/.apollo/paperclip_agent_tokens.json"


class AgentTokenRegistry:
    """File-backed token → agent mapping. Thread-safe; persists on mint."""

    def __init__(self, path: str = DEFAULT_PATH):
        self._path = os.path.expanduser(path)
        self._lock = threading.Lock()
        self._tokens: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        try:
            with open(self._path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                self._tokens = {
                    str(token): dict(meta)
                    for token, meta in data.items()
                    if isinstance(meta, dict) and meta.get("agent_id")
                }
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as exc:
            logger.warning("Could not read agent token registry: %s", exc)

    def _save(self) -> None:
        os.makedirs(os.path.dirname(self._path), exist_ok=True)
        with open(self._path, "w", encoding="utf-8") as fh:
            json.dump(self._tokens, fh, indent=2)
        try:
            os.chmod(self._path, 0o600)
        except OSError:
            pass

    def mint(self, agent_id: str, name: str = "") -> str:
        """Create (or rotate) the token for an agent and persist it."""
        agent_id = str(agent_id).strip()
        if not agent_id:
            raise ValueError("agent_id is required")
        token = "pa-" + secrets.token_hex(24)
        with self._lock:
            # One token per agent: minting again rotates the old one out.
            self._tokens = {
                tok: meta for tok, meta in self._tokens.items()
                if meta.get("agent_id") != agent_id
            }
            self._tokens[token] = {"agent_id": agent_id, "name": str(name or agent_id)}
            self._save()
        return token

    def lookup(self, token: str) -> Optional[dict]:
        if not token:
            return None
        with self._lock:
            meta = self._tokens.get(token)
            return dict(meta) if meta else None

    def list(self) -> list[dict]:
        """Token metadata without the secrets (suffix only, for the UI)."""
        with self._lock:
            return [
                {
                    "agent_id": meta.get("agent_id", ""),
                    "name": meta.get("name", ""),
                    "token_suffix": token[-6:],
                }
                for token, meta in self._tokens.items()
            ]
