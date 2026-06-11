"""SearxngRuntime lifecycle with fake spawn/health."""
import os
import threading
import time

import services.searxng.runtime as _runtime_mod
from services.searxng.config import SearxngConfig
from services.searxng.runtime import SearxngRuntime


def _cfg(tmp_path, enabled=True, installed=True, port=9001):
    cfg = SearxngConfig(enabled=enabled, port=port, home=str(tmp_path))
    if installed:
        os.makedirs(os.path.dirname(cfg.venv_python), exist_ok=True)
        open(cfg.venv_python, "w").close()
        open(cfg.settings_path, "w").close()
    return cfg


class FakeProc:
    def __init__(self, *a, **kw):
        self.args = a
        self.kwargs = kw
        self.killed = False

    def poll(self):
        return 1 if self.killed else None

    def terminate(self):
        self.killed = True

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.killed = True


def test_disabled_is_noop(tmp_path):
    rt = SearxngRuntime(cfg_provider=lambda: _cfg(tmp_path, enabled=False),
                        spawn=FakeProc, health_check=lambda u, t=2.0: False)
    assert rt.start() is False
    assert rt.status() == "disabled"


def test_not_installed(tmp_path):
    rt = SearxngRuntime(cfg_provider=lambda: _cfg(tmp_path, installed=False),
                        spawn=FakeProc, health_check=lambda u, t=2.0: False)
    assert rt.start() is False
    assert rt.status() == "not_installed"


def test_start_spawns_webapp_with_settings_env(tmp_path):
    spawned = []

    def spawn(*a, **kw):
        p = FakeProc(*a, **kw)
        spawned.append(p)
        return p

    calls = {"n": 0}

    def check(u, t=2.0):
        calls["n"] += 1
        return calls["n"] > 1  # unhealthy pre-spawn, healthy after

    rt = SearxngRuntime(cfg_provider=lambda: _cfg(tmp_path), spawn=spawn, health_check=check)
    assert rt.start() is True
    cmd = spawned[0].args[0]
    assert cmd[1:] == ["-m", "searx.webapp"]
    env = spawned[0].kwargs["env"]
    assert env["SEARXNG_SETTINGS_PATH"].endswith("settings.yml")
    assert rt.status() == "running"


def test_reuses_already_serving_instance(tmp_path):
    rt = SearxngRuntime(cfg_provider=lambda: _cfg(tmp_path),
                        spawn=FakeProc, health_check=lambda u, t=2.0: True)
    assert rt.start() is True
    assert rt.status() == "running"
    assert rt._proc is None  # nothing spawned — external/prior instance reused


def test_stop_terminates(tmp_path):
    spawned = []

    def spawn(*a, **kw):
        p = FakeProc(*a, **kw)
        spawned.append(p)
        return p

    calls = {"n": 0}

    def check(u, t=2.0):
        calls["n"] += 1
        return calls["n"] > 1

    rt = SearxngRuntime(cfg_provider=lambda: _cfg(tmp_path), spawn=spawn, health_check=check)
    rt.start()
    rt.stop()
    assert spawned and spawned[0].killed is True


def test_is_serving_caches_health(tmp_path):
    calls = []

    def check(u, t=2.0):
        calls.append(u)
        return True

    rt = SearxngRuntime(cfg_provider=lambda: _cfg(tmp_path), spawn=FakeProc, health_check=check)
    assert rt.is_serving() is True
    assert rt.is_serving() is True
    assert len(calls) == 1  # second call within TTL served from cache


def test_stop_interrupts_boot_wait(tmp_path):
    """stop() must return quickly (< 1 s wall-clock) while start() is in the
    boot-wait loop, and must not block waiting for the loop to finish."""
    spawned = []

    def spawn(*a, **kw):
        p = FakeProc(*a, **kw)
        spawned.append(p)
        return p

    # health_check always returns False after spawn, so start() will enter the
    # full 30-iteration wait loop — unless stop() interrupts it.
    health_calls = {"n": 0}

    def check(u, t=2.0):
        health_calls["n"] += 1
        # First call: pre-spawn reuse check — return False so we spawn.
        # Subsequent calls: boot-wait loop — always False.
        return False

    rt = SearxngRuntime(cfg_provider=lambda: _cfg(tmp_path),
                        spawn=spawn,
                        health_check=check)

    start_result = []
    start_exc = []

    def run_start():
        try:
            start_result.append(rt.start())
        except Exception as e:  # pragma: no cover
            start_exc.append(e)

    t = threading.Thread(target=run_start, daemon=True)
    t.start()

    # Give start() just enough time to spawn and enter the boot-wait loop.
    time.sleep(0.15)

    t0 = time.monotonic()
    rt.stop()
    elapsed = time.monotonic() - t0

    # stop() itself must return quickly — well under 1 s.
    assert elapsed < 1.0, f"stop() took {elapsed:.2f}s; expected < 1s"

    # Join the start thread (should exit promptly once stop event fires).
    t.join(timeout=2.0)
    assert not t.is_alive(), "start() thread did not exit within 2 s after stop()"

    # The spawned proc should have been killed/terminated.
    assert spawned and spawned[0].killed is True


def test_proc_exits_during_boot(tmp_path):
    """If the spawned process exits immediately (poll() returns non-None right
    after spawn), start() should return False and status() should be 'failed'."""

    class ImmediateExitProc(FakeProc):
        """Simulates a process that dies as soon as it is polled."""
        def poll(self):
            return 1  # non-zero exit right away

    rt = SearxngRuntime(cfg_provider=lambda: _cfg(tmp_path),
                        spawn=lambda *a, **kw: ImmediateExitProc(*a, **kw),
                        health_check=lambda u, t=2.0: False)
    result = rt.start()
    assert result is False
    assert rt.status() == "failed"


def test_start_after_stop_clears_stop_signal(tmp_path):
    """A stop() followed by start() must not abort its own boot wait."""
    spawned = []

    def spawn(*a, **kw):
        p = FakeProc(*a, **kw)
        spawned.append(p)
        return p

    calls = {"n": 0}

    def check(u, t=2.0):
        calls["n"] += 1
        return calls["n"] > 1  # unhealthy pre-spawn, healthy after

    rt = SearxngRuntime(cfg_provider=lambda: _cfg(tmp_path), spawn=spawn, health_check=check)
    rt.stop()  # sets the stopping event with nothing running
    assert rt.start() is True  # must clear the event and boot normally
    assert spawned, "start() should have spawned after a prior stop()"


def test_maybe_restart_respawns_dead_proc(tmp_path):
    spawned = []

    def spawn(*a, **kw):
        p = FakeProc(*a, **kw)
        spawned.append(p)
        return p

    calls = {"n": 0}

    def check(u, t=2.0):
        calls["n"] += 1
        return calls["n"] in (2,)  # healthy once after first spawn, then dead

    rt = SearxngRuntime(cfg_provider=lambda: _cfg(tmp_path), spawn=spawn, health_check=check)
    rt.start()
    assert len(spawned) == 1
    spawned[0].killed = True          # simulate crash
    rt._health_cache = None
    assert rt.maybe_restart() is True  # schedules a restart
    import time as _t
    for _ in range(50):                # restart happens on a background thread
        if len(spawned) >= 2:
            break
        _t.sleep(0.05)
    assert len(spawned) == 2


def test_maybe_restart_rate_limited(tmp_path):
    rt = SearxngRuntime(cfg_provider=lambda: _cfg(tmp_path),
                        spawn=FakeProc, health_check=lambda u, t=2.0: False)
    rt._last_restart_attempt = None
    assert rt.maybe_restart() is True
    assert rt.maybe_restart() is False  # within the cooldown window


def test_spawn_env_is_minimal(tmp_path, monkeypatch):
    """The sidecar must not inherit Apollo's secrets (API keys etc.)."""
    monkeypatch.setenv("TAVILY_API_KEY", "sk-secret")
    monkeypatch.setenv("DATA_BRAVE_API_KEY", "sk-secret2")
    spawned = []

    def spawn(*a, **kw):
        p = FakeProc(*a, **kw)
        spawned.append(p)
        return p

    calls = {"n": 0}

    def check(u, t=2.0):
        calls["n"] += 1
        return calls["n"] > 1

    rt = SearxngRuntime(cfg_provider=lambda: _cfg(tmp_path), spawn=spawn, health_check=check)
    assert rt.start() is True
    env = spawned[0].kwargs["env"]
    assert "TAVILY_API_KEY" not in env
    assert "DATA_BRAVE_API_KEY" not in env
    assert env["SEARXNG_SETTINGS_PATH"].endswith("settings.yml")
    assert "PATH" in env


def test_sidecar_stdout_points_at_log_file(tmp_path, monkeypatch):
    """stdout/stderr passed to spawn must be a file handle for searxng.log,
    not DEVNULL.  We monkeypatch the module-level _LOG_PATH to a tmp file so
    no real filesystem side-effects escape the test."""
    log_file = str(tmp_path / "searxng.log")
    monkeypatch.setattr(_runtime_mod, "_LOG_PATH", log_file)

    spawned = []

    def spawn(*a, **kw):
        p = FakeProc(*a, **kw)
        spawned.append(p)
        return p

    calls = {"n": 0}

    def check(u, t=2.0):
        calls["n"] += 1
        return calls["n"] > 1

    rt = SearxngRuntime(cfg_provider=lambda: _cfg(tmp_path), spawn=spawn, health_check=check)
    assert rt.start() is True
    assert spawned, "nothing was spawned"
    stdout_arg = spawned[0].kwargs.get("stdout")
    # Must be a real file object (not subprocess.DEVNULL == -1)
    assert hasattr(stdout_arg, "name"), "stdout should be a file object with .name"
    assert stdout_arg.name.endswith("searxng.log"), (
        f"stdout.name should end with searxng.log, got: {stdout_arg.name!r}"
    )
    # stderr must point at the same file
    assert spawned[0].kwargs.get("stderr") is stdout_arg
    # stop() must close the handle
    rt.stop()
    assert stdout_arg.closed, "log file handle should be closed after stop()"
