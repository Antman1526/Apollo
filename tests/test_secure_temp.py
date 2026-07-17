import os

from src.secure_temp import ensure_private_dir, remove_private_file, write_private_text


def _mode(path):
    return path.stat().st_mode & 0o777


def test_private_directory_and_regular_secret_file_are_owner_only(tmp_path):
    directory = ensure_private_dir(tmp_path / "runner-files")
    secret = write_private_text(directory / "token.env", "HF_TOKEN=secret\n")

    assert directory.is_dir()
    assert secret.read_text(encoding="utf-8") == "HF_TOKEN=secret\n"
    if os.name != "nt":
        assert _mode(directory) == 0o700
        assert _mode(secret) == 0o600


def test_private_writer_replaces_existing_contents(tmp_path):
    path = tmp_path / "token.env"
    write_private_text(path, "old")
    write_private_text(path, "new")

    assert path.read_text(encoding="utf-8") == "new"
    if os.name != "nt":
        assert _mode(path) == 0o600


def test_executable_private_script_is_owner_executable(tmp_path):
    script = write_private_text(tmp_path / "runner.sh", "#!/bin/sh\n", executable=True)

    if os.name != "nt":
        assert _mode(script) == 0o700


def test_remove_private_file_is_idempotent(tmp_path):
    path = write_private_text(tmp_path / "token.env", "value")

    remove_private_file(path)
    remove_private_file(path)

    assert not path.exists()
