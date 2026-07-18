#!/usr/bin/env python3
"""Apollo — Windows launcher (compiled to ``Apollo.exe`` by PyInstaller in CI).

A friendly double-click entry point for Windows. It runs the maintained
``launch-windows.ps1`` (which creates the venv, installs dependencies, runs the
first-time ``setup.py`` that prints the temporary admin password, and starts the
server), and opens your browser once Apollo is reachable.

This is a *launcher*, the Windows parallel to the macOS ``Apollo.dmg`` launcher —
it starts your local Apollo install; it is not a self-contained bundle of the
whole app. Run it from inside the Apollo project folder (or place ``Apollo.exe``
next to ``launch-windows.ps1`` / ``app.py``).

If ``launch-windows.ps1`` is not found, it falls back to starting the server
directly from an existing ``venv``.
"""
from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

PORT = int(os.environ.get("APOLLO_PORT", os.environ.get("APP_PORT", "7000")))
HOST = os.environ.get("APOLLO_HOST", os.environ.get("APP_BIND", "127.0.0.1"))


def app_root() -> Path:
    """Locate the Apollo project dir (contains app.py / launch-windows.ps1)."""
    base = Path(sys.executable).parent if getattr(sys, "frozen", False) \
        else Path(__file__).resolve().parent
    for d in [base, *base.parents][:6]:
        if (d / "app.py").exists() or (d / "launch-windows.ps1").exists():
            return d
    return base


def _port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _open_browser_when_ready() -> None:
    """Poll the local port; open the browser once the server answers.

    First run can take minutes (venv creation + pip install), so we wait
    generously rather than racing the server.
    """
    url = f"http://127.0.0.1:{PORT}/"
    for _ in range(900):  # ~15 min ceiling for a cold first-run install
        if _port_open(PORT):
            time.sleep(1.5)  # let uvicorn finish binding routes
            try:
                webbrowser.open(url)
            except Exception:
                pass
            return
        time.sleep(1)


def _find_python(root: Path) -> str | None:
    venv_py = root / "venv" / "Scripts" / "python.exe"
    if venv_py.exists():
        return str(venv_py)
    from shutil import which
    return which("python") or which("py")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Start Apollo from a local Windows project checkout."
    )
    parser.add_argument(
        "--version",
        action="version",
        version="Apollo Windows launcher",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    # Parsing before the launcher side effects makes `Apollo.exe --help` a
    # deterministic packaging smoke test instead of an accidental server run.
    parse_args(argv)
    root = app_root()
    os.chdir(root)
    print(f"Apollo launcher — project: {root}")
    print(f"Opening http://127.0.0.1:{PORT}/ once the server is ready...\n")
    threading.Thread(target=_open_browser_when_ready, daemon=True).start()

    ps1 = root / "launch-windows.ps1"
    if ps1.exists():
        # Reuse the maintained launcher: venv + install + setup + start.
        cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
               "-File", str(ps1), "-Port", str(PORT), "-BindHost", HOST]
        return subprocess.call(cmd)

    # Fallback: start directly from an existing venv (no first-run install).
    py = _find_python(root)
    if not py:
        print("ERROR: No Python found and launch-windows.ps1 is missing.")
        print("Install Python 3.11+ and run launch-windows.ps1 once to set up.")
        input("Press Enter to exit...")
        return 1
    if not (root / "app.py").exists():
        print("ERROR: app.py not found — run Apollo.exe from inside the Apollo folder.")
        input("Press Enter to exit...")
        return 1
    return subprocess.call([py, "-m", "uvicorn", "app:app",
                            "--host", HOST, "--port", str(PORT)])


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
