"""Guards for scripts/build-windows-zip.sh.

The Windows zip used to be assembled by hand. That build applied .gitignore
patterns without their `!` negation rules, so it silently dropped tracked
runtime files — notably static/js/editor/build/*.js, which galleryEditor.js
imports, leaving the shipped gallery editor unable to load. It also had to
remember not to sweep up untracked secrets (.env) on its way past.

These tests pin the two properties that keep both classes of bug from coming
back: the archive is built from `git archive` (tracked files only), and the
script refuses to ship without the runtime files that went missing before.
"""
import os
import re
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "build-windows-zip.sh"
SOURCE = SCRIPT.read_text(encoding="utf-8")


def test_script_exists_and_is_executable():
    assert SCRIPT.is_file()
    if os.name != "nt":  # POSIX permission bits only
        assert os.access(SCRIPT, os.X_OK), "build script must be executable"


@pytest.mark.skipif(os.name == "nt", reason="requires a POSIX bash")
def test_script_syntax_is_valid():
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)


def test_archive_is_built_from_tracked_files_only():
    """`git archive` is what makes secret-leakage structurally impossible.

    A copy-based build (cp/rsync of the working tree) would pick up whatever
    untracked files happen to sit in the checkout — .env among them.
    """
    assert "git -C \"$ROOT_DIR\" archive" in SOURCE
    for copier in ("rsync", "cp -R", "cp -r"):
        assert copier not in SOURCE, f"archive must not be assembled with {copier}"


def test_secrets_and_runtime_state_are_gated_out():
    forbidden = re.search(r"FORBIDDEN_PATHS=\((.*?)\)", SOURCE, re.DOTALL)
    assert forbidden, "FORBIDDEN_PATHS list not found"
    listed = forbidden.group(1).split()
    for path in (".env", ".git", "venv", "data", "logs"):
        assert path in listed, f"{path} must be gated out of the archive"


def test_previously_dropped_runtime_files_are_required():
    """The regression guard: these are the files the hand-built zip lost."""
    required = re.search(r"REQUIRED_FILES=\((.*?)\)", SOURCE, re.DOTALL)
    assert required, "REQUIRED_FILES list not found"
    listed = required.group(1).split()
    for path in (
        "static/js/editor/build/toolbar.js",
        "static/js/editor/build/controls.js",
        "static/js/editor/build/popups.js",
        "services/hwfit/data/hf_models.json",
        "WINDOWS-SETUP.md",
        "launch-windows.ps1",
    ):
        assert path in listed, f"{path} must be required by the build gate"


def test_required_runtime_files_are_actually_tracked():
    """A required-file list that names an untracked path would fail every
    build. Verify each entry is really in the repo."""
    required = re.search(r"REQUIRED_FILES=\((.*?)\)", SOURCE, re.DOTALL)
    root = SCRIPT.parent.parent
    for path in required.group(1).split():
        assert (root / path).is_file(), f"REQUIRED_FILES names a missing path: {path}"


def test_editor_build_modules_are_imported_by_the_editor():
    """Explains WHY those .js files are required — if this ever stops being
    true the requirement can be revisited, rather than cargo-culted."""
    root = SCRIPT.parent.parent
    editor = (root / "static" / "js" / "galleryEditor.js").read_text(encoding="utf-8")
    assert "./editor/build/toolbar.js" in editor
    assert "./editor/build/controls.js" in editor
