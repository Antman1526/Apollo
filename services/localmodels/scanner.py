"""Discover local GGUF chat/embedding models under configured directories."""
from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field

from services.localmodels.gguf_meta import classify_architecture, read_architecture

# Quant regex ported from routes/cookbook_helpers.py (_cached_model_scan_script).
_QUANT_RE = re.compile(
    r"(?i)(UD-)?(IQ[0-9]_[A-Z0-9_]+|Q[0-9](?:_[A-Z0-9]+)+|BF16|F16|FP16|F32|Q8_0)"
)
_EMBED_HINT = re.compile(r"(?i)(embed|nomic|bge|gte|e5|minilm)")
_SPLIT_RE = re.compile(r"(?i)^(.+)-(\d+)-of-(\d+)\.gguf$")

# Subdirectories that hold cache/blob stores rather than user-facing models.
# Pruning them keeps HF cache blobs, ollama stores, and duplicate copies from
# showing up as selectable models.
_SKIP_DIRS = {"cache", ".cache", "llama-cache", "ollama", ".ollama", "blobs", "tmp", ".git"}


@dataclass
class LocalModel:
    id: str
    name: str
    path: str
    quant: str
    kind: str  # "chat" | "embedding" | "unsupported"
    size_bytes: int
    directory: str
    arch: str = field(default="")


def _quant(name: str) -> str:
    m = _QUANT_RE.search(name)
    return m.group(0).upper() if m else ""


def _is_projector(name: str) -> bool:
    n = name.lower()
    return n.startswith("mmproj") or "mmproj" in n


def _kind_from_filename(name: str) -> str:
    """Filename-only heuristic — used as fallback when the GGUF header is unreadable."""
    return "embedding" if _EMBED_HINT.search(name) else "chat"


def _resolve_kind(path: str, name: str) -> tuple[str, str]:
    """Return (kind, arch) for a GGUF file.

    Reads the GGUF header for a reliable architecture tag; falls back to the
    filename heuristic when the header is unreadable (e.g. a zero-byte stub in
    tests, a freshly-downloaded partial file).
    """
    arch = read_architecture(path) or ""
    kind = classify_architecture(arch) if arch else None
    if kind is None:
        kind = _kind_from_filename(name)
    return kind, arch


def _model_id(path: str) -> str:
    return "lm_" + hashlib.sha1(path.encode("utf-8")).hexdigest()[:16]


def scan_dirs(dirs: list[str]) -> list[LocalModel]:
    """Walk each directory and return discovered GGUF models (deduped by path)."""
    out: dict[str, LocalModel] = {}
    for raw in dirs or []:
        base = os.path.realpath(os.path.expanduser(raw))
        if not os.path.isdir(base):
            continue
        for root, subdirs, files in os.walk(base, followlinks=False):
            # Prune cache/blob dirs in place so os.walk never descends into them.
            subdirs[:] = [d for d in subdirs if d.lower() not in _SKIP_DIRS]
            for fn in sorted(files):
                if not fn.lower().endswith(".gguf"):
                    continue
                if fn.startswith("._"):
                    continue
                if _is_projector(fn):
                    continue
                split = _SPLIT_RE.match(fn)
                if split and split.group(2) != str(int(split.group(2))):
                    continue
                if split and int(split.group(2)) != 1:
                    continue  # only register the first part of a split model
                fp = os.path.join(root, fn)
                try:
                    size = os.path.getsize(fp)
                except OSError:
                    continue
                mid = _model_id(fp)
                if mid in out:
                    continue
                model_name = fn[:-5]  # strip ".gguf"
                kind, arch = _resolve_kind(fp, model_name)
                out[mid] = LocalModel(
                    id=mid,
                    name=model_name,
                    path=fp,
                    quant=_quant(fn),
                    kind=kind,
                    size_bytes=size,
                    directory=base,
                    arch=arch,
                )
    return list(out.values())


def discover_piper_voices(dirs: list[str]) -> list[dict]:
    """Find Piper TTS voices (`*.onnx` with a sibling `*.onnx.json`) under the
    configured dirs, so the UI can offer them without the user typing paths.

    Returns [{"name": <stem>, "path": <abs .onnx path>}], deduped by path.
    """
    out: dict[str, dict] = {}
    for raw in dirs or []:
        base = os.path.realpath(os.path.expanduser(raw))
        if not os.path.isdir(base):
            continue
        for root, subdirs, files in os.walk(base, followlinks=False):
            subdirs[:] = [d for d in subdirs if d.lower() not in _SKIP_DIRS]
            for fn in sorted(files):
                if not fn.lower().endswith(".onnx") or fn.startswith("._"):
                    continue
                fp = os.path.join(root, fn)
                if not os.path.isfile(fp + ".json"):
                    continue  # a Piper voice needs its config sidecar
                if fp not in out:
                    out[fp] = {"name": fn[:-5], "path": fp}
    return list(out.values())
