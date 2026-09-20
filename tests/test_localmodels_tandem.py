"""Helper model beside the main one; busy guard; idle unload; persisted tool
verdicts; thinking budget; compaction backoff."""
import asyncio
import json
import os
import threading
import time
from unittest.mock import patch

from services.localmodels import config, helper, server_manager
from services.localmodels.scanner import LocalModel
from services.localmodels.server_manager import LocalModelServer, _Proc
from src import context_compactor, endpoint_resolver, llm_core


def _m(name, gb=5.0, arch="qwen35", ctx=262144, backend="llama.cpp", kind="chat", tools=None):
    return LocalModel(id="lm_" + name, name=name, path=f"/m/{name}", quant="", kind=kind,
                      size_bytes=int(gb * 2**30), directory="/m", arch=arch, backend=backend,
                      tools=tools, native_context=ctx)


class _Alive:
    def poll(self):
        return None

    def terminate(self):
        pass

    def wait(self, timeout=None):
        pass


def _srv(models, launched=None):
    srv = LocalModelServer(dirs_provider=lambda: [])
    srv.set_catalog(models)
    launched = launched if launched is not None else []

    def fake_launch(mm):
        proc = _Proc(mm.id, mm.name, mm.kind, 9000 + len(launched), _Alive(), f"http://127.0.0.1:{9000 + len(launched)}")
        launched.append(proc)
        return proc

    srv._launch = fake_launch  # type: ignore[assignment]
    srv._start_idle_watch = lambda: None  # no background thread in tests
    return srv


# -- picking the helper -------------------------------------------------------

LIB = [_m("Swift-27B", 27.1), _m("Qwen3VL-8B", 4.7, arch="qwen3vl"), _m("Mistral-7B", 4.1, arch="llama", ctx=32768),
       _m("Phi-3.5", 2.2, arch="phi3"), _m("K2-7B", 6.9, arch="k2-horizon", ctx=524288),
       _m("Qwen3.5-9B", 8.9), _m("Hermes-8B", 4.6, arch="llama", ctx=131072),
       _m("Small-MLX", 4.7, backend="mlx"), _m("nomic", 0.3, kind="embedding"), _m("NoTools-5B", 5.0, tools=False)]


def test_auto_pick_prefers_newest_family_then_smallest():
    assert helper.pick_helper(LIB).name == "Qwen3VL-8B"
    assert helper.pick_helper([m for m in LIB if m.name != "Qwen3VL-8B"]).name == "Qwen3.5-9B"
    assert helper.pick_helper([_m("Swift-27B", 27.1), _m("Phi-3.5", 2.2, arch="phi3")]) is None  # too big / too small


def test_configured_helper_wins_and_auto_can_be_off():
    with patch.object(helper, "load_settings", return_value={"helper_model": "Hermes-8B"}):
        assert helper.get_helper(LIB).name == "Hermes-8B"
    with patch.object(helper, "load_settings", return_value={"helper_model": "gone", "helper_model_auto": True}):
        assert helper.get_helper(LIB).name == "Qwen3VL-8B"
    with patch.object(helper, "load_settings", return_value={"helper_model": "", "helper_model_auto": False}):
        assert helper.get_helper(LIB) is None


def test_helper_fills_utility_and_light_but_not_explicit_settings():
    with patch.object(endpoint_resolver, "_helper_endpoint", return_value=("local://llama.cpp/chat/completions", "Qwen3VL-8B", {})), \
         patch("src.settings.load_settings", return_value={"utility_endpoint_id": "", "light_endpoint_id": "", "default_endpoint_id": "", "task_endpoint_id": ""}), \
         patch("src.settings.get_user_setting", lambda k, o, d: d):
        # A local session gets the helper for these roles...
        assert endpoint_resolver.resolve_endpoint("utility", "local://llama.cpp/chat/completions", "Swift-27B", {})[1] == "Qwen3VL-8B"
        assert endpoint_resolver.resolve_endpoint("light", "local://llama.cpp/chat/completions", "Swift-27B", {})[1] == "Qwen3VL-8B"
        # ...a cloud session keeps its own model, a GGUF on disk notwithstanding.
        assert endpoint_resolver.resolve_endpoint("utility", "https://api.openai.com/v1", "gpt-5", {})[1] == "gpt-5"
        assert endpoint_resolver.resolve_endpoint("light", "https://api.openai.com/v1", "gpt-5", {})[1] == "gpt-5"
        # Other roles (task, research) are untouched by the helper.
        assert endpoint_resolver.resolve_endpoint("task", "local://llama.cpp/chat/completions", "Swift-27B", {})[1] == "Swift-27B"
    # No session given: only when the default chat model is local.
    with patch.object(endpoint_resolver, "_helper_endpoint", return_value=("local://llama.cpp/chat/completions", "Qwen3VL-8B", {})), \
         patch.object(endpoint_resolver, "_session_is_local", return_value=True), \
         patch("src.settings.load_settings", return_value={"utility_endpoint_id": "", "default_endpoint_id": ""}), \
         patch("src.settings.get_user_setting", lambda k, o, d: d):
        assert endpoint_resolver.resolve_endpoint("utility")[1] == "Qwen3VL-8B"
    with patch.object(endpoint_resolver, "_helper_endpoint", return_value=None), \
         patch("src.settings.load_settings", return_value={"utility_endpoint_id": ""}), \
         patch("src.settings.get_user_setting", lambda k, o, d: d):
        assert endpoint_resolver.resolve_endpoint("utility", "http://x", "big-model", {})[1] == "big-model"


# -- the helper slot -----------------------------------------------------------

def test_helper_runs_beside_the_main_model():
    launched = []
    srv = _srv([_m("Swift-27B", 27.1), _m("Qwen3VL-8B", 4.7, arch="qwen3vl")], launched)
    with patch.object(helper, "load_settings", return_value={}):
        srv.ensure_running("Swift-27B")
        srv.ensure_running("Qwen3VL-8B")
        srv.ensure_running("Swift-27B")  # still warm: no relaunch
    assert [p.name for p in launched] == ["Swift-27B", "Qwen3VL-8B"]
    st = srv.status()
    assert {v["name"]: v["role"] for v in st.values()} == {"Swift-27B": "chat", "Qwen3VL-8B": "helper"}


# -- busy guard ---------------------------------------------------------------

def test_eviction_waits_for_the_reply_in_flight():
    launched = []
    srv = _srv([_m("A", 5), _m("B", 5)], launched)
    with patch.object(helper, "load_settings", return_value={"helper_model_auto": False}):
        srv.ensure_running("A")
        released = threading.Event()

        def hold():
            with srv.lease("A"):
                released.wait()

        t = threading.Thread(target=hold)
        t.start()
        time.sleep(0.05)
        assert srv.status()["lm_A"]["in_flight"] == 1
        start = time.monotonic()
        threading.Timer(0.6, released.set).start()
        with patch.object(server_manager, "EVICT_WAIT_SECONDS", 5.0):
            srv.ensure_running("B")  # must wait for A's lease before evicting it
        assert time.monotonic() - start >= 0.5
        t.join(timeout=2)
    assert [p.name for p in launched] == ["A", "B"]


def test_local_lease_is_a_noop_for_remote_endpoints():
    import contextlib
    assert isinstance(llm_core._local_lease("https://api.openai.com/v1", "gpt"), contextlib.nullcontext)


# -- idle unload --------------------------------------------------------------

def test_idle_models_are_unloaded_but_busy_ones_are_not():
    srv = _srv([_m("A", 5), _m("B", 5)])
    with patch.object(helper, "load_settings", return_value={"helper_model": "B"}):
        srv.ensure_running("A")
        srv.ensure_running("B")
    now = time.monotonic()
    with patch.object(server_manager, "get_idle_minutes", return_value=30):
        assert srv.unload_idle(now=now + 29 * 60) == []
        with srv.lease("B"):
            assert srv.unload_idle(now=now + 31 * 60) == ["A"]  # B is answering
    with patch.object(server_manager, "get_idle_minutes", return_value=0):
        assert srv.unload_idle(now=now + 10**6) == []  # 0 = never


# -- persisted tool verdicts ---------------------------------------------------

def test_runtime_tool_verdicts_survive_a_restart(tmp_path):
    (tmp_path / "Coder").write_text("")
    m = _m("Coder"); m.path = str(tmp_path / "Coder")
    with patch("src.constants.DATA_DIR", str(tmp_path)):
        srv = _srv([m])
        srv.set_tool_caps("Coder", False)
        assert json.load(open(tmp_path / "local_tool_caps.json")) == {m.path: False}
        srv2 = _srv([m])  # a fresh server, as after an app restart
        assert srv2.supports_tool_calls("Coder") is False


# -- thinking budget ----------------------------------------------------------

def test_reasoning_budget_flag_only_when_set():
    srv = _srv([_m("A", 5)])
    with patch.object(srv, "find_binary", return_value="/usr/bin/true"):
        with patch.object(server_manager, "get_reasoning_budget", return_value=-1):
            assert "--reasoning-budget" not in srv._llama_command(_m("A", 5), 9001, 16384, "f16")
        with patch.object(server_manager, "get_reasoning_budget", return_value=2048):
            cmd = srv._llama_command(_m("A", 5), 9001, 16384, "f16")
            assert cmd[cmd.index("--reasoning-budget") + 1] == "2048"
        with patch.object(server_manager, "get_reasoning_budget", return_value=0):
            assert "--reasoning-budget" not in srv._llama_command(_m("e", 1, kind="embedding"), 9001, 4096, "f16")
    with patch.object(config, "load_settings", return_value={"local_model_reasoning_budget": "abc"}):
        assert config.get_reasoning_budget() == -1


# -- compaction backoff -------------------------------------------------------

class _Session:
    history = []


def test_compaction_backs_off_after_repeated_failures(monkeypatch):
    calls = []

    async def boom(*a, **kw):
        calls.append(1)
        raise TimeoutError("slow")

    monkeypatch.setattr(context_compactor, "llm_call_async", boom)
    monkeypatch.setattr(context_compactor, "get_context_length", lambda u, m: 4000)
    monkeypatch.setattr(context_compactor, "resolve_endpoint", lambda role, **kw: (None, None, None))
    msgs = [{"role": "system", "content": "sys"}] + [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"m{i} " + "x" * 1500} for i in range(40)]
    sess = _Session()
    for _ in range(3):
        asyncio.run(context_compactor.maybe_compact(sess, "http://127.0.0.1:1/v1", "M", msgs))
    assert len(calls) == 2  # third turn skipped: backing off
    assert sess._compact_failures == 2

    async def ok(*a, **kw):
        return "summary"

    monkeypatch.setattr(context_compactor, "llm_call_async", ok)
    sess._compact_skip_until = 0
    _, _, compacted = asyncio.run(context_compactor.maybe_compact(sess, "http://127.0.0.1:1/v1", "M", msgs))
    assert compacted is True and sess._compact_failures == 0


# -- settings API -------------------------------------------------------------

def test_helper_reasoning_idle_routes(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import routes.localmodels_routes as lr

    saved = {}
    monkeypatch.setattr(lr, "require_admin", lambda request: None)
    monkeypatch.setattr(config, "load_settings", lambda: dict(saved))
    monkeypatch.setattr(config, "save_settings", saved.update)
    monkeypatch.setattr("src.settings.load_settings", lambda: dict(saved))
    monkeypatch.setattr("src.settings.save_settings", saved.update)
    monkeypatch.setattr(helper, "load_settings", lambda: dict(saved))
    srv = _srv(LIB)
    monkeypatch.setattr(lr, "get_server", lambda: srv)
    app = FastAPI()
    app.include_router(lr.setup_localmodels_routes())
    c = TestClient(app)
    h = c.get("/api/local-models/helper").json()
    assert h["helper_model"] == "" and h["effective"] == "Qwen3VL-8B" and "Swift-27B" in h["options"]
    assert c.put("/api/local-models/helper", json={"helper_model": "Hermes-8B"}).json()["ok"] is True
    assert c.get("/api/local-models/helper").json()["effective"] == "Hermes-8B"
    assert c.put("/api/local-models/helper", json={"helper_model": "nope"}).status_code == 400
    assert c.put("/api/local-models/reasoning", json={"value": 2048}).json()["value"] == 2048
    assert c.get("/api/local-models/reasoning").json() == {"value": 2048}
    assert c.put("/api/local-models/reasoning", json={"value": -5}).status_code == 400
    assert c.put("/api/local-models/idle", json={"value": 10}).json()["value"] == 10
    assert c.get("/api/local-models/idle").json() == {"value": 10}


def test_route_chat_never_routes_to_the_session_model_itself():
    from services import model_router
    with patch.object(model_router, "get_setting", lambda k, d=None: True), \
         patch("src.endpoint_resolver.resolve_endpoint",
               return_value=("local://llama.cpp/chat/completions", "Swift-27B", {})):
        assert model_router.route_chat("thanks!", session_url="local://llama.cpp/chat/completions",
                                       session_model="Swift-27B") is None
    with patch.object(model_router, "get_setting", lambda k, d=None: True), \
         patch("src.endpoint_resolver.resolve_endpoint",
               return_value=("local://llama.cpp/chat/completions", "Qwen3VL-8B", {})):
        assert model_router.route_chat("thanks!", session_url="local://llama.cpp/chat/completions",
                                       session_model="Swift-27B")[1] == "Qwen3VL-8B"


def test_llm_call_async_retries_take_a_fresh_lease_each_attempt(monkeypatch):
    import httpx
    leases = []

    class _Resp:
        status_code = 200
        is_success = True
        text = ""

        def json(self):
            return {"choices": [{"message": {"content": "ok"}}]}

    class _Client:
        calls = 0

        async def post(self, *a, **kw):
            _Client.calls += 1
            if _Client.calls == 1:
                raise httpx.ReadTimeout("slow")
            return _Resp()

    monkeypatch.setattr(llm_core, "_get_http_client", lambda: _Client())
    monkeypatch.setattr(llm_core, "materialize_local_url", lambda u, m: "http://127.0.0.1:9/v1/chat/completions")
    import contextlib
    monkeypatch.setattr(llm_core, "_local_lease", lambda u, m: (leases.append(u), contextlib.nullcontext())[1])
    out = asyncio.run(llm_core.llm_call_async("local://llama.cpp/chat/completions", "Swift-27B",
                                              [{"role": "user", "content": "hi"}], max_retries=3))
    assert out == "ok" and _Client.calls == 2
    assert leases == ["local://llama.cpp/chat/completions"] * 2  # one lease per attempt


def test_fast_lane_is_enabled_once_for_older_installs():
    saved = {"mixture_routing_enabled": False}
    with patch.object(helper, "load_settings", lambda: dict(saved)), \
         patch("src.settings.save_settings", saved.update):
        assert helper.enable_fast_lane_once() is True
        assert saved["mixture_routing_enabled"] is True
        saved["mixture_routing_enabled"] = False  # the user turns it off again
        assert helper.enable_fast_lane_once() is False  # not flipped back
        assert saved["mixture_routing_enabled"] is False
