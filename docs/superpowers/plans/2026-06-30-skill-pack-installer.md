# Skill-Pack Installer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Install Agent Skills into Apollo from a GitHub repo (or uploaded zip): discover every `SKILL.md`, normalize frontmatter into Apollo's schema with provenance, quarantine script-backed skills (never auto-run), install into `data/skills/`, and reindex.

**Architecture:** A new `services/skills/pack_installer.py` whose discover/classify/normalize/install logic is **pure and unit-tested** (operates on temp dirs, no network), plus a thin SSRF-guarded `fetch_pack` (reuses `src/search/content.py`'s `_get_public_url`). A new admin-gated route pair and one Skills-panel UI action. No changes to the skill format or retrieval.

**Tech Stack:** Python 3.11 / FastAPI, `httpx`, `pytest` (`asyncio_mode=auto`), vanilla-JS frontend.

**Spec:** `docs/superpowers/specs/2026-06-30-skill-pack-installer-design.md`

**Worktree + interpreter:** Work in `/Users/Antman/Apollo-skills-wt`. The venv lives in the main checkout; run tests with:
`cd /Users/Antman/Apollo-skills-wt && /Users/Antman/Apollo/venv/bin/python -m pytest <path> -q`

---

## Data shapes (used across tasks)

```python
# A skill discovered in a fetched pack.
@dataclass
class FoundSkill:
    name: str            # slug (dir name, slugified)
    description: str
    tier: str            # "prose" | "script"
    rel_dir: str         # path of the skill dir relative to the pack root
    frontmatter: dict    # parsed YAML frontmatter of the source SKILL.md
    body: str            # markdown body of the source SKILL.md (verbatim)
    files: list          # file paths (relative to the skill dir) other than SKILL.md
    error: str = ""      # non-empty if the SKILL.md failed to parse (skipped)

# Options for normalize/install.
@dataclass
class InstallOpts:
    category: str        # target Apollo category
    owner: str | None
    source_url: str      # provenance: where the pack came from
    source_ref: str      # provenance: commit/ref/branch
    now_iso: str         # provenance timestamp (passed in; no clock in pure code)
    overwrite: bool = False
```

---

## Task 1: Trust-tier classifier (pure)

`classify_tier(skill_dir)` returns `"script"` if a skill folder ships executable
code / hooks / MCP config, else `"prose"`.

**Files:**
- Create: `services/skills/__init__.py` (empty), `services/skills/pack_installer.py`
- Test: `tests/test_pack_installer.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_pack_installer.py`:

```python
import os
from services.skills.pack_installer import classify_tier


def _mk(tmp_path, name, files):
    d = tmp_path / name
    d.mkdir(parents=True)
    for rel, content in files.items():
        p = d / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    return str(d)


def test_prose_only_skill_is_prose(tmp_path):
    d = _mk(tmp_path, "humanizer", {"SKILL.md": "---\nname: humanizer\n---\nbody"})
    assert classify_tier(d) == "prose"


def test_scripts_dir_makes_it_script(tmp_path):
    d = _mk(tmp_path, "docx", {"SKILL.md": "x", "scripts/office.py": "print(1)"})
    assert classify_tier(d) == "script"


def test_mcp_json_makes_it_script(tmp_path):
    d = _mk(tmp_path, "conn", {"SKILL.md": "x", ".mcp.json": "{}"})
    assert classify_tier(d) == "script"


def test_loose_code_file_makes_it_script(tmp_path):
    d = _mk(tmp_path, "hook", {"SKILL.md": "x", "run.sh": "echo hi"})
    assert classify_tier(d) == "script"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/Antman/Apollo-skills-wt && /Users/Antman/Apollo/venv/bin/python -m pytest tests/test_pack_installer.py -q`
Expected: FAIL — `ModuleNotFoundError: services.skills.pack_installer`.

- [ ] **Step 3: Write minimal implementation**

Create `services/skills/__init__.py` (empty). Create `services/skills/pack_installer.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/Antml/Apollo-skills-wt && /Users/Antman/Apollo/venv/bin/python -m pytest tests/test_pack_installer.py -q`
(Use the correct path `/Users/Antman/Apollo-skills-wt`.)
Expected: PASS — 4 tests.

- [ ] **Step 5: Commit**

```bash
cd /Users/Antman/Apollo-skills-wt
git add services/skills/__init__.py services/skills/pack_installer.py tests/test_pack_installer.py
git commit -m "feat(skills): trust-tier classifier for imported skill packs"
```

---

## Task 2: Discover skills in a pack (pure)

`discover_skills(pack_root)` walks for every `SKILL.md`, parses it (reusing
`skill_format.parse_frontmatter`), classifies its tier, and returns a list of
`FoundSkill`. A malformed `SKILL.md` becomes a `FoundSkill` with `error` set (not
fatal).

**Files:**
- Modify: `services/skills/pack_installer.py`
- Test: `tests/test_pack_installer.py`

- [ ] **Step 1: Write the failing test** (append)

```python
from services.skills.pack_installer import discover_skills, FoundSkill


def test_discovers_multiple_skills_with_tiers(tmp_path):
    (tmp_path / "skills/humanizer").mkdir(parents=True)
    (tmp_path / "skills/humanizer/SKILL.md").write_text(
        "---\nname: humanizer\ndescription: strip AI tells\n---\n\nBody here.")
    (tmp_path / "skills/docx").mkdir(parents=True)
    (tmp_path / "skills/docx/SKILL.md").write_text(
        "---\nname: docx\ndescription: word docs\n---\n\nUse pandoc.")
    (tmp_path / "skills/docx/scripts").mkdir()
    (tmp_path / "skills/docx/scripts/x.py").write_text("print(1)")

    found = {f.name: f for f in discover_skills(str(tmp_path))}
    assert set(found) == {"humanizer", "docx"}
    assert found["humanizer"].tier == "prose"
    assert found["humanizer"].description == "strip AI tells"
    assert "Body here." in found["humanizer"].body
    assert found["docx"].tier == "script"


def test_malformed_skill_is_reported_not_fatal(tmp_path):
    (tmp_path / "s/good").mkdir(parents=True)
    (tmp_path / "s/good/SKILL.md").write_text("---\nname: good\n---\nok")
    (tmp_path / "s/bad").mkdir(parents=True)
    (tmp_path / "s/bad/SKILL.md").write_text("\x00 not valid frontmatter")
    found = {f.name: f for f in discover_skills(str(tmp_path))}
    assert "good" in found
    # bad still returns a FoundSkill (named from its dir) but may carry an error
    # or an empty description; the whole walk must not raise.
```

- [ ] **Step 2: Run to verify it fails**

Run the test file. Expected: FAIL — `cannot import name 'discover_skills'`.

- [ ] **Step 3: Implement** (append to `pack_installer.py`)

```python
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
```

- [ ] **Step 4: Run to verify it passes.** Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add services/skills/pack_installer.py tests/test_pack_installer.py
git commit -m "feat(skills): discover SKILL.md files in a fetched pack"
```

---

## Task 3: Normalize a found skill to an Apollo SKILL.md (pure)

`render_skill_md(found, opts)` produces the target `SKILL.md` text: Apollo
frontmatter (name/description/category/status-by-tier/source/owner + provenance)
followed by the **verbatim original body**. Reuses `emit_frontmatter`.

**Files:**
- Modify: `services/skills/pack_installer.py`
- Test: `tests/test_pack_installer.py`

- [ ] **Step 1: Write the failing test** (append)

```python
from services.skills.pack_installer import render_skill_md, InstallOpts


def _opts(**kw):
    base = dict(category="writing", owner="me", source_url="https://github.com/blader/humanizer",
                source_ref="abc123", now_iso="2026-06-30T00:00:00Z", overwrite=False)
    base.update(kw)
    return InstallOpts(**base)


def test_render_prose_skill_is_published_with_provenance(tmp_path):
    f = FoundSkill("humanizer", "strip AI tells", "prose", "skills/humanizer",
                   {"name": "humanizer", "description": "strip AI tells", "license": "MIT"},
                   "\n\nOriginal body **kept**.\n", [])
    md = render_skill_md(f, _opts())
    assert md.startswith("---\n")
    assert "status: published" in md
    assert "source: imported" in md
    assert "category: writing" in md
    assert "imported_from: https://github.com/blader/humanizer" in md
    assert "imported_ref: abc123" in md
    assert "Original body **kept**." in md   # body preserved verbatim


def test_render_script_skill_is_draft(tmp_path):
    f = FoundSkill("docx", "word docs", "script", "skills/docx",
                   {"name": "docx"}, "Use pandoc.", ["scripts/x.py"])
    md = render_skill_md(f, _opts(category="office"))
    assert "status: draft" in md   # quarantined
```

- [ ] **Step 2: Run to verify it fails.** Expected: `cannot import name 'render_skill_md'`.

- [ ] **Step 3: Implement** (append)

```python
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
    fm["imported_from"] = opts.source_url
    fm["imported_ref"] = opts.source_ref
    fm["imported_at"] = opts.now_iso
    fm["imported_tier"] = found.tier
    body = (found.body or "").strip("\n")
    return f"---\n{emit_frontmatter(fm)}\n---\n\n{body}\n"
```

- [ ] **Step 4: Run to verify it passes.** Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/skills/pack_installer.py tests/test_pack_installer.py
git commit -m "feat(skills): normalize imported skill to Apollo SKILL.md + provenance"
```

---

## Task 4: Install found skills to the store dir (pure, temp-dir)

`install_skills(found, opts, skills_root)` writes each selected skill to
`skills_root/<category>/<name>/SKILL.md` (+ copies a script-backed skill's own
files as inert), handling collisions per `opts.overwrite`. Returns a summary.

**Files:**
- Modify: `services/skills/pack_installer.py`
- Test: `tests/test_pack_installer.py`

- [ ] **Step 1: Write the failing test** (append)

```python
from services.skills.pack_installer import install_skills


def test_install_writes_files_and_reports(tmp_path):
    pack = tmp_path / "pack/skills/humanizer"
    pack.mkdir(parents=True)
    (pack / "SKILL.md").write_text("---\nname: humanizer\ndescription: d\n---\nBody")
    found = discover_skills(str(tmp_path / "pack"))
    root = str(tmp_path / "store")
    res = install_skills(found, _opts(category="writing"), root, src_root=str(tmp_path / "pack"))
    target = os.path.join(root, "writing", "humanizer", "SKILL.md")
    assert os.path.exists(target)
    assert "status: published" in open(target).read()
    assert res["installed"] == ["humanizer"]


def test_install_skips_existing_without_overwrite(tmp_path):
    pack = tmp_path / "pack/skills/humanizer"
    pack.mkdir(parents=True)
    (pack / "SKILL.md").write_text("---\nname: humanizer\ndescription: d\n---\nBody")
    found = discover_skills(str(tmp_path / "pack"))
    root = str(tmp_path / "store")
    os.makedirs(os.path.join(root, "writing", "humanizer"))
    open(os.path.join(root, "writing", "humanizer", "SKILL.md"), "w").write("existing")
    res = install_skills(found, _opts(category="writing", overwrite=False), root, src_root=str(tmp_path / "pack"))
    assert res["skipped"] == ["humanizer"]
    assert open(os.path.join(root, "writing", "humanizer", "SKILL.md")).read() == "existing"
```

- [ ] **Step 2: Run to verify it fails.** Expected: `cannot import name 'install_skills'`.

- [ ] **Step 3: Implement** (append)

```python
import shutil


def install_skills(found_list, opts, skills_root, src_root=""):
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
```

- [ ] **Step 4: Run to verify it passes.** Expected: PASS. Then run the WHOLE file:
`/Users/Antman/Apollo/venv/bin/python -m pytest tests/test_pack_installer.py -q` → all green.

- [ ] **Step 5: Commit**

```bash
git add services/skills/pack_installer.py tests/test_pack_installer.py
git commit -m "feat(skills): install discovered skills into the store with collision handling"
```

---

## Task 5: Fetch a pack (GitHub tarball / zip) — SSRF + zip-bomb guarded

`fetch_pack(source)` downloads a GitHub repo tarball via the existing
`_get_public_url` guard (or accepts a local zip path), extracts it to a temp dir
with path-traversal + size caps, and returns the extracted root. The extraction
guards are unit-tested; the network download is verified manually.

**Files:**
- Modify: `services/skills/pack_installer.py`
- Test: `tests/test_pack_installer.py`

- [ ] **Step 1: Write the failing test for the extraction guard** (append)

```python
import io, tarfile
from services.skills.pack_installer import safe_extract_tar


def test_safe_extract_rejects_path_traversal(tmp_path):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        data = b"x"
        info = tarfile.TarInfo("../evil.txt"); info.size = len(data)
        t.addfile(info, io.BytesIO(data))
    buf.seek(0)
    import pytest
    with pytest.raises(ValueError):
        safe_extract_tar(tarfile.open(fileobj=buf, mode="r:gz"), str(tmp_path), max_bytes=1000)


def test_safe_extract_ok(tmp_path):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        data = b"hello"
        info = tarfile.TarInfo("repo/skills/x/SKILL.md"); info.size = len(data)
        t.addfile(info, io.BytesIO(data))
    buf.seek(0)
    root = safe_extract_tar(tarfile.open(fileobj=buf, mode="r:gz"), str(tmp_path), max_bytes=10_000)
    assert os.path.exists(os.path.join(root, "repo/skills/x/SKILL.md"))
```

- [ ] **Step 2: Run to verify it fails.** Expected: `cannot import name 'safe_extract_tar'`.

- [ ] **Step 3: Implement** (append)

```python
import tarfile
import tempfile
from urllib.parse import urlparse


def safe_extract_tar(tar, dest: str, max_bytes: int) -> str:
    total = 0
    for m in tar.getmembers():
        # Reject absolute paths and traversal.
        if m.name.startswith("/") or ".." in m.name.split("/"):
            raise ValueError(f"unsafe path in archive: {m.name}")
        total += max(0, m.size)
        if total > max_bytes:
            raise ValueError("archive too large")
    tar.extractall(dest)  # nosec - members validated above
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
```

- [ ] **Step 4: Run to verify it passes.** Expected: the two `safe_extract_tar` tests PASS. (Network `fetch_pack` is exercised manually in Task 8.)

- [ ] **Step 5: Commit**

```bash
git add services/skills/pack_installer.py tests/test_pack_installer.py
git commit -m "feat(skills): SSRF- and zip-bomb-guarded GitHub pack fetch"
```

---

## Task 6: Preview + install routes (admin-gated)

Add `POST /api/skills/packs/preview` (fetch + discover, no writes) and
`POST /api/skills/packs/install` (install a confirmed selection + provenance).

**Files:**
- Create: `routes/skill_pack_routes.py`
- Modify: `app.py` (register the router)
- Test: `tests/test_skill_pack_routes.py`

- [ ] **Step 1: Locate the skills-manager wiring + data dir**

Run: `grep -n "setup_skills_routes\|skills_manager\|SkillsManager(" app.py` and
`grep -n "skills_root\|self.data_dir" services/memory/skills.py`.
Confirm how `skills_manager` is constructed and how to reach its `skills_root`
(e.g. `skills_manager.store.skills_root` or a `data_dir`). Use that in the route.

- [ ] **Step 2: Write the route file**

Create `routes/skill_pack_routes.py`:

```python
"""Admin routes to install Agent Skills packs from GitHub / zip."""
import datetime

from fastapi import APIRouter, Request
from pydantic import BaseModel

from core.middleware import require_admin
from services.skills import pack_installer as pi


class PreviewRequest(BaseModel):
    source: str
    ref: str = ""


class InstallRequest(BaseModel):
    source: str
    ref: str = ""
    category: str = "imported"
    names: list = []          # skill names to install; empty = all discovered
    overwrite: bool = False


def setup_skill_pack_routes(skills_manager) -> APIRouter:
    router = APIRouter(prefix="/api/skills/packs", tags=["skills"])

    def _skills_root():
        # Resolve from the manager wired in Step 1.
        return skills_manager.store.skills_root

    @router.post("/preview")
    async def preview(request: Request, body: PreviewRequest):
        require_admin(request)
        root = pi.fetch_pack(body.source, body.ref)
        found = pi.discover_skills(root)
        return {"ok": True, "root": root, "skills": [
            {"name": f.name, "description": f.description, "tier": f.tier,
             "rel_dir": f.rel_dir, "error": f.error} for f in found]}

    @router.post("/install")
    async def install(request: Request, body: InstallRequest):
        require_admin(request)
        root = pi.fetch_pack(body.source, body.ref)
        found = pi.discover_skills(root)
        if body.names:
            found = [f for f in found if f.name in set(body.names)]
        opts = pi.InstallOpts(
            category=body.category,
            owner=getattr(request.state, "user", None) or None,
            source_url=body.source, source_ref=body.ref or "HEAD",
            now_iso=datetime.datetime.utcnow().isoformat() + "Z",
            overwrite=body.overwrite,
        )
        res = pi.install_skills(found, opts, _skills_root(), src_root=root)
        return {"ok": True, **res}

    return router
```

- [ ] **Step 3: Register in app.py**

After the `setup_skills_routes` registration (found in Step 1, ~`app.py:577`),
add a `RouterSpec` for the pack routes, importing `setup_skill_pack_routes`:

```python
from routes.skill_pack_routes import setup_skill_pack_routes
# ... in the RouterSpec list:
RouterSpec("SkillPacks", setup_skill_pack_routes, args=(skills_manager,)),
```

(Match the exact RouterSpec construction pattern used for the skills route.)

- [ ] **Step 4: Route test (mock fetch, no network)**

Create `tests/test_skill_pack_routes.py` — build a fake `skills_manager` with a
tmp `store.skills_root`, monkeypatch `pack_installer.fetch_pack` to return a
tmp dir containing a prose `SKILL.md`, drive the router with Starlette's
`TestClient`, assert `/preview` lists the skill (no file written) and `/install`
writes `skills_root/imported/<name>/SKILL.md`. Bypass `require_admin` by
monkeypatching it to a no-op (mirror how existing route tests handle admin).

- [ ] **Step 5: Run tests + smoke import**

Run: `/Users/Antman/Apollo/venv/bin/python -m pytest tests/test_skill_pack_routes.py -q`
and `/Users/Antman/Apollo/venv/bin/python -c "import app"` (app imports cleanly with the new router).
Expected: PASS / no import error.

- [ ] **Step 6: Commit**

```bash
git add routes/skill_pack_routes.py app.py tests/test_skill_pack_routes.py
git commit -m "feat(skills): admin routes to preview and install skill packs"
```

---

## Task 7: Skills-panel UI — "Install skill pack"

Add an "Install skill pack" button to the Skills panel that opens a modal:
source URL → Preview (lists skills + tier badges + checkboxes) → Install.

**Files:**
- Modify: `static/index.html` (Skills panel header near `id="skills-list"` / the `data-memory-panel="skills"` header ~line 340-384), `static/js/skills.js`

- [ ] **Step 1: Add the button + modal markup**

In the Skills panel header (run `grep -n "skills-count-h2\|skills-list\|data-memory-panel=\"skills\"" static/index.html`), add a button `id="install-skill-pack-btn"` mirroring the existing header controls, and a hidden modal `id="skill-pack-modal"` with: a text input `id="skill-pack-source"`, a "Preview" button, a results container `id="skill-pack-results"`, and an "Install selected" button.

- [ ] **Step 2: Wire it in skills.js**

Add handlers (mirror the existing `fetch('/api/skills')` pattern):
- Preview → `POST /api/skills/packs/preview {source}` → render each returned skill as a row with a checkbox, name, description, and a tier badge (prose = neutral, script = warning "ships code — review"). Script rows default unchecked.
- Install selected → `POST /api/skills/packs/install {source, category, names, overwrite}` → on success, toast the installed/skipped counts and refresh the skills list (call the existing list-refresh function).

- [ ] **Step 3: Verify in-app**

Reload Apollo (main checkout will run this branch only after the worktree is
merged; for now verify by serving the worktree, or defer to Task 8). Open the
Skills panel → the "Install skill pack" button is present and the modal opens.

- [ ] **Step 4: Commit**

```bash
git add static/index.html static/js/skills.js
git commit -m "feat(skills): Skills-panel UI to install a skill pack from a repo"
```

---

## Task 8: End-to-end verification (real repo)

**Files:** none (verification only)

- [ ] **Step 1** From the worktree, launch Apollo on a spare port using the main venv:
`cd /Users/Antman/Apollo-skills-wt && /Users/Antman/Apollo/venv/bin/python -m uvicorn app:app --host 127.0.0.1 --port 7861`
- [ ] **Step 2** In the Skills panel, Install pack from `https://github.com/blader/humanizer`. Preview lists `humanizer` as tier=prose. Install it.
- [ ] **Step 3** Confirm `data/skills/imported/humanizer/SKILL.md` exists with `status: published`, `source: imported`, and `imported_from`/`imported_ref` provenance, and that the body is preserved.
- [ ] **Step 4** Install `https://github.com/anthropics/skills` — confirm prose skills (frontend-design) come in published while script-backed ones (docx/pdf/xlsx) are `status: draft` (quarantined) with their scripts copied but inert.
- [ ] **Step 5** Confirm the imported skills appear in the Skills list and are retrievable (published ones surface to the agent; drafts do not until promoted).

---

## Self-Review

**Spec coverage:** fetch (Task 5) · discover (Task 2) · trust tiers/quarantine (Tasks 1,3) · normalize+provenance (Task 3) · install+collision (Task 4) · SSRF guard (Task 5) · zip-bomb/traversal (Task 5) · preview-before-commit + routes (Task 6) · UI (Task 7) · reindex (lazy-on-read — no code needed, verified Task 8) · never-execute (Tasks 1/4 copy inert, never run). ✓

**Placeholder scan:** none — pure-function tasks carry full code; route/UI tasks carry full code + grep anchors for the two lookups that depend on live wiring (`skills_manager` construction, Skills-panel header).

**Name consistency:** `FoundSkill`/`InstallOpts` fields, `classify_tier`/`discover_skills`/`render_skill_md`/`install_skills`/`safe_extract_tar`/`fetch_pack`, and route paths `/api/skills/packs/{preview,install}` are consistent across tasks and tests. One correction to make while implementing: Task 1 Step 4 has a typo (`Antml`) — the correct path is `/Users/Antman/Apollo-skills-wt`.

**Deferred (not this plan):** installing a pack's MCP connectors, pack update/versioning, and the curated starter/domain imports (those are follow-on plans that just *call* this installer).
