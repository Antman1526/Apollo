#!/usr/bin/env python3
"""Bounded smoke test for a frozen Apollo desktop executable."""

from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _status(url: str) -> tuple[int, bytes] | None:
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            return response.status, response.read()
    except (OSError, urllib.error.URLError):
        return None


def _stop(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=8)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=8)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", required=True, type=Path)
    parser.add_argument("--timeout", type=float, default=60)
    args = parser.parse_args()

    binary = args.binary.expanduser().resolve()
    if not binary.is_file():
        raise SystemExit(f"packaged binary not found: {binary}")
    port = _free_port()
    temporary_profile = tempfile.TemporaryDirectory(prefix="apollo-packaged-smoke-")
    profile = Path(temporary_profile.name)
    profile.mkdir(parents=True, exist_ok=True)
    data_root = profile / "data"
    data_root.mkdir(parents=True, exist_ok=True)
    # Model an upgrade over a prior install. The frozen server must serve the
    # assets shipped beside the executable, never these stale profile files.
    stale_static = profile / "static"
    stale_static.mkdir(parents=True, exist_ok=True)
    (stale_static / "index.html").write_text("STALE_APOLLO_ASSET", encoding="utf-8")
    (stale_static / "app.js").write_text("STALE_APOLLO_ASSET", encoding="utf-8")
    log_path = profile / "smoke.log"
    environment = os.environ | {
        "APOLLO_HOME": str(profile),
        "APOLLO_DATA_DIR": str(data_root),
        "DATABASE_URL": f"sqlite:///{data_root / 'app.db'}",
        "APOLLO_PORT": str(port),
        "APOLLO_HOST": "127.0.0.1",
        "APOLLO_OPEN_BROWSER": "false",
        "AUTH_ENABLED": "false",
        "APOLLO_DISABLE_MCP": "true",
        "PAPERCLIP_COLLECTOR_ENABLED": "false",
        "CHROMADB_HOST": "",
    }
    process: subprocess.Popen[str] | None = None
    try:
        with log_path.open("w", encoding="utf-8") as log:
            process = subprocess.Popen(
                [str(binary)],
                cwd=profile,
                env=environment,
                stdout=log,
                stderr=subprocess.STDOUT,
                text=True,
            )
            deadline = time.monotonic() + args.timeout
            base = f"http://127.0.0.1:{port}"
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    break
                health = _status(f"{base}/api/health")
                if health and health[0] == 200:
                    break
                time.sleep(0.25)
            else:
                raise RuntimeError(f"health timeout; log tail:\n{log_path.read_text(encoding='utf-8')[-4000:]}")

            if process.poll() is not None:
                raise RuntimeError(f"packaged process exited {process.returncode}; log tail:\n{log_path.read_text(encoding='utf-8')[-4000:]}")
            checks = {
                "/api/health": _status(f"{base}/api/health"),
                "/api/ready": _status(f"{base}/api/ready"),
                "/": _status(f"{base}/"),
                "/static/app.js": _status(f"{base}/static/app.js"),
            }
            failures = [path for path, result in checks.items() if not result or result[0] != 200]
            if failures:
                raise RuntimeError(f"packaged smoke failed for {', '.join(failures)}; log tail:\n{log_path.read_text(encoding='utf-8')[-4000:]}")
            if b"Apollo" not in checks["/"][1]:
                raise RuntimeError("packaged root response did not contain Apollo")
            if not checks["/static/app.js"][1]:
                raise RuntimeError("packaged static module was empty")
            if b"STALE_APOLLO_ASSET" in checks["/"][1] or b"STALE_APOLLO_ASSET" in checks["/static/app.js"][1]:
                raise RuntimeError("packaged process served stale profile assets")
            print("packaged-smoke-ok")
            return 0
    finally:
        if process is not None:
            _stop(process)
        temporary_profile.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
