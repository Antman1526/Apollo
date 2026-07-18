"""Regression coverage for embedding endpoint failure handling."""

import os

import pytest
from fastapi import HTTPException

from routes import embedding_routes


def _route(method: str):
    router = embedding_routes.setup_embedding_routes()
    return next(
        route.endpoint
        for route in router.routes
        if getattr(route, "path", "").endswith("/endpoint") and method in route.methods
    )


def test_load_custom_endpoint_ignores_corrupt_saved_json(tmp_path, monkeypatch):
    path = tmp_path / "embedding_endpoint.json"
    path.write_text("not-json", encoding="utf-8")
    monkeypatch.setattr(embedding_routes, "_ENDPOINT_FILE", str(path))

    assert embedding_routes._load_custom_endpoint() == {}


def test_clear_endpoint_preserves_runtime_config_when_file_delete_fails(tmp_path, monkeypatch, caplog):
    endpoint_path = tmp_path / "embedding_endpoint.json"
    endpoint_path.mkdir()
    monkeypatch.setattr(embedding_routes, "_ENDPOINT_FILE", str(endpoint_path))
    monkeypatch.setenv("EMBEDDING_URL", "http://127.0.0.1:8080/embed")

    with pytest.raises(HTTPException, match="Could not clear embedding endpoint") as error:
        _route("DELETE")()

    assert error.value.status_code == 500
    assert os.environ["EMBEDDING_URL"] == "http://127.0.0.1:8080/embed"
    assert "embedding_endpoint_config_delete_failed" in caplog.text
