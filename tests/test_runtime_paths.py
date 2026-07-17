from pathlib import Path
import os
import subprocess
import sys

from src.runtime_paths import data_path, data_root, legacy_data_root, platform_data_root


def test_platform_data_root_uses_macos_application_support():
    assert platform_data_root(platform="darwin", env={}, home=Path("/Users/alice")) == Path(
        "/Users/alice/Library/Application Support/Apollo"
    )


def test_platform_data_root_uses_windows_local_app_data():
    assert platform_data_root(
        platform="win32",
        env={"LOCALAPPDATA": r"C:\\Users\\alice\\AppData\\Local"},
        home=Path(r"C:\\Users\\alice"),
    ) == Path(r"C:\\Users\\alice\\AppData\\Local") / "Apollo"


def test_platform_data_root_honors_xdg_then_linux_default():
    home = Path("/home/alice")

    assert platform_data_root(platform="linux", env={"XDG_DATA_HOME": "/mnt/state"}, home=home) == Path("/mnt/state/apollo")
    assert platform_data_root(platform="linux", env={}, home=home) == Path("/home/alice/.local/share/apollo")


def test_explicit_apollo_data_dir_wins_over_legacy_data_dir(tmp_path):
    legacy = tmp_path / "legacy"
    explicit = tmp_path / "explicit"

    assert data_root(
        env={"APOLLO_DATA_DIR": str(explicit), "DATA_DIR": str(legacy)},
        repo=tmp_path / "repo",
        platform="linux",
        home=tmp_path / "home",
    ) == explicit


def test_legacy_data_dir_environment_remains_supported(tmp_path):
    configured = tmp_path / "configured-data"

    assert data_root(
        env={"DATA_DIR": str(configured)},
        repo=tmp_path / "repo",
        platform="linux",
        home=tmp_path / "home",
    ) == configured


def test_existing_legacy_directory_is_used_until_migration_activates(tmp_path):
    repo = tmp_path / "repo"
    legacy = legacy_data_root(repo)
    legacy.mkdir(parents=True)

    assert data_root(env={}, repo=repo, platform="linux", home=tmp_path / "home") == legacy


def test_data_path_is_absolute_and_joins_under_resolved_root(tmp_path):
    root = tmp_path / "state"

    assert data_path(
        "deep_research", "report.json",
        env={"APOLLO_DATA_DIR": str(root)},
        repo=tmp_path / "repo",
        platform="linux",
        home=tmp_path / "home",
    ) == root / "deep_research" / "report.json"


def test_constants_and_config_follow_explicit_data_root(tmp_path):
    root = tmp_path / "application-state"
    env = {**os.environ, "APOLLO_DATA_DIR": str(root)}
    code = (
        "from src.constants import DATA_DIR, UPLOAD_DIR; "
        "from src.config import DataConfig; "
        "assert DATA_DIR == r'" + str(root) + "'; "
        "assert UPLOAD_DIR == r'" + str(root / "uploads") + "'; "
        "assert DataConfig().data_dir == __import__('pathlib').Path(r'" + str(root) + "')"
    )

    result = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
