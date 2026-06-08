import os

from services.paperclip import runtime
from services.paperclip.config import PaperclipConfig


def _cfg(mode="native", enabled=True, port=3100):
    return PaperclipConfig(
        enabled=enabled, mode=mode, url=f"http://localhost:{port}",
        browser_url=f"http://localhost:{port}", port=port,
        model_endpoint="apollo", model_base_url="", model_name="",
    )


def test_build_env_points_opencode_at_proxy():
    env = runtime.build_env(_cfg(), proxy_token="tok", proxy_base="http://localhost:7000/lmproxy/v1",
                            base_env={"PATH": "/usr/bin"})
    assert env["PORT"] == "3100"
    assert env["HOST"] == "127.0.0.1"
    assert env["OPENAI_BASE_URL"] == "http://localhost:7000/lmproxy/v1"
    assert env["OPENAI_API_KEY"] == "tok"
    assert env["OPENCODE_ALLOW_ALL_MODELS"] == "true"
    # base env preserved
    assert env["PATH"] == "/usr/bin"


def test_build_command_uses_explicit_cli_when_set():
    cmd = runtime.build_command(node="/opt/node", npx="/opt/npx", cli="/x/paperclipai", version="2026.529.0")
    assert cmd == ["/opt/node", "/x/paperclipai", "run"]


def test_build_command_falls_back_to_npx_pinned():
    cmd = runtime.build_command(node="/opt/node", npx="/opt/npx", cli=None, version="2026.529.0")
    assert cmd == ["/opt/npx", "-y", "paperclipai@2026.529.0", "run"]


def test_find_node_prefers_bundled(monkeypatch, tmp_path):
    bundled = tmp_path / "node"
    bundled.write_text("#!/bin/sh\n")
    bundled.chmod(0o755)
    monkeypatch.setenv("PAPERCLIP_NODE_BIN", str(bundled))
    assert runtime.find_node() == str(bundled)


def test_find_node_returns_none_when_absent(monkeypatch):
    monkeypatch.delenv("PAPERCLIP_NODE_BIN", raising=False)
    # which() finds nothing, candidates all missing
    assert runtime.find_node(which=lambda _n: None, candidates=["/nope/node"]) is None


def test_runtime_disabled_is_noop_start():
    rt = runtime.PaperclipRuntime(_cfg(enabled=False), proxy_token_provider=lambda: "t",
                                  proxy_base_provider=lambda: "http://localhost:7000/lmproxy/v1")
    assert rt.start() is False  # disabled → does not spawn


def test_runtime_external_mode_is_noop_start():
    rt = runtime.PaperclipRuntime(_cfg(mode="external"), proxy_token_provider=lambda: "t",
                                  proxy_base_provider=lambda: "http://localhost:7000/lmproxy/v1")
    assert rt.start() is False  # external → user/Docker owns the process


def test_runtime_spawns_in_native_mode(monkeypatch):
    calls = {}

    class FakeProc:
        def poll(self):
            return None

        def terminate(self):
            calls["terminated"] = True

        def wait(self, timeout=None):
            return 0

    def fake_spawn(cmd, env=None, **kw):
        calls["cmd"] = cmd
        calls["env"] = env
        return FakeProc()

    monkeypatch.setenv("PAPERCLIP_CLI", "/x/paperclipai")
    rt = runtime.PaperclipRuntime(
        _cfg(), proxy_token_provider=lambda: "tok",
        proxy_base_provider=lambda: "http://localhost:7000/lmproxy/v1",
        spawn=fake_spawn, health_check=lambda url, timeout=0: True,
        node_finder=lambda: "/opt/node",
        npx_finder=lambda: "/opt/npx",
    )
    assert rt.start() is True
    assert calls["cmd"] == ["/opt/node", "/x/paperclipai", "run"]
    assert calls["env"]["OPENAI_API_KEY"] == "tok"
    rt.stop()
    assert calls.get("terminated") is True
