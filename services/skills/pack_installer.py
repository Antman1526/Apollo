"""Install Agent Skills packs into Apollo's SKILL.md store.

Safe-by-default: prose-only skills install published; skills that ship
executable code (scripts/hooks/MCP config) are quarantined as drafts and never
run during import. Discover/classify/normalize/install are pure and operate on
local dirs; only fetch_pack touches the network (SSRF-guarded).
"""
import os
import logging

from src.observability import report_exception

logger = logging.getLogger(__name__)

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


import re as _re
from dataclasses import dataclass, field, replace

from services.memory.skill_format import parse_frontmatter, slugify


def _parse_frontmatter_robust(text: str):
    """Parse SKILL.md frontmatter with PyYAML, falling back to Apollo's regex
    parser. External packs use richer YAML (block scalars, quotes, nested
    metadata) than Apollo's regex parser handles — e.g. `description: |` would
    otherwise come back as the literal '|'. Returns (frontmatter_dict, body)."""
    m = _re.match(r"^﻿?---\s*\n(.*?)\n---\s*\n?(.*)$", text, _re.S)
    if m:
        try:
            import yaml
            fm = yaml.safe_load(m.group(1))
            if isinstance(fm, dict):
                return fm, m.group(2)
        except Exception as error:
            report_exception(
                logger,
                "skill_pack_frontmatter_parse_failed",
                error,
                outcome="best_effort",
            )
    return parse_frontmatter(text)


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
            fm, body = _parse_frontmatter_robust(text)
            name = slugify(str(fm.get("name") or raw_name))
            desc = str(fm.get("description") or "").strip()
            tier = classify_tier(skill_dir)
            extra = [
                os.path.relpath(os.path.join(r, f), skill_dir)
                for r, _d, fs in os.walk(skill_dir) for f in fs
                if not (r == skill_dir and f == "SKILL.md")
            ]
            out.append(FoundSkill(name, desc, tier, rel_dir, fm, body, extra))
        except Exception as error:  # never let one bad skill abort the pack
            report_exception(
                logger,
                "skill_pack_discovery_entry_failed",
                error,
                outcome="best_effort",
                context={"skill_name": slugify(raw_name)},
            )
            out.append(FoundSkill(slugify(raw_name), "", "prose", rel_dir, {}, "", [], "parse failed"))
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


import shutil


def install_skills(found_list, opts, skills_root, src_root=""):
    # Sanitize the caller-supplied category so it can't escape skills_root via a
    # crafted value like "../../etc" (slugify collapses separators/dots). Names
    # are already slugified in discover_skills. SECURITY.
    opts = replace(opts, category=slugify(opts.category or "imported", fallback="imported"))
    installed, skipped, errored = [], [], []
    for f in found_list:
        if f.error:
            errored.append(f.name)
            continue
        dest_dir = os.path.join(skills_root, opts.category, f.name)
        if os.path.exists(os.path.join(dest_dir, "SKILL.md")) and not opts.overwrite:
            skipped.append(f.name)
            continue
        os.makedirs(dest_dir, exist_ok=True)
        # Copy the skill's own files (inert) for script-backed skills so the
        # quarantined draft is complete; SKILL.md is written normalized below.
        if src_root and f.tier == "script":
            src_dir = os.path.join(src_root, f.rel_dir)
            for rel in f.files:
                s = os.path.join(src_dir, rel)
                d = os.path.join(dest_dir, rel)
                os.makedirs(os.path.dirname(d), exist_ok=True)
                try:
                    shutil.copy2(s, d)
                except OSError:
                    pass
        with open(os.path.join(dest_dir, "SKILL.md"), "w", encoding="utf-8") as fh:
            fh.write(render_skill_md(f, opts))
        installed.append(f.name)
    return {"installed": installed, "skipped": skipped, "errored": errored}


import io
import tarfile
import tempfile
from urllib.parse import urlparse


_MAX_PACK_MEMBERS = 5000


def safe_extract_tar(tar, dest: str, max_bytes: int) -> str:
    members = tar.getmembers()
    # Member-count cap guards against inode exhaustion (many tiny entries).
    if len(members) > _MAX_PACK_MEMBERS:
        raise ValueError("archive has too many entries")
    total = 0
    for m in members:
        # Reject absolute paths and name-based traversal up front.
        if m.name.startswith("/") or ".." in m.name.split("/"):
            raise ValueError(f"unsafe path in archive: {m.name}")
        total += max(0, m.size)
        if total > max_bytes:
            raise ValueError("archive too large")
    try:
        # filter="data" (Python 3.12+) is the vetted extraction policy: it blocks
        # symlink/hardlink members that escape dest, absolute paths, and device
        # nodes — closing the traversal gap the name-only check above misses.
        tar.extractall(dest, filter="data")
    except tarfile.FilterError as e:
        raise ValueError(f"unsafe archive member: {e}") from e
    return dest


_MAX_PACK_BYTES = 50 * 1024 * 1024  # 50 MB


def _github_tarball_url(repo_url: str, ref: str = "") -> str:
    """Map a github.com repo URL to the API tarball endpoint (default branch if
    no ref). api.github.com is a public host, so _get_public_url allows it."""
    p = urlparse(repo_url)
    parts = [x for x in p.path.split("/") if x]
    if p.hostname not in ("github.com", "www.github.com") or len(parts) < 2:
        raise ValueError("expected a https://github.com/<owner>/<repo> URL")
    owner, repo = parts[0], parts[1].removesuffix(".git")
    base = f"https://api.github.com/repos/{owner}/{repo}/tarball"
    return f"{base}/{ref}" if ref else base


def fetch_pack(source: str, ref: str = "", *, timeout: int = 30) -> str:
    """Download a GitHub repo tarball (SSRF-guarded) and extract to a temp dir.
    Returns the extraction root. Raises on non-public URL / oversize / traversal."""
    from src.search.content import _get_public_url

    url = _github_tarball_url(source, ref)
    resp = _get_public_url(url, headers={"Accept": "application/vnd.github+json",
                                         "User-Agent": "Apollo-SkillInstaller"}, timeout=timeout)
    resp.raise_for_status()
    if len(resp.content) > _MAX_PACK_BYTES:
        raise ValueError("pack download too large")
    dest = tempfile.mkdtemp(prefix="apollo-skillpack-")
    with tarfile.open(fileobj=io.BytesIO(resp.content), mode="r:gz") as t:
        safe_extract_tar(t, dest, _MAX_PACK_BYTES)
    return dest
