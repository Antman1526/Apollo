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


def test_safe_extract_rejects_symlink_escape(tmp_path):
    # A clean-named symlink whose target escapes the destination must be rejected
    # (name-only checks miss this; filter="data" catches it). Arbitrary-file-write
    # primitive otherwise. SECURITY (C1).
    import pytest
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        link = tarfile.TarInfo("repo/evil")
        link.type = tarfile.SYMTYPE
        link.linkname = "/tmp/apollo-outside-target"
        t.addfile(link)
        data = b"OWNED"
        payload = tarfile.TarInfo("repo/evil/payload.txt"); payload.size = len(data)
        t.addfile(payload, io.BytesIO(data))
    buf.seek(0)
    with pytest.raises(ValueError):
        safe_extract_tar(tarfile.open(fileobj=buf, mode="r:gz"), str(tmp_path), max_bytes=10_000)


def test_safe_extract_rejects_too_many_members(tmp_path):
    # Member-count cap guards against inode exhaustion. SECURITY (I2).
    import pytest
    from services.skills.pack_installer import _MAX_PACK_MEMBERS
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        for i in range(_MAX_PACK_MEMBERS + 1):
            info = tarfile.TarInfo(f"repo/f{i}.txt"); info.size = 1
            t.addfile(info, io.BytesIO(b"x"))
    buf.seek(0)
    with pytest.raises(ValueError):
        safe_extract_tar(tarfile.open(fileobj=buf, mode="r:gz"), str(tmp_path), max_bytes=10_000_000)


def test_install_sanitizes_category_no_escape(tmp_path):
    # A crafted category must not escape skills_root. SECURITY (I1).
    pack = tmp_path / "pack/skills/humanizer"
    pack.mkdir(parents=True)
    (pack / "SKILL.md").write_text("---\nname: humanizer\ndescription: d\n---\nBody")
    found = discover_skills(str(tmp_path / "pack"))
    root = tmp_path / "store"
    res = install_skills(found, _opts(category="../../ESCAPED"), str(root), src_root=str(tmp_path / "pack"))
    assert res["installed"] == ["humanizer"]
    # Nothing written outside skills_root.
    assert not (tmp_path / "ESCAPED").exists()
    hits = [os.path.join(r, "SKILL.md") for r, _d, fs in os.walk(str(root)) if "SKILL.md" in fs]
    assert hits, "skill should be written under skills_root"
    real_root = os.path.realpath(str(root))
    assert all(os.path.realpath(h).startswith(real_root) for h in hits)
