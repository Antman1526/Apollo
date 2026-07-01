# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for a self-contained Apollo.app (onedir).

Build from the repo root:

    venv/bin/pyinstaller packaging/apollo.spec --noconfirm

Produces dist/apollo/ (onedir). build-macos-bundle.sh wraps that into
Apollo.app + Apollo.dmg.
"""
import os
from PyInstaller.utils.hooks import collect_all, collect_submodules, collect_data_files

REPO = os.path.abspath(os.getcwd())

# ── Native/data-heavy deps that PyInstaller's static analysis misses ──
datas, binaries, hiddenimports = [], [], []
for pkg in (
    "chromadb",
    "onnxruntime",
    "fastembed",
    "tokenizers",
    "cryptography",
    "pydantic",
    "pydantic_core",
    "crawl4ai",
    "mcp",
    "caldav",
    "icalendar",
    "markdown",
    "qrcode",
    "pyotp",
    "huggingface_hub",
    "tqdm",
    "certifi",
):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception as exc:  # noqa: BLE001 — best effort per package
        print(f"[apollo.spec] collect_all({pkg!r}) skipped: {exc}")

# uvicorn dynamically imports its protocol/lifespan/loop workers.
hiddenimports += collect_submodules("uvicorn")
hiddenimports += [
    "uvicorn.lifespan.on",
    "uvicorn.lifespan.off",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.protocols.websockets.websockets_impl",
    "uvicorn.protocols.websockets.wsproto_impl",
]

# The app imports routes/services/etc. dynamically at startup; pull whole trees.
for pkg in ("routes", "services", "core", "src", "companion", "mcp_servers", "config"):
    if os.path.isdir(os.path.join(REPO, pkg)):
        hiddenimports += collect_submodules(pkg)

# ── Ship the app's own resource trees as data so CWD-relative + BASE_DIR
#    lookups (static assets, index.html, config yaml, seed data) resolve. ──
def tree(src, dst=None):
    dst = dst or src
    out = []
    for root, _dirs, files in os.walk(os.path.join(REPO, src)):
        for f in files:
            if f.endswith((".pyc",)):
                continue
            full = os.path.join(root, f)
            rel = os.path.relpath(full, REPO)
            out.append((full, os.path.dirname(rel)))
    return out

datas += tree("static")
datas += tree("config")
# Seed data (small JSON only — skip large caches/DBs; boot shim copies these).
for name in ("auth.json", "presets.json", "features.json", "settings.json",
             "memory.json", "user_prefs.json"):
    p = os.path.join(REPO, "data", name)
    if os.path.isfile(p):
        datas.append((p, "data"))

block_cipher = None

a = Analysis(
    [os.path.join(REPO, "packaging", "apollo_boot.py")],
    pathex=[REPO],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tests", "pytest", "_pytest"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="apollo",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    target_arch="arm64",
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="apollo",
)
