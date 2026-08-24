"""Reference Library — local, searchable catalogs of external resources.

A fourth store, deliberately separate from the three Apollo already has:
memory holds facts about the USER, skills hold PROCEDURES, documents hold the
user's OWN files. None of those is the right home for a third-party catalog —
folding 1,700 API listings into memory would poison recall, and an API listing
is not a procedure. So catalogs live here, queried on demand by the
`reference_search` agent tool instead of riding in every prompt.

Only the specific markdown files that hold each catalog are fetched (a few
hundred KB), not whole repo tarballs — developer-roadmap alone ships tens of
MB of site assets that would be pure waste. Fetches reuse the same
SSRF-guarded HTTP path as the skill-pack installer.
"""
from __future__ import annotations

import hashlib
import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

RAW_BASE = "https://raw.githubusercontent.com"
_REPO_RE = re.compile(r"^[\w.-]+/[\w.-]+$")
_PATH_RE = re.compile(r"^(?!.*\.\.)[\w./-]+\.md$", re.IGNORECASE)

# Per-source hard cap. Generous (public-apis ships ~1,400 real entries) but
# bounded so a malformed upstream file can't balloon the table.
MAX_ENTRIES_PER_SOURCE = 6000
_MAX_FILE_BYTES = 8 * 1024 * 1024

# kind values: "api" is agent-actionable (feeds api_call); the rest are
# human-facing learning resources surfaced on request.
SOURCES: Dict[str, Dict[str, Any]] = {
    "public-apis": {
        "name": "Public APIs",
        "repo": "public-apis/public-apis",
        "ref": "master",
        "files": ["README.md"],
        "parser": "api_table",
        "kind": "api",
        "license": "MIT",
        "description": "~1,400 free public APIs with auth/HTTPS/CORS noted — "
                       "the agent can look one up and call it directly.",
        "agent_actionable": True,
    },
    "build-your-own-x": {
        "name": "Build Your Own X",
        "repo": "codecrafters-io/build-your-own-x",
        "ref": "master",
        "files": ["README.md"],
        "parser": "byox",
        "kind": "tutorial",
        "license": "CC0-1.0",
        "description": "~400 step-by-step 'build X from scratch' tutorials, "
                       "grouped by what you're building.",
        "agent_actionable": False,
    },
    "free-programming-books": {
        "name": "Free Programming Books",
        "repo": "EbookFoundation/free-programming-books",
        "ref": "main",
        "files": [
            "books/free-programming-books-langs.md",
            "books/free-programming-books-subjects.md",
            "courses/free-courses-en.md",
        ],
        "parser": "book_list",
        "kind": "book",
        "license": "CC-BY-4.0",
        "description": "Free books and courses by language and subject "
                       "(English set; the repo covers 50+ languages).",
        "agent_actionable": False,
    },
    "developer-roadmap": {
        "name": "Developer Roadmaps",
        "repo": "nilbuild/developer-roadmap",
        "ref": "master",
        "files": ["readme.md"],
        "parser": "roadmap",
        "kind": "roadmap",
        "license": "custom (unrecognized by GitHub — check repo before reuse)",
        "description": "80+ interactive learning roadmaps (frontend, backend, "
                       "DevOps, AI, …) from roadmap.sh.",
        "agent_actionable": False,
    },
}


def _raw_url(repo: str, ref: str, path: str) -> str:
    if not _REPO_RE.match(repo or ""):
        raise ValueError(f"invalid repo: {repo}")
    if not _PATH_RE.match(path or ""):
        raise ValueError(f"invalid file path: {path}")
    safe_ref = re.sub(r"[^\w.-]", "", ref or "main")
    return f"{RAW_BASE}/{repo}/{safe_ref}/{path}"


def fetch_markdown(repo: str, ref: str, path: str, *, timeout: int = 30) -> str:
    """Fetch one catalog markdown file through the shared SSRF guard."""
    from src.search.content import _get_public_url

    url = _raw_url(repo, ref, path)
    resp = _get_public_url(url, headers={"User-Agent": "apollo-reference-library"},
                           timeout=timeout)
    if len(resp.content) > _MAX_FILE_BYTES:
        raise ValueError(f"catalog file too large: {path}")
    return resp.text


# ── Parsers ──────────────────────────────────────────────────────────
# Each returns [{category, title, url, description, meta}].

_H_RE = re.compile(r"^(#{2,4})\s+(.+?)\s*$")
_LINK_ROW_RE = re.compile(r"^\|\s*\[([^\]]+)\]\(([^)\s]+)[^)]*\)\s*\|(.*)$")
_LIST_LINK_RE = re.compile(r"^\s*[*-]\s+\[([^\]]+)\]\(([^)\s]+)[^)]*\)\s*(.*)$")
_BYOX_RE = re.compile(r"\[\*\*([^*\]]+)\*\*\s*:\s*_?([^_\]]+?)_?\]\(([^)\s]+)[^)]*\)")


_MD_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")


def _clean(text: str) -> str:
    """Markdown/HTML → plain text. Link syntax collapses to its label so a
    heading like `### [View all](url) &middot; [Best](url)` doesn't leak raw
    markup into a stored category."""
    text = _MD_LINK_RE.sub(r"\1", text or "")
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"&[a-z]+;", " ", text)
    text = re.sub(r"[*_`]", "", text)
    return re.sub(r"\s+", " ", text).strip()


# The public-apis README opens with a sponsor block whose tables look almost
# like real entries. Verified against the live file: real rows carry the full
# API|Description|Auth|HTTPS|CORS shape (4+ trailing cells) while ad rows have
# 3 and always carry the sponsor campaign tag. Both guards are applied — an
# ad served to the agent as a "free API" would be a real correctness bug.
_SPONSOR_MARKERS = ("utm_campaign=public-apis-repo", "apilayer.com", "run.pstmn.io")


def _parse_api_table(md: str) -> List[Dict[str, Any]]:
    """public-apis: `| [Name](url) | Desc | Auth | HTTPS | CORS |` under `### Category`."""
    out: List[Dict[str, Any]] = []
    category = ""
    for line in md.splitlines():
        h = _H_RE.match(line)
        if h:
            if len(h.group(1)) == 3:
                category = _clean(h.group(2))
            continue
        m = _LINK_ROW_RE.match(line)
        if not m:
            continue
        title, url = _clean(m.group(1)), m.group(2).strip()
        if not title or not url.startswith("http"):
            continue
        low = line.lower()
        if any(mark in low for mark in _SPONSOR_MARKERS):
            continue
        cols = [c.strip() for c in m.group(3).split("|")]
        if len([c for c in cols if c]) < 3:   # real rows have desc+auth+https+cors
            continue
        desc = _clean(cols[0]) if cols else ""
        meta = {}
        if len(cols) > 1:
            auth = _clean(cols[1])
            meta["auth"] = "none" if auth.lower() in ("", "no") else auth
        if len(cols) > 2:
            meta["https"] = _clean(cols[2])
        if len(cols) > 3:
            meta["cors"] = _clean(cols[3])
        out.append({"category": category, "title": title, "url": url,
                    "description": desc, "meta": meta})
    return out


def _parse_byox(md: str) -> List[Dict[str, Any]]:
    """build-your-own-x: `[**Language**: _Title_](url)` list items."""
    out: List[Dict[str, Any]] = []
    category = ""
    for line in md.splitlines():
        h = _H_RE.match(line)
        if h:
            category = _clean(h.group(2)).removeprefix("Build your own ").strip()
            continue
        for m in _BYOX_RE.finditer(line):
            lang, title, url = _clean(m.group(1)), _clean(m.group(2)), m.group(3).strip()
            if not title or not url.startswith("http"):
                continue
            out.append({"category": category, "title": title, "url": url,
                        "description": f"{lang} tutorial", "meta": {"language": lang}})
    return out


def _parse_book_list(md: str) -> List[Dict[str, Any]]:
    """free-programming-books: `* [Title](url) - Author (format)` under `### Topic`."""
    out: List[Dict[str, Any]] = []
    category = ""
    for line in md.splitlines():
        h = _H_RE.match(line)
        if h:
            category = _clean(h.group(2))
            continue
        m = _LIST_LINK_RE.match(line)
        if not m:
            continue
        title, url = _clean(m.group(1)), m.group(2).strip()
        if not title or not url.startswith("http"):
            continue
        tail = _clean(m.group(3)).lstrip("-").strip()
        out.append({"category": category, "title": title, "url": url,
                    "description": tail, "meta": {}})
    return out


_ANY_LINK_RE = re.compile(r"\[([^\]]+)\]\((https://roadmap\.sh[^)\s]*)\)")


def _parse_roadmap(md: str) -> List[Dict[str, Any]]:
    """developer-roadmap: roadmap.sh links in list items.

    Scans every link on a line rather than just the first: entries pair a main
    and a beginner variant on one line (`- [Frontend](…) / [Frontend Beginner](…)`),
    and taking only the first would silently drop half the catalog.
    """
    # Verified against the live readme: it carries no per-roadmap category
    # headings (the only heading above the list is a nav line), so a flat
    # category is the honest representation rather than invented grouping.
    out: List[Dict[str, Any]] = []
    seen: set = set()
    for line in md.splitlines():
        if not re.match(r"^\s*[*-]\s+", line):
            continue
        for m in _ANY_LINK_RE.finditer(line):
            title, url = _clean(m.group(1)), m.group(2).strip()
            if not title or url in seen:
                continue
            seen.add(url)
            out.append({"category": "Roadmaps", "title": title,
                        "url": url, "description": "", "meta": {}})
    return out


_PARSERS = {
    "api_table": _parse_api_table,
    "byox": _parse_byox,
    "book_list": _parse_book_list,
    "roadmap": _parse_roadmap,
}


def parse_source(source_id: str, docs: List[str]) -> List[Dict[str, Any]]:
    """Parse already-fetched markdown for a source. Pure — no I/O, easy to test."""
    cfg = SOURCES.get(source_id)
    if not cfg:
        raise ValueError(f"unknown source: {source_id}")
    parser = _PARSERS[cfg["parser"]]
    entries: List[Dict[str, Any]] = []
    for md in docs:
        entries.extend(parser(md))
        if len(entries) >= MAX_ENTRIES_PER_SOURCE:
            entries = entries[:MAX_ENTRIES_PER_SOURCE]
            logger.warning("reference source %s hit the entry cap", source_id)
            break
    # Dedupe by url within a source; upstream lists repeat popular links.
    deduped: Dict[str, Dict[str, Any]] = {}
    for e in entries:
        deduped.setdefault(e["url"], e)
    return list(deduped.values())


def _entry_id(source_id: str, url: str) -> str:
    return "ref_" + hashlib.sha1(f"{source_id}|{url}".encode()).hexdigest()[:20]


# ── Storage ──────────────────────────────────────────────────────────

def install_source(source_id: str) -> Dict[str, Any]:
    """Fetch + parse + replace all stored entries for one source."""
    cfg = SOURCES.get(source_id)
    if not cfg:
        raise ValueError(f"unknown source: {source_id}")
    docs = [fetch_markdown(cfg["repo"], cfg["ref"], p) for p in cfg["files"]]
    entries = parse_source(source_id, docs)
    if not entries:
        raise ValueError(f"no entries parsed from {source_id} — upstream format may have changed")

    from core.database import ReferenceEntry, SessionLocal
    db = SessionLocal()
    try:
        db.query(ReferenceEntry).filter(ReferenceEntry.source == source_id).delete()
        for e in entries:
            db.add(ReferenceEntry(
                id=_entry_id(source_id, e["url"]),
                source=source_id,
                kind=cfg["kind"],
                category=(e.get("category") or "")[:200],
                title=(e.get("title") or "")[:300],
                url=e["url"][:1000],
                description=(e.get("description") or "")[:1000],
                meta=e.get("meta") or {},
            ))
        db.commit()
        return {"ok": True, "source": source_id, "installed": len(entries)}
    finally:
        db.close()


def remove_source(source_id: str) -> Dict[str, Any]:
    from core.database import ReferenceEntry, SessionLocal
    db = SessionLocal()
    try:
        n = db.query(ReferenceEntry).filter(ReferenceEntry.source == source_id).delete()
        db.commit()
        return {"ok": True, "removed": n}
    finally:
        db.close()


def source_status() -> List[Dict[str, Any]]:
    """Catalog of known sources plus how many entries each has stored."""
    from core.database import ReferenceEntry, SessionLocal
    counts: Dict[str, int] = {}
    db = SessionLocal()
    try:
        from sqlalchemy import func
        for src, n in db.query(ReferenceEntry.source, func.count(ReferenceEntry.id)) \
                        .group_by(ReferenceEntry.source).all():
            counts[src] = n
    finally:
        db.close()
    return [
        {
            "id": sid,
            "name": cfg["name"],
            "description": cfg["description"],
            "kind": cfg["kind"],
            "license": cfg["license"],
            "repo": cfg["repo"],
            "agent_actionable": cfg["agent_actionable"],
            "installed": counts.get(sid, 0),
        }
        for sid, cfg in SOURCES.items()
    ]


def search(query: str, *, source: Optional[str] = None, kind: Optional[str] = None,
           limit: int = 20) -> List[Dict[str, Any]]:
    """Substring search over title/description/category, newest-relevant first.

    Title matches rank above description matches so "weather" surfaces the
    Weather API rather than everything that merely mentions weather.
    """
    from core.database import ReferenceEntry, SessionLocal

    q = (query or "").strip()
    if not q:
        return []
    like = f"%{q}%"
    db = SessionLocal()
    try:
        rows = db.query(ReferenceEntry).filter(
            ReferenceEntry.title.ilike(like)
            | ReferenceEntry.description.ilike(like)
            | ReferenceEntry.category.ilike(like)
        )
        if source:
            rows = rows.filter(ReferenceEntry.source == source)
        if kind:
            rows = rows.filter(ReferenceEntry.kind == kind)
        found = rows.limit(max(1, min(limit, 100)) * 3).all()
    finally:
        db.close()

    ql = q.lower()

    def rank(r) -> int:
        title = (r.title or "").lower()
        if title == ql:
            return 0
        if title.startswith(ql):
            return 1
        if ql in title:
            return 2
        if ql in (r.category or "").lower():
            return 3
        return 4

    found.sort(key=rank)
    return [
        {
            "source": r.source, "kind": r.kind, "category": r.category,
            "title": r.title, "url": r.url, "description": r.description,
            "meta": r.meta or {},
        }
        for r in found[: max(1, min(limit, 100))]
    ]


def format_for_agent(results: List[Dict[str, Any]], query: str) -> str:
    """Render search hits as compact text for the agent tool result."""
    if not results:
        return (f"No reference entries matched {query!r}. "
                "Catalogs may not be installed yet (Settings → AI → Reference Library).")
    lines = [f"{len(results)} reference match(es) for {query!r}:"]
    for r in results:
        bits = [r["title"]]
        if r.get("category"):
            bits.append(f"[{r['category']}]")
        lines.append(" - " + " ".join(bits))
        if r.get("description"):
            lines.append(f"   {r['description'][:180]}")
        lines.append(f"   {r['url']}")
        meta = r.get("meta") or {}
        if meta.get("auth"):
            lines.append(f"   auth: {meta['auth']}  https: {meta.get('https', '?')}  cors: {meta.get('cors', '?')}")
    return "\n".join(lines)
