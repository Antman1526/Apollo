#!/usr/bin/env python3
"""Fail when a broad exception handler has no visible failure classification."""

from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

DEFAULT_PATHS = ("app.py", "core", "routes", "services", "src")
_LOGGING_METHODS = {"debug", "info", "warning", "error", "exception", "critical"}
_CLASSIFICATION_HELPERS = {"report_exception"}


@dataclass(frozen=True)
class Finding:
    path: Path
    line: int
    handler_type: str

    def render(self, root: Path) -> str:
        return f"{self.path.relative_to(root)}:{self.line}: unclassified broad {self.handler_type} handler"


def _is_broad_exception(node: ast.ExceptHandler) -> str | None:
    if node.type is None:
        return "bare"
    names: set[str] = set()
    for candidate in ast.walk(node.type):
        if isinstance(candidate, ast.Name):
            names.add(candidate.id)
        elif isinstance(candidate, ast.Attribute):
            names.add(candidate.attr)
    if "BaseException" in names:
        return "BaseException"
    if "Exception" in names:
        return "Exception"
    return None


def _has_visible_classification(body: Iterable[ast.stmt]) -> bool:
    for statement in ast.walk(ast.Module(body=list(body), type_ignores=[])):
        if isinstance(statement, ast.Raise):
            return True
        if not isinstance(statement, ast.Call):
            continue
        function = statement.func
        if isinstance(function, ast.Name) and function.id in _CLASSIFICATION_HELPERS:
            return True
        if isinstance(function, ast.Attribute) and function.attr in _LOGGING_METHODS:
            return True
    return False


def audit(root: Path, paths: Iterable[str] = DEFAULT_PATHS) -> list[Finding]:
    """Return unclassified broad exception handlers below *root*."""

    findings: list[Finding] = []
    for relative in paths:
        candidate = root / relative
        files = [candidate] if candidate.is_file() else sorted(candidate.rglob("*.py")) if candidate.exists() else []
        for path in files:
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            except (OSError, UnicodeDecodeError, SyntaxError) as error:
                findings.append(Finding(path, getattr(error, "lineno", 1) or 1, "unparseable"))
                continue
            for handler in (node for node in ast.walk(tree) if isinstance(node, ast.ExceptHandler)):
                handler_type = _is_broad_exception(handler)
                if handler_type and not _has_visible_classification(handler.body):
                    findings.append(Finding(path, handler.lineno, handler_type))
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("paths", nargs="*", default=list(DEFAULT_PATHS))
    args = parser.parse_args()
    root = args.root.resolve()
    findings = audit(root, args.paths)
    if findings:
        print("Exception handler audit failed:")
        for finding in findings:
            print(finding.render(root))
        return 1
    print("exception-handler-audit-ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
