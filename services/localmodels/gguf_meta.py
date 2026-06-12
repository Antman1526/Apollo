"""Minimal GGUF header reader — just enough to get general.architecture.

Reads only the KV metadata section (a few KB), never the tensors, so it's
safe to run on every file during a directory scan.
"""
from __future__ import annotations

import logging
import struct
from typing import Optional

logger = logging.getLogger(__name__)

# Byte-widths of fixed-size GGUF value types.
_SIZES = {0: 1, 1: 1, 2: 2, 3: 2, 4: 4, 5: 4, 6: 4, 7: 1, 10: 8, 11: 8, 12: 8}
_STRING = 8
_ARRAY = 9


def read_architecture(path: str, max_kv: int = 64) -> Optional[str]:
    """Return general.architecture from a GGUF file, or None when unreadable."""
    try:
        with open(path, "rb") as f:
            if f.read(4) != b"GGUF":
                return None
            struct.unpack("<I", f.read(4))          # version
            struct.unpack("<Q", f.read(8))          # n_tensors
            (n_kv,) = struct.unpack("<Q", f.read(8))

            def read_str() -> str:
                (n,) = struct.unpack("<Q", f.read(8))
                return f.read(n).decode("utf-8", "replace")

            def skip(t: int) -> None:
                if t in _SIZES:
                    f.seek(_SIZES[t], 1)
                elif t == _STRING:
                    read_str()
                elif t == _ARRAY:
                    (et,) = struct.unpack("<I", f.read(4))
                    (n,) = struct.unpack("<Q", f.read(8))
                    if et == _STRING:
                        for _ in range(n):
                            read_str()
                    else:
                        f.seek(_SIZES.get(et, 1) * n, 1)

            for _ in range(min(n_kv, max_kv)):
                key = read_str()
                (t,) = struct.unpack("<I", f.read(4))
                if key == "general.architecture" and t == _STRING:
                    return read_str()
                skip(t)
    except Exception as e:
        logger.debug("GGUF header read failed for %s: %s", path, e)
    return None


# Architectures llama-server serves as pure embedding endpoints (not chat).
_EMBEDDING_ARCHS = {"bert", "nomic-bert", "jina-bert-v2", "gte", "snowflake-arctic-embed"}

# Architecture substrings that llama-server CANNOT serve at all.
_UNSUPPORTED_HINTS = (
    "diffusion", "dream", "llada", "clip", "whisper",
    "t5encoder", "mmproj", "wavtokenizer",
)


def classify_architecture(arch: Optional[str]) -> Optional[str]:
    """Return 'chat' | 'embedding' | 'unsupported' from a GGUF architecture string.

    Returns None when arch is None (unknown / unreadable header).
    """
    if not arch:
        return None
    a = arch.lower()
    if a in _EMBEDDING_ARCHS or "embed" in a or a.endswith("-bert") or a == "bert":
        return "embedding"
    if any(h in a for h in _UNSUPPORTED_HINTS):
        return "unsupported"
    return "chat"
