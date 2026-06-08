"""Auto-provision a pinned Node runtime for the bundled Paperclip.

Apollo ships as a native Mac/Windows app with no Node prerequisite. On first
native launch this downloads an official Node build from nodejs.org into Apollo's
data dir and returns the node/npx paths, so `paperclipai` runs with zero user
setup. Idempotent; degrades gracefully (callers fall back to a PATH Node).

Pure helpers (filename/url/paths/pick_lts) are unit-tested; the network download
is injectable.
"""
from __future__ import annotations

import logging
import os
import platform
import tarfile
import urllib.request
import zipfile
from typing import Callable, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Fallback when the LTS index can't be fetched. Overridable via env.
DEFAULT_NODE_VERSION = "22.13.0"
INDEX_URL = "https://nodejs.org/dist/index.json"


def _arch(machine: str) -> str:
    m = machine.lower()
    if m in ("arm64", "aarch64"):
        return "arm64"
    if m in ("x86_64", "amd64", "x64"):
        return "x64"
    if m in ("armv7l",):
        return "armv7l"
    return m


def _os_tag(system: str) -> str:
    s = system.lower()
    if s == "darwin":
        return "darwin"
    if s == "windows":
        return "win"
    return "linux"


def dist_basename(version: str, system: str, machine: str) -> str:
    return f"node-v{version}-{_os_tag(system)}-{_arch(machine)}"


def dist_filename(version: str, system: str, machine: str) -> str:
    base = dist_basename(version, system, machine)
    ext = "zip" if _os_tag(system) == "win" else "tar.gz"
    return f"{base}.{ext}"


def dist_url(version: str, system: str, machine: str) -> str:
    return f"https://nodejs.org/dist/v{version}/{dist_filename(version, system, machine)}"


def bin_paths(node_home: str, system: str) -> Tuple[str, str]:
    if _os_tag(system) == "win":
        return os.path.join(node_home, "node.exe"), os.path.join(node_home, "npx.cmd")
    return os.path.join(node_home, "bin", "node"), os.path.join(node_home, "bin", "npx")


def pick_lts(index: List[dict]) -> Optional[str]:
    """Highest version with a truthy `lts` field. Versions look like 'v22.13.0'."""
    best: Optional[Tuple[int, int, int]] = None
    best_str: Optional[str] = None
    for entry in index:
        if not entry.get("lts"):
            continue
        v = str(entry.get("version", "")).lstrip("v")
        try:
            parts = tuple(int(x) for x in v.split("."))
        except ValueError:
            continue
        if len(parts) == 3 and (best is None or parts > best):
            best = parts  # type: ignore[assignment]
            best_str = v
    return best_str


def _default_download_extract(url: str, dest_parent: str) -> None:
    os.makedirs(dest_parent, exist_ok=True)
    tmp = os.path.join(dest_parent, "_node_download.tmp")
    logger.info("Downloading Node: %s", url)
    urllib.request.urlretrieve(url, tmp)
    try:
        if url.endswith(".zip"):
            with zipfile.ZipFile(tmp) as z:
                z.extractall(dest_parent)
        else:
            with tarfile.open(tmp) as t:
                t.extractall(dest_parent)
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def resolve_version(fetch_index: Optional[Callable[[], List[dict]]] = None) -> str:
    env = os.getenv("PAPERCLIP_NODE_VERSION")
    if env:
        return env.lstrip("v")
    if fetch_index is not None:
        try:
            lts = pick_lts(fetch_index())
            if lts:
                return lts
        except Exception as e:  # pragma: no cover - network/parse defensive
            logger.warning("Node LTS index lookup failed (%s); using default", e)
    return DEFAULT_NODE_VERSION


def _find_installed(node_root: str, system: str, machine: str) -> Optional[Tuple[str, str]]:
    """Reuse any already-extracted Node for this os/arch — avoids a network
    version lookup on every launch once Node is provisioned."""
    suffix = f"-{_os_tag(system)}-{_arch(machine)}"
    try:
        names = os.listdir(node_root)
    except OSError:
        return None
    for name in sorted(names, reverse=True):
        if name.startswith("node-v") and name.endswith(suffix):
            node, npx = bin_paths(os.path.join(node_root, name), system)
            if os.path.exists(node):
                return node, npx
    return None


def ensure_node(install_dir: str, *, version: Optional[str] = None,
                system: Optional[str] = None, machine: Optional[str] = None,
                download_extract: Callable[[str, str], None] = _default_download_extract,
                fetch_index: Optional[Callable[[], List[dict]]] = None) -> Optional[Tuple[str, str]]:
    """Return (node, npx) paths, downloading the pinned Node if absent.
    Returns None on failure (caller falls back to a PATH Node)."""
    system = system or platform.system()
    machine = machine or platform.machine()
    node_root = os.path.join(install_dir, ".node")
    # Fast path: an unpinned caller reuses whatever is already installed.
    if version is None:
        existing = _find_installed(node_root, system, machine)
        if existing:
            return existing
    version = (version or resolve_version(fetch_index)).lstrip("v")
    node_home = os.path.join(node_root, dist_basename(version, system, machine))
    node, npx = bin_paths(node_home, system)
    if os.path.exists(node):
        return node, npx
    try:
        download_extract(dist_url(version, system, machine), node_root)
    except Exception as e:
        logger.warning("Node bootstrap failed (%s); will try a system Node", e)
        return None
    if os.path.exists(node):
        return node, npx
    logger.warning("Node bootstrap: expected binary missing after extract: %s", node)
    return None
