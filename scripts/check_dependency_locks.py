#!/usr/bin/env python3
"""Fail when generated Python dependency locks differ from their inputs."""

from __future__ import annotations

import argparse
import difflib
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


LOCKS = (("requirements.in", "requirements.txt"), ("requirements-dev.in", "requirements-dev.txt"))


def compile_lock(python: str, source: Path, output: Path, cwd: Path) -> None:
    subprocess.run(
        [
            python,
            "-m",
            "piptools",
            "compile",
            "--quiet",
            "--resolver=backtracking",
            "--strip-extras",
            "--output-file",
            output.name,
            source.name,
        ],
        cwd=cwd,
        check=True,
    )


def lock_matches(committed: Path, generated: Path) -> bool:
    def normalize(path: Path) -> str:
        text = path.read_text(encoding="utf-8")
        # pip-compile records the output argument in its header. The generated
        # check target lives in a temporary directory, so normalize only that
        # non-semantic path before comparing the resolved package graph.
        text = re.sub(r"--output-file=\S+", f"--output-file={committed.name}", text)
        # The resolver also writes copied input paths into ``# via -r``
        # comments. They are non-semantic, but previous normalization missed
        # them because the leading comment text was part of the match.
        return re.sub(
            r"(?<=-r )\S*/(requirements(?:-dev)?\.in)",
            r"\1",
            text,
        )

    return normalize(committed) == normalize(generated)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--python", default=sys.executable)
    args = parser.parse_args()
    root = args.root.resolve()

    with tempfile.TemporaryDirectory(prefix="apollo-lock-check-") as temporary:
        temp_dir = Path(temporary)
        for source_name, _lock_name in LOCKS:
            shutil.copy2(root / source_name, temp_dir / source_name)
        for source_name, lock_name in LOCKS:
            source = temp_dir / source_name
            committed = root / lock_name
            if not source.exists() or not committed.exists():
                print(f"Missing dependency input or lock: {source_name}, {lock_name}", file=sys.stderr)
                return 1
            generated = temp_dir / lock_name
            try:
                compile_lock(args.python, source, generated, temp_dir)
            except subprocess.CalledProcessError as error:
                print(f"Could not compile {source_name}: exit {error.returncode}", file=sys.stderr)
                return error.returncode or 1
            if not lock_matches(committed, generated):
                diff = difflib.unified_diff(
                    committed.read_text(encoding="utf-8").splitlines(),
                    generated.read_text(encoding="utf-8").splitlines(),
                    fromfile=lock_name,
                    tofile=f"generated/{lock_name}",
                    lineterm="",
                )
                print("\n".join(diff), file=sys.stderr)
                return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
