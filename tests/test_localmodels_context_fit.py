"""Local models are budgeted against the context window actually served.

llama-server was launched with -c 16384 while Apollo sized prompts against
the model's advertised window (128k), so history compaction never ran and a
long chat failed with "request (33815 tokens) exceeds the available context
size (16384 tokens)".
"""
import io
import json
from unittest.mock import patch

from services.localmodels import config, server_manager
from services.localmodels.scanner import LocalModel
from services.localmodels.server_manager import LocalModelServer, _Proc
from src import llm_core, model_context


def _m(name, backend="llama.cpp"):
    return LocalModel(id="lm_" + name, name=name, path=f"/m/{name}.gguf", quant="", kind="chat",
                      size_bytes=1, directory="/m", arch="qwen35", backend=backend)


# -- the configured window ----------------------------------------------------

def test_context_setting_wins_then_env_then_default(monkeypatch):
    monkeypatch.delenv("APOLLO_LLAMA_CONTEXT", raising=False)
    with patch.object(config, "load_settings", return_value={}):
        assert config.get_local_context() == 16384
    monkeypatch.setenv("APOLLO_LLAMA_CONTEXT", "32768")
    with patch.object(config, "load_settings", return_value={}):
        assert config.get_local_context() == 32768
    with patch.object(config, "load_settings", return_value={"local_model_context": 65536}):
        assert config.get_local_context() == 65536
    with patch.object(config, "load_settings", return_value={"local_model_context": 0}):
        assert config.get_local_context() == 0  # auto


def test_set_context_validates(monkeypatch):
    saved = {}
    with patch.object(config, "load_settings", return_value={}), \
         patch.object(config, "save_settings", side_effect=saved.update):
        assert config.set_local_context(32768) == 32768
        assert saved["local_model_context"] == 32768
        assert config.set_local_context(0) == 0
        for bad in (-1, 100, 10**9):
            try:
                config.set_local_context(bad)
            except ValueError:
                pass
            else:
                raise AssertionError(f"{bad} accepted")


def test_serving_context_uses_setting_and_auto(monkeypatch):
    srv = LocalModelServer(dirs_provider=lambda: [])
    m = _m("Swift-Qwen3.8-27B-Q8_0")
    with patch.object(server_manager, "get_local_context", return_value=32768), \
         patch("src.model_context._lookup_known", return_value=262144):
        assert srv._serving_context(m) == 32768
    with patch.object(server_manager, "get_local_context", return_value=32768), \
         patch("src.model_context._lookup_known", return_value=8192):
        assert srv._serving_context(m) == 8192  # never beyond the model's own window
    with patch.object(server_manager, "get_local_context", return_value=0):
        assert srv._serving_context(m) == 0  # auto: llama.cpp --fit picks it


# -- what the budget sees -----------------------------------------------------

class _Alive:
    def poll(self):
        return None


def test_served_context_prefers_the_running_servers_n_ctx():
    m = _m("Swift-Qwen3.8-27B-Q8_0")
    srv = LocalModelServer(dirs_provider=lambda: [])
    srv.set_catalog([m])
    srv._chat = _Proc(m.id, m.name, "chat", 9001, _Alive(), "http://127.0.0.1:9001", n_ctx=40960)
    assert srv.served_context(m.name) == 40960


def test_served_context_before_launch_is_what_will_be_served():
    m = _m("Swift-Qwen3.8-27B-Q8_0")
    srv = LocalModelServer(dirs_provider=lambda: [])
    srv.set_catalog([m])
    with patch.object(srv, "_serving_context", return_value=16384):
        assert srv.served_context(m.name) == 16384
    with patch.object(srv, "_serving_context", return_value=0):
        assert srv.served_context(m.name) is None  # auto, not launched yet: unknown
    assert srv.served_context("not-ours") is None


def test_mlx_models_have_no_served_cap():
    m = _m("Qwen3.6-27B-MLX-6bit", backend="mlx")
    srv = LocalModelServer(dirs_provider=lambda: [])
    srv.set_catalog([m])
    assert srv.served_context(m.name) is None


def test_probe_n_ctx_reads_props():
    body = io.BytesIO(json.dumps({"default_generation_settings": {"n_ctx": 40960}}).encode())
    with patch("urllib.request.urlopen", return_value=body):
        assert server_manager._probe_n_ctx("http://x") == 40960
    with patch("urllib.request.urlopen", side_effect=OSError("down")):
        assert server_manager._probe_n_ctx("http://x") is None


def test_get_context_length_uses_served_window_for_local_models():
    class _Srv:
        def served_context(self, name):
            return 16384 if name == "Swift-Qwen3.8-27B-Q8_0" else None

    with patch("services.localmodels.server_manager.get_server", return_value=_Srv()):
        assert model_context.get_context_length("local://llama.cpp", "Swift-Qwen3.8-27B-Q8_0") == 16384
        # Unknown to the server (e.g. MLX): the model's own window as before.
        assert model_context.get_context_length("local://llama.cpp", "qwen3-8b") == model_context._lookup_known("qwen3-8b")


# -- when a single request still doesn't fit ---------------------------------

def test_context_overflow_error_says_what_to_do():
    body = json.dumps({"error": {
        "code": 400, "type": "exceed_context_size_error",
        "message": "request (33815 tokens) exceeds the available context size (16384 tokens), try increasing it",
        "n_prompt_tokens": 33815, "n_ctx": 16384}})
    msg = llm_core._format_upstream_error(400, body, "http://127.0.0.1:9001/v1/chat/completions")
    assert "33,815" in msg and "16,384" in msg
    assert "Context size" in msg
    assert "exceeds the available context size" not in msg  # not the raw server text


# -- settings API -------------------------------------------------------------

def test_context_api_saves_and_restarts_the_running_chat_model(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import routes.localmodels_routes as lr

    saved = {}
    stopped = []

    class _Srv:
        def status(self):
            return {"lm_a": {"name": "Swift", "kind": "chat"}, "lm_e": {"name": "nomic", "kind": "embedding"}}

        def stop(self, mid):
            stopped.append(mid)
            return True

    monkeypatch.delenv("APOLLO_LLAMA_CONTEXT", raising=False)
    monkeypatch.setattr(lr, "require_admin", lambda request: None)
    # Real get/set_local_context over an in-memory settings dict.
    monkeypatch.setattr(config, "load_settings", lambda: dict(saved))
    monkeypatch.setattr(config, "save_settings", saved.update)
    monkeypatch.setattr(lr, "get_server", lambda: _Srv())
    app = FastAPI()
    app.include_router(lr.setup_localmodels_routes())
    c = TestClient(app)
    assert c.get("/api/local-models/context").json() == {"context": 16384}
    r = c.put("/api/local-models/context", json={"context": 32768})
    assert r.json() == {"ok": True, "context": 32768, "restarted": ["Swift"]}
    assert stopped == ["lm_a"]  # the embedding model keeps running
    assert c.get("/api/local-models/context").json() == {"context": 32768}
    assert c.put("/api/local-models/context", json={"context": 5}).status_code == 400



# -- telling the user when their own message was shortened -------------------

def test_shortened_current_message_is_detected_and_explained():
    from src.context_compactor import context_notice, current_message_shortened, trim_for_context
    msgs = [{"role": "system", "content": "sys"},
            {"role": "user", "content": "x " * 20000}]
    trimmed = trim_for_context(msgs, context_length=2048, reserve_tokens=256)
    assert current_message_shortened(msgs, trimmed) is True
    small = [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}]
    assert current_message_shortened(small, trim_for_context(small, 2048)) is False
    local = context_notice(8192, "local://llama.cpp")
    assert "8,192" in local and "Context size" in local
    assert "Context size" not in context_notice(8192, "https://api.openai.com/v1")
