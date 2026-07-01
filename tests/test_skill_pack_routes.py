"""Route-level checks for the skill-pack installer via FastAPI TestClient.

`fetch_pack` is monkeypatched to return a local temp dir (no network); the
admin guard is bypassed the same way existing admin-route tests do. Skipped
unless FastAPI is really installed (import chain pulls core.middleware etc.).
"""
import importlib.util
import os

import pytest


def _has_real(mod: str) -> bool:
    try:
        return importlib.util.find_spec(mod) is not None
    except (ImportError, ValueError, AttributeError):
        return False


_REAL = all(_has_real(m) for m in ("fastapi", "pydantic"))
pytestmark = pytest.mark.skipif(
    not _REAL, reason="needs real fastapi + pydantic installed"
)


class _FakeManager:
    def __init__(self, skills_root):
        self.skills_root = skills_root


def _make_pack(tmp_path):
    """A minimal pack containing one prose SKILL.md."""
    d = tmp_path / "pack" / "skills" / "humanizer"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        "---\nname: humanizer\ndescription: strip AI tells\n---\n\nOriginal body.",
        encoding="utf-8",
    )
    return str(tmp_path / "pack")


@pytest.fixture
def client(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import routes.skill_pack_routes as routes_mod

    pack_root = _make_pack(tmp_path)
    skills_root = str(tmp_path / "store")

    # Bypass admin auth (mirror existing admin-route tests) and stub the
    # network fetch so both routes discover from the local pack.
    monkeypatch.setattr(routes_mod, "require_admin", lambda request: None)
    monkeypatch.setattr(routes_mod.pi, "fetch_pack", lambda source, ref="": pack_root)

    app = FastAPI()
    app.include_router(routes_mod.setup_skill_pack_routes(_FakeManager(skills_root)))
    return TestClient(app), skills_root


def test_preview_lists_skills_without_writing(client):
    c, skills_root = client
    r = c.post("/api/skills/packs/preview",
               json={"source": "https://github.com/blader/humanizer"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    names = {s["name"]: s for s in body["skills"]}
    assert "humanizer" in names
    assert names["humanizer"]["tier"] == "prose"
    assert names["humanizer"]["description"] == "strip AI tells"
    # Preview writes nothing.
    assert not os.path.exists(skills_root)


def test_install_writes_skill_with_provenance(client):
    c, skills_root = client
    r = c.post("/api/skills/packs/install",
               json={"source": "https://github.com/blader/humanizer",
                     "ref": "abc123"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["installed"] == ["humanizer"]
    target = os.path.join(skills_root, "imported", "humanizer", "SKILL.md")
    assert os.path.exists(target)
    text = open(target, encoding="utf-8").read()
    assert "status: published" in text          # prose → published
    assert "source: imported" in text
    assert "imported_from: https://github.com/blader/humanizer" in text
    assert "imported_ref: abc123" in text
    assert "Original body." in text             # body preserved


def test_install_selects_named_skills(client):
    c, skills_root = client
    # Selecting a name that isn't present installs nothing.
    r = c.post("/api/skills/packs/install",
               json={"source": "https://github.com/blader/humanizer",
                     "names": ["does-not-exist"]})
    assert r.status_code == 200
    assert r.json()["installed"] == []
    assert not os.path.exists(os.path.join(skills_root, "imported"))
