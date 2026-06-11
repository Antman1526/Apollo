"""SearxngRuntime lifecycle with fake spawn/health."""
import os

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
