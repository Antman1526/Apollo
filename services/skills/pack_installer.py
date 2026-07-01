"""Install Agent Skills packs into Apollo's SKILL.md store.

Safe-by-default: prose-only skills install published; skills that ship
executable code (scripts/hooks/MCP config) are quarantined as drafts and never
run during import. Discover/classify/normalize/install are pure and operate on
local dirs; only fetch_pack touches the network (SSRF-guarded).
"""
import os

_CODE_EXTS = (".py", ".js", ".mjs", ".ts", ".sh", ".rb", ".php", ".pl", ".ps1")
_CODE_DIRS = ("scripts", "hooks", "bin")
_CODE_FILES = (".mcp.json",)


def classify_tier(skill_dir: str) -> str:
    """Return 'script' if the skill folder ships executable code / hooks / MCP
    config, else 'prose'. Never executes anything — inspects file names only."""
    for root, dirs, files in os.walk(skill_dir):
        rel = os.path.relpath(root, skill_dir)
        parts = set(rel.split(os.sep))
        if parts & set(_CODE_DIRS):
            return "script"
        for f in files:
            if f in _CODE_FILES or os.path.splitext(f)[1].lower() in _CODE_EXTS:
                return "script"
    return "prose"
