from pathlib import Path

import scripts.check_dependency_locks as lock_check
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


def test_main_seeds_committed_lock_before_compile_and_accepts_unchanged_result(tmp_path, monkeypatch):
    (tmp_path / "requirements.in").write_text("package>=1.0\n", encoding="utf-8")
    (tmp_path / "requirements-dev.in").write_text("-r requirements.in\n", encoding="utf-8")
    committed = "package==1.2\n"
    (tmp_path / "requirements.txt").write_text(committed, encoding="utf-8")
    (tmp_path / "requirements-dev.txt").write_text(committed, encoding="utf-8")
    seen = []

    def fake_compile(_python, _source, output, _cwd):
        seen.append(output.read_text(encoding="utf-8"))

    monkeypatch.setattr(lock_check, "compile_lock", fake_compile)
    monkeypatch.setattr(lock_check.sys, "argv", ["check_dependency_locks", "--root", str(tmp_path)])

    assert lock_check.main() == 0
    assert seen == [committed, committed]


def test_main_rejects_drift_from_seeded_lock(tmp_path, monkeypatch, capsys):
    (tmp_path / "requirements.in").write_text("package>=1.0\n", encoding="utf-8")
    (tmp_path / "requirements-dev.in").write_text("-r requirements.in\n", encoding="utf-8")
    committed = "package==1.2\n"
    (tmp_path / "requirements.txt").write_text(committed, encoding="utf-8")
    (tmp_path / "requirements-dev.txt").write_text(committed, encoding="utf-8")
    seen = []

    def fake_compile(_python, _source, output, _cwd):
        seen.append(output.read_text(encoding="utf-8"))
        output.write_text("package==1.3\n", encoding="utf-8")

    monkeypatch.setattr(lock_check, "compile_lock", fake_compile)
    monkeypatch.setattr(lock_check.sys, "argv", ["check_dependency_locks", "--root", str(tmp_path)])

    assert lock_check.main() == 1
    assert seen == [committed]
    assert "package==1.3" in capsys.readouterr().err


def test_main_resolves_relative_interpreter_before_temp_cwd(tmp_path, monkeypatch):
    (tmp_path / "requirements.in").write_text("package>=1.0\n", encoding="utf-8")
    (tmp_path / "requirements-dev.in").write_text("-r requirements.in\n", encoding="utf-8")
    (tmp_path / "requirements.txt").write_text("package==1.2\n", encoding="utf-8")
    (tmp_path / "requirements-dev.txt").write_text("package==1.2\n", encoding="utf-8")
    interpreter = tmp_path / "tools" / "python"
    interpreter.parent.mkdir()
    interpreter.write_text("", encoding="utf-8")
    seen = []

    def fake_compile(python, _source, output, _cwd):
        seen.append(python)

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(lock_check, "compile_lock", fake_compile)
    monkeypatch.setattr(lock_check.sys, "argv", [
        "check_dependency_locks", "--root", str(tmp_path), "--python", "tools/python"
    ])

    assert lock_check.main() == 0
    assert seen == [str(interpreter.absolute()), str(interpreter.absolute())]
