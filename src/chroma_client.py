"""
chroma_client.py

Singleton ChromaDB client.

Two modes, chosen automatically:
  • Embedded (default): a local on-disk ``PersistentClient`` under ``data/chroma``.
    No separate service needed — this is what the native desktop app uses.
  • HTTP: an ``HttpClient`` to a standalone ChromaDB service. Used when
    ``CHROMADB_HOST`` is explicitly set (e.g. Docker Compose sets
    ``CHROMADB_HOST=chromadb``).
"""

import os
import socket
import logging

logger = logging.getLogger(__name__)

_client = None

# Repo root (parent of this src/ dir) so the embedded store resolves to a
# stable absolute path regardless of the process working directory.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# A short connect probe so an unreachable ChromaDB fails fast instead of
# blocking on the OS connection timeout (~30-60s, WinError 10060 on Windows),
# which otherwise stalls app startup. Tunable via CHROMADB_CONNECT_TIMEOUT.
_CONNECT_TIMEOUT = float(os.getenv("CHROMADB_CONNECT_TIMEOUT", "2.0"))


def _port_open(host: str, port: int, timeout: float = None) -> bool:
    """Return True if a TCP connection to host:port succeeds within timeout."""
    try:
        with socket.create_connection((host, port), timeout=timeout or _CONNECT_TIMEOUT):
            return True
    except OSError:
        return False


def _persist_dir() -> str:
    """Absolute path of the embedded ChromaDB store."""
    d = os.getenv("CHROMA_PERSIST_DIR", "").strip() or os.path.join(_REPO_ROOT, "data", "chroma")
    return d if os.path.isabs(d) else os.path.join(_REPO_ROOT, d)


def get_chroma_client():
    """Get or create the singleton ChromaDB client.

    Uses an embedded on-disk ``PersistentClient`` by default (native desktop,
    no service to run). Switches to ``HttpClient`` only when ``CHROMADB_HOST``
    is explicitly set (Docker / remote service).

    Raises RuntimeError with a clear install hint if the `chromadb` package
    is not installed — it's an optional dependency (RAG + memory vectors).
    """
    global _client
    if _client is not None:
        return _client

    try:
        import chromadb
    except ImportError as e:
        raise RuntimeError(
            "ChromaDB integration is not installed. Install the optional "
            "dependency with: pip install chromadb"
        ) from e

    host = os.getenv("CHROMADB_HOST", "").strip()

    # ── HTTP mode: only when a service host is explicitly configured ──
    if host:
        port = int(os.getenv("CHROMADB_PORT", "8000"))
        if not _port_open(host, port):
            raise RuntimeError(
                f"ChromaDB is not reachable at {host}:{port}. Start the ChromaDB "
                f"service (e.g. `docker compose up chromadb`) or unset CHROMADB_HOST "
                f"to use the built-in embedded store."
            )
        client = chromadb.HttpClient(host=host, port=port)
        # Health check before caching — if the port is open but the service
        # isn't healthy yet, don't poison the singleton with a dead client.
        client.heartbeat()
        _client = client
        logger.info(f"ChromaDB connected (http): {host}:{port}")
        return _client

    # ── Embedded mode (default): local on-disk store, no service required ──
    path = _persist_dir()
    os.makedirs(path, exist_ok=True)
    _client = chromadb.PersistentClient(path=path)
    logger.info(f"ChromaDB connected (embedded): {path}")
    return _client


def reset_client():
    """Reset the singleton (e.g. after config change)."""
    global _client
    _client = None
