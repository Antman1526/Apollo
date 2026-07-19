"""Regression checks for Docker-specific startup guarantees."""

from pathlib import Path


def test_entrypoint_seeds_explicit_admin_before_starting_uvicorn():
    entrypoint = Path("docker/entrypoint.sh").read_text(encoding="utf-8")

    assert 'if [ -n "${APOLLO_ADMIN_PASSWORD:-}" ]; then' in entrypoint
    assert 'APOLLO_ADMIN_PASSWORD must be at least 8 characters' in entrypoint
    assert "from core.auth import AuthManager" in entrypoint
    assert "if not manager.setup(username, password):" in entrypoint
    assert entrypoint.index("manager.setup(username, password)") < entrypoint.index('exec gosu "$PUID:$PGID" "$@"')


def test_compose_uses_persisted_embedded_chroma_and_backups():
    compose = Path("docker-compose.yml").read_text(encoding="utf-8")

    assert "CHROMA_PERSIST_DIR=/app/data/chroma" in compose
    assert "- ./backups:/app/backups:z" in compose
    assert "  chromadb:" not in compose
    assert "CHROMADB_HOST=chromadb" not in compose
