import importlib.util
import subprocess
import sys
import time
from pathlib import Path


def test_startup_smoke_uses_isolated_data_and_terminates_child_process():
    source = (Path(__file__).resolve().parents[1] / "scripts" / "smoke_startup.py").read_text(encoding="utf-8")

    assert '"APOLLO_DATA_DIR"' in source
    assert '"AUTH_ENABLED": "false"' in source
    assert "/api/health" in source
    assert "/openapi.json" in source
    assert "process.terminate()" in source


def _load_smoke_module():
    source = Path(__file__).resolve().parents[1] / "scripts" / "smoke_startup.py"
    spec = importlib.util.spec_from_file_location("smoke_startup_test", source)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_failure_message_does_not_hang_on_a_live_process_with_a_full_pipe():
    """Regression: a plain `process.stdout.read()` blocks until EOF, which
    never comes while the child is alive — this is exactly what happens when
    the health-check deadline fires but the server is merely slow, not stuck
    (observed on a loaded dev machine: boot exceeded 45s, then this call hung
    smoke_startup.py indefinitely even though the server was healthy)."""
    smoke = _load_smoke_module()
    # A real subprocess that keeps writing past the OS pipe buffer and never
    # exits on its own reproduces the "full pipe, still alive" condition —
    # a mock stdout can't, since the deadlock is specifically about the pipe
    # buffer filling while nothing drains it.
    process = subprocess.Popen(
        [sys.executable, "-c", (
            "import sys, time\n"
            "while True:\n"
            "    sys.stdout.write('x' * 65536)\n"
            "    sys.stdout.flush()\n"
            "    time.sleep(0.01)\n"
        )],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    try:
        time.sleep(0.5)  # let it fill the pipe buffer at least once
        started = time.monotonic()
        message = smoke._failure_message(process)
        elapsed = time.monotonic() - started
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
    assert elapsed < 15, f"_failure_message took {elapsed:.1f}s — pipe deadlock regression"
    assert "Apollo startup failed" in message
    assert process.poll() is not None  # terminated as a side effect
