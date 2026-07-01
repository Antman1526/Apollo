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


from dataclasses import dataclass, field

from services.memory.skill_format import parse_frontmatter, slugify


@dataclass
class FoundSkill:
    name: str
    description: str
    tier: str
    rel_dir: str
    frontmatter: dict
    body: str
    files: list = field(default_factory=list)
    error: str = ""


def discover_skills(pack_root: str) -> list:
    out = []
    for root, _dirs, files in os.walk(pack_root):
        if "SKILL.md" not in files:
            continue
        skill_dir = root
        rel_dir = os.path.relpath(skill_dir, pack_root)
        raw_name = os.path.basename(skill_dir.rstrip(os.sep)) or "skill"
        try:
            text = open(os.path.join(skill_dir, "SKILL.md"), encoding="utf-8", errors="replace").read()
            fm, body = parse_frontmatter(text)
            name = slugify(str(fm.get("name") or raw_name))
            desc = str(fm.get("description") or "").strip()
            tier = classify_tier(skill_dir)
            extra = [
                os.path.relpath(os.path.join(r, f), skill_dir)
                for r, _d, fs in os.walk(skill_dir) for f in fs
                if not (r == skill_dir and f == "SKILL.md")
            ]
            out.append(FoundSkill(name, desc, tier, rel_dir, fm, body, extra))
        except Exception as e:  # never let one bad skill abort the pack
            out.append(FoundSkill(slugify(raw_name), "", "prose", rel_dir, {}, "", [], str(e)))
    return out


from services.memory.skill_format import emit_frontmatter


@dataclass
class InstallOpts:
    category: str
    owner: "str | None"
    source_url: str
    source_ref: str
    now_iso: str
    overwrite: bool = False


def render_skill_md(found: "FoundSkill", opts: "InstallOpts") -> str:
    fm = {
        "name": found.name,
        "description": found.description,
        "version": str(found.frontmatter.get("version") or "1.0.0"),
        "category": opts.category,
        "status": "published" if found.tier == "prose" else "draft",
        "source": "imported",
    }
    if opts.owner:
        fm["owner"] = opts.owner
    # emit_frontmatter() JSON-quotes any value containing ':' (e.g. a URL or an
    # ISO timestamp). Provenance keys read better — and match the installer's
    # expected frontmatter — as plain unquoted lines, so emit them by hand and
    # keep emit_frontmatter for the well-behaved scalar fields above.
    provenance = {
        "imported_from": opts.source_url,
        "imported_ref": opts.source_ref,
        "imported_at": opts.now_iso,
        "imported_tier": found.tier,
    }
    prov_lines = "\n".join(f"{k}: {v}" for k, v in provenance.items() if v != "")
    fm_text = emit_frontmatter(fm)
    if prov_lines:
        fm_text = f"{fm_text}\n{prov_lines}"
    body = (found.body or "").strip("\n")
    return f"---\n{fm_text}\n---\n\n{body}\n"
