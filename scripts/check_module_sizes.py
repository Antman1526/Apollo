#!/usr/bin/env python3
"""Keep JavaScript feature modules small and ratchet existing entry points down."""

from __future__ import annotations

import argparse
from pathlib import Path

# Existing entry points are intentionally grandfathered at their measured
# baseline. Moving code out must reduce these values in a later commit; adding
# code above a baseline fails this check. Every other module has the hard cap.
BASELINES = {
    "admin.js": 2092, "calendar.js": 3348, "chat.js": 4584,
    "chatRenderer.js": 2105, "cookbook-hwfit.js": 1790, "cookbook.js": 1965,
    "cookbookRunning.js": 3218, "cookbookServe.js": 2086, "document.js": 9453,
    "documentLibrary.js": 3365, "emailLibrary.js": 5217, "gallery.js": 2835,
    "galleryEditor.js": 3798, "modalManager.js": 1550, "notes.js": 5011,
    "sessions.js": 3135, "settings.js": 5043, "skills.js": 2038,
    "slashCommands.js": 5940, "tasks.js": 2709, "theme.js": 2160,
}
MAX_NEW_MODULE_LINES = 1500


def line_count(path: Path) -> int:
    return len(path.read_text(encoding="utf-8").splitlines())


def check_modules(static_js: Path) -> list[str]:
    failures: list[str] = []
    for path in sorted(static_js.rglob("*.js")):
        relative = path.relative_to(static_js).as_posix()
        count = line_count(path)
        baseline = BASELINES.get(relative)
        if baseline is not None and count > baseline:
            failures.append(f"{relative}: {count} lines exceeds ratchet baseline {baseline}")
        elif baseline is None and count > MAX_NEW_MODULE_LINES:
            failures.append(f"{relative}: {count} lines exceeds new-module limit {MAX_NEW_MODULE_LINES}")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--static-js", type=Path, default=Path(__file__).resolve().parents[1] / "static" / "js")
    args = parser.parse_args()
    failures = check_modules(args.static_js)
    if failures:
        print("module-size-check-failed")
        print("\n".join(failures))
        return 1
    print("module-size-check-ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
