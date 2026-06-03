import importlib.machinery
import importlib.util
from pathlib import Path


def _load_dispatcher():
    path = Path(__file__).resolve().parent.parent / "scripts" / "apollo"
    loader = importlib.machinery.SourceFileLoader("apollo_dispatcher_under_test", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def test_is_runnable_subcommand_requires_executable_file(tmp_path):
    cli = _load_dispatcher()
    sub = tmp_path / "apollo-demo"
    sub.write_text("#!/bin/sh\n")
    sub.chmod(0o644)

    assert cli._is_runnable_subcommand(sub) is False

    sub.chmod(0o755)
    assert cli._is_runnable_subcommand(sub) is True
