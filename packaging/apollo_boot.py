#!/usr/bin/env python3
"""PyInstaller entrypoint for the self-contained Apollo.app bundle.

Apollo normally runs out of its cloned repo, deriving BASE_DIR / DATA_DIR /
STATIC_DIR from ``core/constants.py``'s ``__file__``. Inside a PyInstaller
onedir bundle that ``__file__`` lives in the read-only app bundle, so:

  * the SQLite DB, chroma store, uploads, settings, etc. cannot be written
    where the code expects them, and
  * a fresh install has none of the seed JSON the app assumes exists.

This boot shim fixes both WITHOUT editing any application source:

  1. Pick a per-user writable home using ``platform_data_root``
     (override with ``APOLLO_HOME``).
  2. On first run, copy only the small, non-personal seed files shipped in the
     bundle into the resolved writable data root. Existing files are preserved.
  3. chdir into that home and monkeypatch ``core.constants`` BASE_DIR,
     STATIC_DIR, and DATA_DIR before the app module is imported. BASE_DIR and
     DATA_DIR point at writable state, while STATIC_DIR remains pointed at the
     current read-only bundle.
  4. Point ``DATABASE_URL`` at the writable DB unless the user set one.
  5. Run uvicorn programmatically.
"""
from __future__ import annotations

import os
import sys
import shutil
import multiprocessing
import socket
import threading
import time
import webbrowser
from pathlib import Path

from src.runtime_paths import platform_data_root


def _bundle_root() -> Path:
    """Directory holding the bundled resources (static/, data/, code).

    Under PyInstaller onedir this is ``sys._MEIPASS``; when run from source
    (for testing the shim) it's this file's parent's parent (the repo root).
    """
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass)
    return Path(__file__).resolve().parent.parent


def _apollo_home() -> Path:
    env = os.environ.get("APOLLO_HOME")
    if env:
        return Path(env).expanduser()
    return platform_data_root(env=os.environ)


def _data_root(home: Path) -> Path:
    """Resolve writable state, preserving both supported override names."""
    for key in ("APOLLO_DATA_DIR", "DATA_DIR"):
        value = os.environ.get(key)
        if value:
            return Path(value).expanduser().resolve()
    return home / "data"


def _seed_home(bundle: Path, home: Path, data_root: Path | None = None) -> None:
    home.mkdir(parents=True, exist_ok=True)

    # data/ must be writable. Copy the small seed files the app expects on first
    # run; never clobber an existing user home. Large/regenerable caches
    # (chroma, fastembed_cache, tts_cache, uploads, generated_images) are left
    # for the app to recreate.
    bundle_data = bundle / "data"
    # Callers that do not provide the already-resolved root are testing or
    # using the helper directly; keep that helper local to the selected home.
    # ``main`` passes ``_data_root(home)`` so environment overrides still win
    # for the actual packaged process.
    home_data = data_root or home / "data"
    home_data.mkdir(parents=True, exist_ok=True)
    # Authentication state is deliberately absent: copying a checkout's
    # auth.json would ship its local accounts to every installed app and skip
    # first-run setup. The auth manager creates an empty file on first use.
    seed_files = [
        "presets.json",
        "features.json",
        "settings.json",
        "memory.json",
        "user_prefs.json",
    ]
    if bundle_data.exists():
        for name in seed_files:
            src = bundle_data / name
            dst = home_data / name
            if src.exists() and not dst.exists():
                try:
                    shutil.copy2(src, dst)
                except OSError:
                    pass

    # Directories the app writes into — create them so first-run code paths
    # that assume they exist don't crash.
    for sub in (
        "uploads",
        "personal_docs",
        "personal_uploads",
        "generated_images",
        "chroma",
        "memory_vectors",
        "rag",
        "skills",
        "tts_cache",
        "deep_research",
    ):
        (home_data / sub).mkdir(parents=True, exist_ok=True)


def _configure_bundled_playwright(bundle: Path) -> None:
    """Point Playwright at Chromium shipped with the application, if present.

    A fresh desktop profile has no ``~/Library/Caches/ms-playwright`` cache.
    The package build places Chromium in this resource directory so the
    browser panel and agent browser API work without a global Node install or
    first-run browser download. Operators can still provide an explicit path.
    """
    bundled_browsers = bundle / "playwright-browsers"
    if bundled_browsers.is_dir():
        os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(bundled_browsers))


def _configure_runtime_paths(home: Path) -> None:
    """Make every runtime-path import resolve under the writable app home."""
    # DATA_DIR is a supported legacy override. Do not overwrite it by adding
    # APOLLO_DATA_DIR when the operator deliberately set the older name.
    if not os.environ.get("APOLLO_DATA_DIR") and not os.environ.get("DATA_DIR"):
        os.environ["APOLLO_DATA_DIR"] = str(home / "data")


def _bundled_script_argument() -> Path | None:
    """Resolve a frozen child-script argument without relying on CWD."""
    if len(sys.argv) <= 1 or not sys.argv[1].endswith(".py"):
        return None
    argument = Path(sys.argv[1])
    if not argument.is_absolute() and getattr(sys, "_MEIPASS", None):
        # A frozen child must use the script shipped with this bundle even if
        # the launcher's CWD happens to contain an older checkout copy.
        candidates = [_bundle_root() / argument, argument]
    else:
        candidates = [argument]
        if not argument.is_absolute():
            candidates.append(_bundle_root() / argument)
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    return None


def _is_loopback(host: str) -> bool:
    return host in {"127.0.0.1", "localhost", "::1"}


def _open_browser_when_ready(host: str, port: int) -> None:
    """Open the Windows entry UI after the frozen server accepts connections."""
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1):
                webbrowser.open(f"http://{host}:{port}")
                return
        except OSError:
            time.sleep(0.25)


def _patch_constants(
    home: Path,
    *,
    bundle: Path | None = None,
    data_root: Path | None = None,
) -> None:
    """Rebind bundle paths and writable state before importing the app."""
    import core.constants as C
    import src.constants as S

    bundle = bundle or _bundle_root()
    resolved_data = data_root or _data_root(home)
    # BASE_DIR is still used by legacy managers for writable logs/uploads.
    # STATIC_DIR is kept separate so the current bundle's immutable assets are
    # served even when a prior install left a stale profile directory behind.
    base = str(home) + os.sep
    values = {
        "BASE_DIR": base,
        "STATIC_DIR": str(bundle / "static"),
        "DATA_DIR": str(resolved_data),
    }
    values.update(
        {
            "SESSIONS_FILE": os.path.join(values["DATA_DIR"], "sessions.json"),
            "MEMORY_FILE": os.path.join(values["DATA_DIR"], "memory.json"),
            "MEMORY_DOC": os.path.join(values["DATA_DIR"], "memory_doc.md"),
            "PERSONAL_DIR": os.path.join(values["DATA_DIR"], "personal_docs"),
            "RUNBOOK_DIR": os.path.join(values["DATA_DIR"], "personal_docs", "runbook"),
            "UPLOAD_DIR": os.path.join(values["DATA_DIR"], "uploads"),
            "FEATURES_FILE": os.path.join(values["DATA_DIR"], "features.json"),
            "SETTINGS_FILE": os.path.join(values["DATA_DIR"], "settings.json"),
        }
    )
    for name, value in values.items():
        setattr(S, name, value)
        setattr(C, name, value)


def main() -> None:
    multiprocessing.freeze_support()

    # Script re-exec mode: ``apollo <script>.py [args...]`` runs a bundled
    # Python script inside the frozen environment instead of booting the
    # server. src/builtin_mcp.py spawns its stdio MCP servers as
    # ``sys.executable mcp_servers/<x>.py`` — in the frozen app
    # sys.executable IS this binary, so without this branch every such spawn
    # would try to start a second Apollo server (and die on the bind).
    script_path = _bundled_script_argument()
    if script_path is not None:
        import runpy

        sys.argv = [str(script_path), *sys.argv[2:]]
        runpy.run_path(str(script_path), run_name="__main__")
        return

    bundle = _bundle_root()
    home = _apollo_home()

    # Ensure the app package root is importable regardless of CWD. In a frozen
    # bundle the modules are embedded (this is a no-op); when running this shim
    # from source it puts the repo root on sys.path so ``import core`` / ``app``
    # resolve even after we chdir into the writable home below.
    root = str(bundle)
    if root not in sys.path:
        sys.path.insert(0, root)

    data_root = _data_root(home)
    _seed_home(bundle, home, data_root)
    _configure_bundled_playwright(bundle)
    _configure_runtime_paths(home)

    # Everything downstream expects to run from a dir containing static/ + data/.
    os.chdir(home)

    host = os.environ.get("APOLLO_HOST", "127.0.0.1")
    loopback = _is_loopback(host)
    # Only a loopback desktop server gets the friction-free local default.
    # Inherited LAN/public bindings retain the app's authenticated default.
    if loopback:
        os.environ.setdefault("AUTH_ENABLED", "false")

    # Writable SQLite DB (unless the user pinned DATABASE_URL themselves).
    os.environ.setdefault(
        "DATABASE_URL", "sqlite:///" + str(data_root / "app.db")
    )
    # Keep model/cache downloads inside the writable home too.
    os.environ.setdefault("HF_HOME", str(data_root / "hf_cache"))
    os.environ.setdefault("FASTEMBED_CACHE_PATH", str(data_root / "fastembed_cache"))

    _patch_constants(home, bundle=bundle, data_root=data_root)

    import uvicorn

    # Import the ASGI app object directly rather than passing the "app:app"
    # import string: inside a frozen PyInstaller bundle uvicorn's string-based
    # re-import can't resolve the top-level ``app`` module, failing with
    # "Could not import module 'app'". Passing the object sidesteps that (we
    # never use --reload, which is the only thing that needs the string form).
    from app import app as asgi_app

    port = int(os.environ.get("APOLLO_PORT", "7860"))
    open_browser = os.environ.get(
        "APOLLO_OPEN_BROWSER", "true" if os.name == "nt" and loopback else "false"
    )
    if open_browser.lower() == "true" and loopback:
        threading.Thread(
            target=_open_browser_when_ready,
            args=(host, port),
            daemon=True,
        ).start()
    uvicorn.run(asgi_app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
