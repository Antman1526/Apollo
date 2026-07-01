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
