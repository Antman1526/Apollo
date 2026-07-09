"""Shared helper for CSS-source assertion tests.

The former single ``static/style.css`` was split into per-feature files under
``static/css/`` (``static/style.css`` is now just an @import shim). Tests that
grep the stylesheet for rule presence should read the concatenation of the
split files, not the shim.
"""
from pathlib import Path

_CSS_DIR = Path(__file__).resolve().parents[1] / "static" / "css"


def read_all_css() -> str:
    """Return every split stylesheet concatenated (order-independent — callers
    only assert on substring/rule presence)."""
    return "\n".join(
        p.read_text(encoding="utf-8") for p in sorted(_CSS_DIR.glob("*.css"))
    )
