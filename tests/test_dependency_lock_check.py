from pathlib import Path

from scripts.check_dependency_locks import lock_matches


def test_lock_matches_compares_exact_generated_bytes(tmp_path: Path):
    committed = tmp_path / "requirements.txt"
    generated = tmp_path / "generated.txt"
    committed.write_text("package==1.0\n", encoding="utf-8")
    generated.write_text("package==1.0\n", encoding="utf-8")

    assert lock_matches(committed, generated)

    generated.write_text("package==1.1\n", encoding="utf-8")
    assert not lock_matches(committed, generated)


def test_lock_matches_ignores_temporary_output_path_in_generated_header(tmp_path: Path):
    committed = tmp_path / "requirements.txt"
    generated = tmp_path / "generated.txt"
    committed.write_text("# pip-compile --output-file=requirements.txt\npackage==1.0\n", encoding="utf-8")
    generated.write_text("# pip-compile --output-file=/tmp/locks/generated.txt\npackage==1.0\n", encoding="utf-8")

    assert lock_matches(committed, generated)


def test_lock_matches_ignores_temporary_source_paths_in_via_comments(tmp_path: Path):
    committed = tmp_path / "requirements.txt"
    generated = tmp_path / "generated.txt"
    committed.write_text("package==1.0\n    # via -r requirements.in\n", encoding="utf-8")
    generated.write_text(
        "package==1.0\n    # via -r /tmp/locks/requirements.in\n",
        encoding="utf-8",
    )

    assert lock_matches(committed, generated)
