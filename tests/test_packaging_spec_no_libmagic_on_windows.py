"""The Windows bundle must not collect python-magic.

PyInstaller imports every collected package while it looks for DLLs, and
`import magic` loads libmagic's database at import time — on Windows that is
a native fault or an indefinite hang (see src/upload_handler.py), which froze
the Windows release build at "Looking for dynamic libraries". The app never
imports magic on Windows, so the bundle must neither force nor follow it.
"""
from pathlib import Path

SPEC = (Path(__file__).resolve().parents[1] / "packaging" / "apollo.spec").read_text(encoding="utf-8")


def test_magic_is_only_a_hidden_import_off_windows():
    assert 'IS_WINDOWS = os.name == "nt"' in SPEC
    assert 'if not IS_WINDOWS:\n    hiddenimports.append("magic")' in SPEC
    # Not listed unconditionally anywhere else.
    assert SPEC.count('"magic"') == 2  # the guarded append + the Windows exclude


def test_magic_is_excluded_from_the_windows_analysis():
    assert '(["magic"] if IS_WINDOWS else [])' in SPEC
