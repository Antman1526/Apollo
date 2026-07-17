import sqlite3
import json
import subprocess
import sys
from pathlib import Path

import pytest

from src.data_migration import MigrationError, migration_status, migrate_legacy_data
from src.runtime_paths import data_root


def _legacy_with_state(tmp_path):
    legacy = tmp_path / "repo" / "data"
    legacy.mkdir(parents=True)
    (legacy / "memory.json").write_text('{"items": []}', encoding="utf-8")
    conn = sqlite3.connect(legacy / "app.db")
    conn.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()
    return legacy


def _target(tmp_path):
    return tmp_path / "home" / ".local" / "share" / "apollo"


def test_dry_run_reports_copy_without_creating_target(tmp_path):
    legacy = _legacy_with_state(tmp_path)
    target = _target(tmp_path)

    result = migrate_legacy_data(legacy, target, dry_run=True)

    assert result.status == "dry-run"
    assert result.file_count == 2
    assert not target.exists()


def test_migration_copies_verifies_and_activates_without_removing_legacy(tmp_path):
    legacy = _legacy_with_state(tmp_path)
    target = _target(tmp_path)

    result = migrate_legacy_data(legacy, target)

    assert result.status == "activated"
    assert legacy.exists()
    assert (target / "memory.json").read_text(encoding="utf-8") == '{"items": []}'
    assert result.receipt_path.exists()
    assert data_root(env={}, repo=tmp_path / "repo", platform="linux", home=tmp_path / "home") == target


def test_migration_is_idempotent_after_activation(tmp_path):
    legacy = _legacy_with_state(tmp_path)
    target = _target(tmp_path)
    migrate_legacy_data(legacy, target)

    result = migrate_legacy_data(legacy, target)

    assert result.status == "already-activated"


def test_existing_target_is_never_overwritten(tmp_path):
    legacy = _legacy_with_state(tmp_path)
    target = _target(tmp_path)
    target.mkdir(parents=True)
    (target / "different.txt").write_text("keep", encoding="utf-8")

    with pytest.raises(MigrationError, match="already exists"):
        migrate_legacy_data(legacy, target)

    assert (target / "different.txt").read_text(encoding="utf-8") == "keep"


def test_corrupt_sqlite_refuses_activation_and_keeps_legacy(tmp_path):
    legacy = _legacy_with_state(tmp_path)
    (legacy / "broken.db").write_bytes(b"not sqlite")
    target = _target(tmp_path)

    with pytest.raises(MigrationError, match="integrity"):
        migrate_legacy_data(legacy, target)

    assert legacy.exists()
    assert not target.exists()


def test_status_reports_missing_legacy_and_pending_migration(tmp_path):
    legacy = tmp_path / "repo" / "data"
    target = _target(tmp_path)

    assert migration_status(legacy, target).status == "no-legacy-data"

    legacy.mkdir(parents=True)
    assert migration_status(legacy, target).status == "pending"


def test_migration_cli_reports_dry_run_as_json(tmp_path):
    legacy = _legacy_with_state(tmp_path)
    target = _target(tmp_path)
    script = Path(__file__).resolve().parents[1] / "scripts" / "apollo-data-migrate"

    result = subprocess.run(
        [sys.executable, str(script), "--dry-run", "--source", str(legacy), "--target", str(target)],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["status"] == "dry-run"
