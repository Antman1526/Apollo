"""Owner-only temporary files for short-lived secrets and runner scripts."""

from __future__ import annotations

import os
import secrets
from pathlib import Path


def ensure_private_dir(path: Path) -> Path:
    """Create ``path`` with owner-only access and return it."""
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name != "nt":
        path.chmod(0o700)
    return path


def write_private_text(path: Path, content: str, *, executable: bool = False) -> Path:
    """Atomically replace ``path`` with owner-only UTF-8 content.

    The temporary file receives its restrictive mode at creation time, then is
    atomically moved into place. This never exposes new content through an old
    file that had broader permissions.
    """
    path = Path(path)
    ensure_private_dir(path.parent)
    mode = 0o700 if executable else 0o600
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        if os.name != "nt":
            temporary.chmod(mode)
        os.replace(temporary, path)
    except Exception:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return path


def remove_private_file(path: Path | None) -> None:
    """Remove a private staging file without turning cleanup into a failure."""
    if path is None:
        return
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        pass
