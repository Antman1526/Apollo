"""Regression tests for the Windows launcher command-line contract."""
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path

import pytest


def _launcher():
    path = Path(__file__).resolve().parents[1] / "scripts" / "windows_launcher.py"
    loader = SourceFileLoader("apollo_windows_launcher", str(path))
    spec = spec_from_loader(loader.name, loader)
    assert spec is not None
    module = module_from_spec(spec)
    loader.exec_module(module)
    return module


def test_help_exits_before_launcher_side_effects(capsys):
    launcher = _launcher()

    with pytest.raises(SystemExit) as exited:
        launcher.main(["--help"])

    assert exited.value.code == 0
    assert "Start Apollo" in capsys.readouterr().out


def test_version_exits_before_launcher_side_effects(capsys):
    launcher = _launcher()

    with pytest.raises(SystemExit) as exited:
        launcher.main(["--version"])

    assert exited.value.code == 0
    assert "Apollo Windows launcher" in capsys.readouterr().out
