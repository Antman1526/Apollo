"""Helpers for tests that need real modules after collection-time stubs."""

import importlib
import sys
import types


def _drop_module(name: str) -> None:
    sys.modules.pop(name, None)
    if "." not in name:
        return
    parent_name, _, child_name = name.rpartition(".")
    parent = sys.modules.get(parent_name)
    if parent is not None and hasattr(parent, child_name):
        delattr(parent, child_name)


def import_real_module(name: str):
    """Import a real on-disk module, replacing lightweight test stubs."""
    mod = sys.modules.get(name)
    if mod is not None and not _is_real_module(mod):
        _drop_module(name)
        parent_name = name.rpartition(".")[0]
        parent = sys.modules.get(parent_name) if parent_name else None
        if parent is not None and not _is_real_module(parent):
            _drop_module(parent_name)
    mod = importlib.import_module(name)
    if "." in name:
        parent_name, _, child_name = name.rpartition(".")
        parent = sys.modules.get(parent_name)
        if parent is not None:
            setattr(parent, child_name, mod)
    return mod


def _is_real_module(mod) -> bool:
    return isinstance(mod, types.ModuleType) and isinstance(getattr(mod, "__file__", None), str)
