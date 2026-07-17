"""Copy-verify activation of checkout-local Apollo data into user storage."""

from __future__ import annotations

import json
import shutil
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from src.runtime_paths import migration_receipt_path


class MigrationError(RuntimeError):
    """Raised when a migration cannot safely activate its target."""


@dataclass(frozen=True)
class MigrationResult:
    status: str
    source: Path
    target: Path
    file_count: int = 0
    total_bytes: int = 0
    receipt_path: Path | None = None


def _manifest(root: Path) -> list[dict[str, int | str]]:
    entries: list[dict[str, int | str]] = []
    for path in sorted(root.rglob("*")):
        if path.is_file():
            entries.append({"path": str(path.relative_to(root)), "bytes": path.stat().st_size})
    return entries


def _verify_sqlite_files(root: Path) -> None:
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in {".db", ".sqlite", ".sqlite3"}:
            continue
        try:
            with sqlite3.connect(path) as connection:
                value = connection.execute("PRAGMA integrity_check").fetchone()[0]
        except sqlite3.DatabaseError as exc:
            raise MigrationError(f"SQLite integrity check failed for {path.name}") from exc
        if value != "ok":
            raise MigrationError(f"SQLite integrity check failed for {path.name}: {value}")


def _active_receipt(target: Path) -> Path | None:
    receipt = migration_receipt_path(target)
    if not receipt.exists():
        return None
    try:
        payload = json.loads(receipt.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if payload.get("status") == "activated" and payload.get("target") == str(target) and target.exists():
        return receipt
    return None


def migration_status(source: Path, target: Path) -> MigrationResult:
    """Describe whether a legacy-root migration is needed without changing state."""
    source = Path(source)
    target = Path(target)
    receipt = _active_receipt(target)
    if receipt:
        entries = _manifest(target)
        return MigrationResult("already-activated", source, target, len(entries), sum(int(item["bytes"]) for item in entries), receipt)
    if not source.exists():
        return MigrationResult("no-legacy-data", source, target)
    entries = _manifest(source)
    return MigrationResult("pending", source, target, len(entries), sum(int(item["bytes"]) for item in entries))


def migrate_legacy_data(source: Path, target: Path, *, dry_run: bool = False) -> MigrationResult:
    """Copy legacy state, verify it, then atomically activate the new root.

    Source data is never removed. A target directory that already exists without
    a valid receipt is treated as a collision rather than overwritten.
    """
    source = Path(source)
    target = Path(target)
    state = migration_status(source, target)
    if state.status in {"no-legacy-data", "already-activated"}:
        return state
    if target.exists():
        raise MigrationError(f"Migration target already exists: {target}")
    if dry_run:
        return MigrationResult("dry-run", source, target, state.file_count, state.total_bytes)

    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.parent / f".{target.name}.staging-{uuid.uuid4().hex}"
    try:
        shutil.copytree(source, staging, copy_function=shutil.copy2, symlinks=True)
        source_manifest = _manifest(source)
        target_manifest = _manifest(staging)
        if source_manifest != target_manifest:
            raise MigrationError("Copied data does not match the source manifest")
        _verify_sqlite_files(staging)
        staging.replace(target)
        receipt = migration_receipt_path(target)
        receipt_payload = {
            "status": "activated",
            "source": str(source),
            "target": str(target),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "file_count": len(source_manifest),
            "total_bytes": sum(int(item["bytes"]) for item in source_manifest),
            "files": source_manifest,
        }
        temporary_receipt = receipt.with_name(f".{receipt.name}.{uuid.uuid4().hex}.tmp")
        temporary_receipt.write_text(json.dumps(receipt_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        temporary_receipt.replace(receipt)
        return MigrationResult(
            "activated",
            source,
            target,
            len(source_manifest),
            sum(int(item["bytes"]) for item in source_manifest),
            receipt,
        )
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise
