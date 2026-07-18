"""Secret keys must be stored with the configured application state."""

from __future__ import annotations

import src.secret_storage as secret_storage


def test_key_path_follows_application_data_root(tmp_path, monkeypatch):
    monkeypatch.setenv("APOLLO_DATA_DIR", str(tmp_path / "state"))

    assert secret_storage._key_path() == tmp_path / "state" / ".app_key"
