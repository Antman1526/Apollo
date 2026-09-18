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

# Keep child paths free of application imports until their explicit isolation
# has been applied. Source-mode tests can still monkeypatch this seam; normal
# execution resolves it lazily when the server path actually needs it.
platform_data_root = None


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
    resolver = platform_data_root
    if resolver is None:
        from src.runtime_paths import platform_data_root as resolver
    return resolver(env=os.environ)


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


def _bundled_script_argument(argument: str | None = None) -> Path | None:
    """Resolve a child-script argument without relying on CWD.

    A frozen executable must never turn an arbitrary path supplied by the
    caller into executable Python. Its script mode is limited to files that
    PyInstaller shipped below ``_MEIPASS``; source-mode tests retain the
    historical relative-path fallback.
    """
    if argument is None:
        if len(sys.argv) <= 1:
            return None
        argument = sys.argv[1]
    if not argument.endswith(".py"):
        return None
    argument_path = Path(argument)
    if getattr(sys, "frozen", False) or getattr(sys, "_MEIPASS", None):
        # A frozen child must use the script shipped with this bundle even if
        # the launcher's CWD happens to contain an older checkout copy.
        bundle = _bundle_root().resolve()
        candidate = (bundle / argument_path).resolve()
        try:
            candidate.relative_to(bundle)
        except ValueError:
            return None
        candidates = [candidate]
    else:
        candidates = [argument_path]
        if not argument_path.is_absolute():
            candidates.append(_bundle_root() / argument_path)
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    return None


def _child_invocation() -> tuple[str, str | Path, tuple[str, ...] | None] | None:
    """Parse the child forms supported by the frozen executable.

    ``-I -c CODE`` is the one-shot Python tool shape, while
    ``-I scripts/apollo_kernel_worker.py`` is the persistent-session shape.
    The existing ``mcp_servers/<name>.py [args...]`` form remains supported.
    Any other arguments are rejected before the server is imported.
    """
    args = sys.argv[1:]
    if not args:
        return None

    if args[0] == "-I":
        if len(args) == 3 and args[1] == "-c":
            return ("code", args[2], None)
        if len(args) == 2 and args[1] != "-c":
            worker = _bundled_script_argument(args[1])
            expected = (_bundle_root() / "scripts" / "apollo_kernel_worker.py").resolve()
            if worker is not None and worker.resolve() == expected:
                return ("worker", worker, None)
        raise SystemExit("apollo: unsupported child arguments after -I")

    script = _bundled_script_argument(args[0])
    if script is not None:
        return ("script", script, tuple(args[1:]))
    raise SystemExit("apollo: unsupported child arguments")


def _configure_isolated_child() -> None:
    """Retain ``python -I`` import hygiene for frozen child processes."""
    if not getattr(sys, "frozen", False):
        return
    # PyInstaller supplies the embedded import machinery; the bundle root is
    # the only filesystem path needed by shipped script files. In particular,
    # do not expose CWD, PYTHONPATH, or a user's site directory to child code.
    pythonpath_entries: set[Path] = set()
    for entry in os.environ.get("PYTHONPATH", "").split(os.pathsep):
        if entry:
            try:
                pythonpath_entries.add(Path(entry).expanduser().resolve())
            except (OSError, RuntimeError, TypeError):
                continue
    os.environ.pop("PYTHONPATH", None)
    os.environ.pop("PYTHONUSERBASE", None)
    os.environ["PYTHONNOUSERSITE"] = "1"
    bundle_root = _bundle_root().resolve()
    cwd = Path.cwd().resolve()
    user_sites: set[Path] = set()
    try:
        import site

        site.ENABLE_USER_SITE = False
        candidates = site.getusersitepackages()
        if isinstance(candidates, str):
            candidates = [candidates]
        for candidate in candidates:
            try:
                user_sites.add(Path(candidate).expanduser().resolve())
            except (OSError, RuntimeError, TypeError):
                continue
    except Exception:
        pass

    # Keep PyInstaller's embedded zip/native directories and bundled eggs, but
    # discard CWD/PYTHONPATH/user-site entries and anything outside the bundle.
    # Canonicalized paths make symlink escapes and duplicate spellings harmless.
    trusted: list[str] = []
    seen: set[str] = set()
    for entry in list(sys.path):
        if not entry:
            continue
        try:
            resolved = Path(entry).expanduser().resolve()
        except (OSError, RuntimeError, TypeError):
            continue
        if (
            resolved == cwd
            or resolved in pythonpath_entries
            or any(
                resolved == user_site or user_site in resolved.parents
                for user_site in user_sites
            )
        ):
            continue
        try:
            resolved.relative_to(bundle_root)
        except ValueError:
            continue
        normalized = str(resolved)
        if normalized not in seen:
            seen.add(normalized)
            trusted.append(normalized)
    if str(bundle_root) not in seen:
        trusted.append(str(bundle_root))
    sys.path[:] = trusted


def _run_child(invocation: tuple[str, str | Path, tuple[str, ...] | None]) -> None:
    """Execute a parsed child invocation and preserve its exit semantics."""
    mode, payload, extra = invocation
    _configure_isolated_child()
    if mode == "code":
        # Match ``python -c``'s argv contract. Exceptions and SystemExit are
        # intentionally allowed to propagate so the caller receives the same
        # non-zero status as the unfrozen subprocess.
        sys.argv = ["-c"]
        exec(compile(str(payload), "<string>", "exec"), {
            "__name__": "__main__",
            "__builtins__": __builtins__,
        })
        return

    import runpy

    sys.argv = [str(payload), *(extra or ())]
    runpy.run_path(str(payload), run_name="__main__")


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

    # Child mode must be decided before any server setup/import. The one-shot
    # and persistent Python tools use ``-I`` forms; built-in MCP servers use a
    # shipped script path directly. Unknown child arguments must not fall
    # through into a second uvicorn startup.
    child = _child_invocation()
    if child is not None:
        _run_child(child)
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
