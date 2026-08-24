"""Import persona/character-preset markdown files from a GitHub repo.

Same shape as agency-agents (msitarzewski/agency-agents): one markdown file
per persona, YAML frontmatter (name/description/...) + a full "You are X…"
system-prompt body — but the source repo is not hardcoded, any repo of
`{name, description}`-fronted markdown files works.

Deliberately reuses the skill-pack installer's already-guarded download path
(`fetch_pack`: SSRF-checked, size-capped, tar-safe) instead of a second
implementation, and its frontmatter parser (PyYAML with a regex fallback).
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from services.skills.pack_installer import _parse_frontmatter_robust, fetch_pack
from services.memory.skill_format import slugify

logger = logging.getLogger(__name__)

# Files that look like personas but aren't — repo docs, not agents.
_SKIP_NAMES = {"readme", "contributing", "license", "changelog", "code_of_conduct"}
MAX_PERSONAS = 400  # generous — agency-agents ships ~230; a hard backstop, not a curation choice


@dataclass
class FoundPersona:
    rel_path: str
    name: str
    description: str
    system_prompt: str


def discover_personas(pack_root: str) -> List[FoundPersona]:
    """Walk an extracted repo for persona-shaped markdown files."""
    found: List[FoundPersona] = []
    for root, dirs, files in os.walk(pack_root):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d != "node_modules"]
        for fn in sorted(files):
            if not fn.lower().endswith(".md"):
                continue
            stem = fn[:-3].lower()
            if stem in _SKIP_NAMES:
                continue
            path = os.path.join(root, fn)
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    text = f.read()
            except OSError:
                continue
            fm, body = _parse_frontmatter_robust(text)
            name = (fm.get("name") or "").strip() if isinstance(fm, dict) else ""
            if not name:
                continue  # not a persona file — no frontmatter name
            description = (fm.get("description") or "").strip() if isinstance(fm, dict) else ""
            found.append(FoundPersona(
                rel_path=os.path.relpath(path, pack_root),
                name=name,
                description=description,
                system_prompt=body.strip(),
            ))
            if len(found) >= MAX_PERSONAS:
                return found
    return found


def preview_personas(source: str, ref: str = "") -> List[Dict[str, Any]]:
    pack_root = fetch_pack(source, ref)
    try:
        return [
            {"rel_path": p.rel_path, "name": p.name, "description": p.description}
            for p in discover_personas(pack_root)
        ]
    finally:
        import shutil
        shutil.rmtree(pack_root, ignore_errors=True)


def install_personas(
    source: str,
    names: List[str],
    preset_manager: Any,
    ref: str = "",
) -> Dict[str, int]:
    """Fetch, filter to the requested persona names, and save each as a user
    template. Dedupes by slugified name against existing templates."""
    pack_root = fetch_pack(source, ref)
    try:
        wanted = set(names)
        personas = [p for p in discover_personas(pack_root) if p.name in wanted]
        existing = {t.get("id") for t in preset_manager.get_user_templates()}
        added = 0
        skipped = 0
        for p in personas:
            tid = "persona-" + slugify(p.name)
            if tid in existing:
                skipped += 1
                continue
            preset_manager.save_user_template({
                "id": tid,
                "name": p.name,
                "system_prompt": p.system_prompt[:10000],  # matches UserTemplateRequest cap
                "temperature": 1.0,
                "max_tokens": 0,
            })
            existing.add(tid)
            added += 1
        return {"added": added, "skipped": skipped}
    finally:
        import shutil
        shutil.rmtree(pack_root, ignore_errors=True)
