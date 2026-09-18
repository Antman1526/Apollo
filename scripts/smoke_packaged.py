#!/usr/bin/env python3
"""Bounded smoke test for a frozen Apollo desktop executable."""

from __future__ import annotations

import argparse
import json
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


def _run_child(
    binary: Path,
    args: list[str],
    *,
    cwd: Path,
    environment: dict[str, str],
    stdin: str = "",
    timeout: float = 12,
) -> subprocess.CompletedProcess[str]:
    """Exercise a frozen child form without allowing it to become a server."""
    return subprocess.run(
        [str(binary), *args],
        cwd=cwd,
        env=environment,
        input=stdin,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


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

            # The production one-shot python caller re-execs the frozen
            # binary as ``-I -c``. Preserve its output and non-zero exit code.
            code_child = _run_child(
                binary,
                [
                    "-I",
                    "-c",
                    "import sqlite3, pypdf; print('child-ok'); raise SystemExit(7)",
                ],
                cwd=profile,
                environment=environment,
            )
            if code_child.returncode != 7 or code_child.stdout.strip() != "child-ok":
                raise RuntimeError(
                    "frozen -I -c child failed: "
                    f"rc={code_child.returncode}, stdout={code_child.stdout!r}, "
                    f"stderr={code_child.stderr[-1000:]!r}"
                )

            # The persistent python_session caller re-execs the shipped worker
            # and must retain state across JSON-line requests.
            worker_input = "\n".join(
                (
                    json.dumps({"code": "value = 41"}),
                    json.dumps({"code": "print(value + 1)"}),
                    json.dumps({"cmd": "shutdown"}),
                    "",
                )
            )
            worker_child = _run_child(
                binary,
                ["-I", "scripts/apollo_kernel_worker.py"],
                cwd=profile,
                environment=environment,
                stdin=worker_input,
            )
            try:
                worker_lines = [json.loads(line) for line in worker_child.stdout.splitlines()]
            except json.JSONDecodeError as error:
                raise RuntimeError(
                    f"frozen worker returned invalid JSON: {error}; "
                    f"stdout={worker_child.stdout[-2000:]!r}; "
                    f"stderr={worker_child.stderr[-1000:]!r}"
                ) from error
            if (
                worker_child.returncode != 0
                or len(worker_lines) != 2
                or worker_lines[0].get("error") is not None
                or worker_lines[1].get("stdout") != "42\n"
            ):
                raise RuntimeError(
                    "frozen python_session child failed: "
                    f"rc={worker_child.returncode}, output={worker_child.stdout[-2000:]!r}, "
                    f"stderr={worker_child.stderr[-1000:]!r}"
                )

            # ``-I`` must not re-enable imports from a hostile CWD/PYTHONPATH.
            (profile / "adversarial_import.py").write_text(
                "raise RuntimeError('hostile module imported')\n", encoding="utf-8"
            )
            hostile_environment = environment | {"PYTHONPATH": str(profile)}
            hostile_child = _run_child(
                binary,
                ["-I", "-c", "import adversarial_import"],
                cwd=profile,
                environment=hostile_environment,
            )
            if hostile_child.returncode == 0 or "ModuleNotFoundError" not in hostile_child.stderr:
                raise RuntimeError(
                    "frozen -I child imported a CWD/PYTHONPATH module: "
                    f"rc={hostile_child.returncode}, stderr={hostile_child.stderr[-1000:]!r}"
                )

            unsupported_child = _run_child(
                binary,
                ["--not-a-child"],
                cwd=profile,
                environment=environment,
            )
            if unsupported_child.returncode == 0 or "unsupported child arguments" not in unsupported_child.stderr:
                raise RuntimeError(
                    "frozen unsupported child args did not fail cleanly: "
                    f"rc={unsupported_child.returncode}, stderr={unsupported_child.stderr[-1000:]!r}"
                )
            print("packaged-smoke-ok")
            return 0
    finally:
        if process is not None:
            _stop(process)
        temporary_profile.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
