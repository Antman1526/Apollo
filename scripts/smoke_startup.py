#!/usr/bin/env python3
"""Boot Apollo in an isolated data directory and verify its basic HTTP surface."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _get(url: str) -> tuple[int, bytes]:
    with urllib.request.urlopen(url, timeout=2) as response:
        return response.status, response.read()


def _failure_message(process: subprocess.Popen[str]) -> str:
    output = process.stdout.read() if process.stdout else ""
    tail = output[-4000:].replace("\x1b", "")
    return f"Apollo startup failed (exit={process.poll()}). Log tail:\n{tail}"


def main() -> int:
    port = _free_port()
    with tempfile.TemporaryDirectory(prefix="apollo-startup-smoke-") as temporary:
        data_root = Path(temporary) / "data"
        env = os.environ | {
            "APOLLO_DATA_DIR": str(data_root),
            "DATABASE_URL": f"sqlite:///{data_root / 'app.db'}",
            "AUTH_ENABLED": "false",
            "APOLLO_DISABLE_MCP": "true",
            "PAPERCLIP_COLLECTOR_ENABLED": "false",
            "CHROMADB_HOST": "",
        }
        command = [sys.executable, "-m", "uvicorn", "app:app", "--host", "127.0.0.1", "--port", str(port)]
        process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        try:
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError(_failure_message(process))
                try:
                    status, _ = _get(f"http://127.0.0.1:{port}/api/health")
                    if status == 200:
                        break
                except OSError:
                    time.sleep(0.25)
            else:
                raise TimeoutError(_failure_message(process))

            health_status, _ = _get(f"http://127.0.0.1:{port}/api/health")
            page_status, _ = _get(f"http://127.0.0.1:{port}/")
            openapi_status, openapi = _get(f"http://127.0.0.1:{port}/openapi.json")
            assert health_status == page_status == openapi_status == 200
            assert json.loads(openapi).get("openapi")
            print("startup-smoke-ok")
            return 0
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)


if __name__ == "__main__":
    raise SystemExit(main())
