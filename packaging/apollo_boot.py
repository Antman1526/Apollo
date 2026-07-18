#!/usr/bin/env python3
"""PyInstaller entrypoint for the self-contained Apollo.app bundle.

Apollo normally runs out of its cloned repo, deriving BASE_DIR / DATA_DIR /
STATIC_DIR from ``core/constants.py``'s ``__file__``. Inside a PyInstaller
onedir bundle that ``__file__`` lives in the read-only app bundle, so:

  * the SQLite DB, chroma store, uploads, settings, etc. cannot be written
    where the code expects them, and
  * a fresh install has none of the seed JSON the app assumes exists.

This boot shim fixes both WITHOUT editing any application source:

  1. Pick a per-user writable home: ``~/Library/Application Support/Apollo``
     (override with ``APOLLO_HOME``).
  2. On first run, seed it from the read-only copies shipped in the bundle
     (``static/`` is symlinked; ``data/`` seed files are copied so they're
     writable).
  3. chdir into that home (so the ``StaticFiles(directory="static")`` mount and
     any other CWD-relative paths resolve there) and monkeypatch
     ``core.constants`` BASE_DIR/DATA_DIR/STATIC_DIR to the home BEFORE the app
     module is imported, so every ``from core.constants import DATA_DIR`` in the
     app binds to the writable location.
  4. Point ``DATABASE_URL`` at the writable DB unless the user set one.
  5. Run uvicorn programmatically.
"""
from __future__ import annotations

import os
import sys
import shutil
from pathlib import Path


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
    base = Path.home() / "Library" / "Application Support" / "Apollo"
    return base


def _seed_home(bundle: Path, home: Path) -> None:
    home.mkdir(parents=True, exist_ok=True)

    # static/ is read-only content; a symlink into the bundle is enough and
    # keeps the writable home tiny. Fall back to a copy if symlinking fails.
    bundle_static = bundle / "static"
    home_static = home / "static"
    if bundle_static.exists() and not home_static.exists():
        try:
            home_static.symlink_to(bundle_static, target_is_directory=True)
        except OSError:
            shutil.copytree(bundle_static, home_static)

    # data/ must be writable. Copy the small seed files the app expects on first
    # run; never clobber an existing user home. Large/regenerable caches
    # (chroma, fastembed_cache, tts_cache, uploads, generated_images) are left
    # for the app to recreate.
    bundle_data = bundle / "data"
    home_data = home / "data"
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
    os.environ.setdefault("APOLLO_DATA_DIR", str(home / "data"))


def _patch_constants(home: Path) -> None:
    """Rebind legacy path constants to the writable home before app import."""
    import core.constants as C
    import src.constants as S

    base = str(home) + os.sep
    values = {
        "BASE_DIR": base,
        "STATIC_DIR": os.path.join(base, "static"),
        "DATA_DIR": os.path.join(base, "data"),
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
    # Script re-exec mode: ``apollo <script>.py [args...]`` runs a bundled
    # Python script inside the frozen environment instead of booting the
    # server. src/builtin_mcp.py spawns its stdio MCP servers as
    # ``sys.executable mcp_servers/<x>.py`` — in the frozen app
    # sys.executable IS this binary, so without this branch every such spawn
    # would try to start a second Apollo server (and die on the bind).
    if len(sys.argv) > 1 and sys.argv[1].endswith(".py") and os.path.isfile(sys.argv[1]):
        import runpy

        script = sys.argv[1]
        sys.argv = sys.argv[1:]  # the script sees itself as argv[0]
        runpy.run_path(script, run_name="__main__")
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

    _seed_home(bundle, home)
    _configure_bundled_playwright(bundle)
    _configure_runtime_paths(home)

    # Everything downstream expects to run from a dir containing static/ + data/.
    os.chdir(home)

    # Writable SQLite DB (unless the user pinned DATABASE_URL themselves).
    os.environ.setdefault(
        "DATABASE_URL", "sqlite:///" + str(home / "data" / "app.db")
    )
    # Keep model/cache downloads inside the writable home too.
    os.environ.setdefault("HF_HOME", str(home / "data" / "hf_cache"))
    os.environ.setdefault("FASTEMBED_CACHE_PATH", str(home / "data" / "fastembed_cache"))

    _patch_constants(home)

    import uvicorn

    # Import the ASGI app object directly rather than passing the "app:app"
    # import string: inside a frozen PyInstaller bundle uvicorn's string-based
    # re-import can't resolve the top-level ``app`` module, failing with
    # "Could not import module 'app'". Passing the object sidesteps that (we
    # never use --reload, which is the only thing that needs the string form).
    from app import app as asgi_app

    port = int(os.environ.get("APOLLO_PORT", "7860"))
    host = os.environ.get("APOLLO_HOST", "127.0.0.1")
    uvicorn.run(asgi_app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
