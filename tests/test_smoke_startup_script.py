from pathlib import Path


def test_startup_smoke_uses_isolated_data_and_terminates_child_process():
    source = (Path(__file__).resolve().parents[1] / "scripts" / "smoke_startup.py").read_text(encoding="utf-8")

    assert '"APOLLO_DATA_DIR"' in source
    assert '"AUTH_ENABLED": "false"' in source
    assert "/api/health" in source
    assert "/openapi.json" in source
    assert "process.terminate()" in source
