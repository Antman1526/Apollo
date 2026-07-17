#!/usr/bin/env python3
"""Reject checkout-relative runtime data paths in production Python code."""

from __future__ import annotations

import argparse
import ast
from pathlib import Path


EXEMPT_RELATIVE_PATHS = {
    Path("src/runtime_paths.py"),
    Path("src/data_migration.py"),
}
EXCLUDED_TOP_LEVEL = {"tests", "venv", ".venv", ".git", "node_modules", ".worktrees"}


def _is_data_literal(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, str) and (
        node.value == "data" or node.value.startswith("data/")
    )


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_call_name(node.value)}.{node.attr}"
    return ""


class RelativeDataPathVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.issues: list[tuple[int, str]] = []

    def visit_Call(self, node: ast.Call) -> None:
        name = _call_name(node.func)
        if node.args and _is_data_literal(node.args[0]) and name in {"Path", "os.path.join"}:
            self.issues.append((node.lineno, name))
        self.generic_visit(node)


def find_issues(root: Path) -> list[tuple[Path, int, str]]:
    issues: list[tuple[Path, int, str]] = []
    for path in sorted(root.rglob("*.py")):
        relative = path.relative_to(root)
        if relative.parts and relative.parts[0] in EXCLUDED_TOP_LEVEL:
            continue
        if relative in EXEMPT_RELATIVE_PATHS:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError):
            continue
        visitor = RelativeDataPathVisitor()
        visitor.visit(tree)
        issues.extend((relative, line, kind) for line, kind in visitor.issues)
    return issues


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    issues = find_issues(args.root.resolve())
    for relative, line, kind in issues:
        print(f"{relative}:{line}: checkout-relative runtime data path via {kind}")
    return 1 if issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
