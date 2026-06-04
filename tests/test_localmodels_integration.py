import os
import urllib.request
import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("APOLLO_LOCAL_MODEL_IT") != "1",
    reason="set APOLLO_LOCAL_MODEL_IT=1 to run the real llama-server integration test",
)


def test_serve_smallest_model():
    from services.localmodels.server_manager import LocalModelServer
    srv = LocalModelServer(
        dirs_provider=lambda: ["/Volumes/MainStore/Development/AI_Models"]
    )
    srv.refresh_catalog()
    base = srv.ensure_running("Llama-3.2-1B-Instruct-Q4_K_M")
    try:
        with urllib.request.urlopen(base + "/health", timeout=5) as r:
            assert r.status == 200
    finally:
        srv.stop_all()
