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
