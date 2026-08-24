"""Curated one-click catalog: skill packs + MCP server presets.

Pure data plus thin lookups — installation itself always goes through the
existing, already-guarded pipelines (skill_pack_routes for packs, the MCP
`add_server` form for servers). This module never installs anything itself;
it only tells the frontend what to pre-fill, so an admin always sees and
confirms the real command/source before anything runs.

Entries are intentionally few and verified against each project's own repo
rather than padded out — an unverifiable "MCP server" that doesn't actually
exist would be worse than a short honest list.
"""
from __future__ import annotations

from typing import Any, Dict, List

# Skill packs: any GitHub repo of SKILL.md files works via
# /api/skills/packs/preview already — these are just good defaults.
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

# MCP servers: verified real npm packages from the official
# modelcontextprotocol/servers monorepo. "Add" pre-fills the existing MCP
# add-server form — the admin still reviews the command and submits it.
MCP_PRESETS: List[Dict[str, Any]] = [
    {
        "id": "mcp-filesystem",
        "name": "Filesystem",
        "description": "Read/write access to directories you specify, with "
                       "MCP-native root/permission controls.",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-filesystem"],
        "args_hint": "Append one or more directory paths to allow.",
        "env": {},
    },
    {
        "id": "mcp-fetch",
        "name": "Fetch",
        "description": "Fetches a URL and converts it to clean, LLM-friendly "
                       "markdown — useful alongside Apollo's own web search.",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-fetch"],
        "args_hint": "",
        "env": {},
    },
    {
        "id": "mcp-brave-search",
        "name": "Brave Search",
        "description": "Web + local search via the Brave Search API. Needs "
                       "your own Brave Search API key.",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-brave-search"],
        "args_hint": "",
        "env": {"BRAVE_API_KEY": ""},
    },
]


def get_catalog() -> Dict[str, Any]:
    return {"skill_packs": SKILL_PACKS, "mcp_servers": MCP_PRESETS}
