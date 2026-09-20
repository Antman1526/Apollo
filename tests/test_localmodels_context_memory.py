"""Context windows capped at each model's real limit, memory-aware launches,
KV-cache precision, MLX budgets, and compaction that never drops history."""
import asyncio
import json
import struct
from unittest.mock import patch

from services.localmodels import config, server_manager
from services.localmodels.gguf_meta import read_metadata
from services.localmodels.scanner import LocalModel, scan_dirs
from services.localmodels.server_manager import LocalModelServer, _Proc
from src import context_compactor


def _kv_str(key, val):
    k, v = key.encode(), val.encode()
    return struct.pack("<Q", len(k)) + k + struct.pack("<I", 8) + struct.pack("<Q", len(v)) + v


def _kv_u32(key, val):
    k = key.encode()
    return struct.pack("<Q", len(k)) + k + struct.pack("<I", 4) + struct.pack("<I", val)


def _write_gguf(path, arch="qwen35", context=262144):
    kvs = _kv_str("general.architecture", arch) + _kv_str("general.name", "x")
    if context:
        kvs += _kv_u32(f"{arch}.context_length", context)
    with open(path, "wb") as f:
        f.write(b"GGUF" + struct.pack("<I", 3) + struct.pack("<Q", 0)
                + struct.pack("<Q", 3 if context else 2) + kvs)


def _m(name, backend="llama.cpp", native=None, kind="chat"):
    return LocalModel(id="lm_" + name, name=name, path=f"/m/{name}.gguf", quant="", kind=kind,
                      size_bytes=1, directory="/m", arch="qwen35", backend=backend,
                      native_context=native)


# -- the model's own limit ----------------------------------------------------

def test_gguf_header_yields_architecture_and_context(tmp_path):
    p = tmp_path / "m.gguf"
    _write_gguf(str(p), "qwen35", 262144)
    assert read_metadata(str(p)) == {"architecture": "qwen35", "context_length": 262144}
    _write_gguf(str(p), "llama", 0)
    assert read_metadata(str(p)) == {"architecture": "llama", "context_length": None}
    assert read_metadata(str(tmp_path / "missing.gguf")) == {"architecture": None, "context_length": None}


def test_scanner_records_native_context_for_gguf_and_mlx(tmp_path):
    _write_gguf(str(tmp_path / "Swift-Q8_0.gguf"), "qwen35", 262144)
    d = tmp_path / "Qwythos-MLX"
    d.mkdir()
    (d / "config.json").write_text(json.dumps({"model_type": "qwen3_5", "quantization": {"bits": 4},
                                               "max_position_embeddings": 1048576}))
    (d / "model.safetensors").write_bytes(b"\0")
    with patch("services.localmodels.mlx.supported_types", return_value=frozenset({"qwen3_5"})), \
         patch("services.localmodels.mlx.tool_parsers", return_value={}):
        models = {m.name: m for m in scan_dirs([str(tmp_path)])}
    assert models["Swift-Q8_0"].native_context == 262144
    assert models["Qwythos-MLX"].native_context == 1048576


def test_serving_context_is_capped_at_the_models_limit():
    srv = LocalModelServer(dirs_provider=lambda: [])
    m = _m("Swift", native=262144)
    with patch.object(server_manager, "get_local_context", return_value=1048576):
        assert srv._serving_context(m) == 262144
    with patch.object(server_manager, "get_local_context", return_value=16384):
        assert srv._serving_context(m) == 16384
    with patch.object(server_manager, "get_local_context", return_value=0):
        assert srv._serving_context(m) == 0
    # Header didn't say: the known-models table, else the setting itself.
    with patch.object(server_manager, "get_local_context", return_value=1048576), \
         patch("src.model_context._lookup_known", return_value=131072):
        assert srv._serving_context(_m("Hermes")) == 131072
    with patch.object(server_manager, "get_local_context", return_value=524288), \
         patch("src.model_context._lookup_known", return_value=None):
        assert srv._serving_context(_m("Mystery")) == 524288


def test_mlx_budget_comes_from_its_config():
    srv = LocalModelServer(dirs_provider=lambda: [])
    srv.set_catalog([_m("Qwen-MLX", backend="mlx", native=262144), _m("NoCfg-MLX", backend="mlx")])
    with patch.object(server_manager, "get_local_context", return_value=0):
        assert srv.served_context("Qwen-MLX") == 262144
    with patch.object(server_manager, "get_local_context", return_value=32768):
        assert srv.served_context("Qwen-MLX") == 32768
    assert srv.served_context("NoCfg-MLX") is None


# -- KV cache -----------------------------------------------------------------

def test_kv_cache_setting(monkeypatch):
    monkeypatch.delenv("APOLLO_LLAMA_KV_CACHE", raising=False)
    with patch.object(config, "load_settings", return_value={}):
        assert config.get_local_kv_cache() == "q8_0"
    with patch.object(config, "load_settings", return_value={"local_model_kv_cache": "f16"}):
        assert config.get_local_kv_cache() == "f16"
    with patch.object(config, "load_settings", return_value={"local_model_kv_cache": "bogus"}):
        assert config.get_local_kv_cache() == "q8_0"
    saved = {}
    with patch.object(config, "load_settings", return_value={}), \
         patch.object(config, "save_settings", side_effect=saved.update):
        assert config.set_local_kv_cache("F16") == "f16"
        assert saved["local_model_kv_cache"] == "f16"
        try:
            config.set_local_kv_cache("q4")
        except ValueError:
            pass
        else:
            raise AssertionError("q4 accepted")


def test_llama_command_kv_flags(tmp_path):
    srv = LocalModelServer(dirs_provider=lambda: [])
    with patch.object(srv, "find_binary", return_value="/usr/bin/true"):
        q8 = srv._llama_command(_m("Swift", native=262144), 9001, 16384, "q8_0")
        f16 = srv._llama_command(_m("Swift", native=262144), 9001, 16384, "f16")
        emb = srv._llama_command(_m("nomic", kind="embedding"), 9001, 4096, "q8_0")
    assert q8[q8.index("-c") + 1] == "16384"
    assert q8[q8.index("--cache-type-k") + 1] == "q8_0" and "--cache-type-v" in q8
    assert "--cache-type-k" not in f16
    assert "--cache-type-k" not in emb and "--embedding" in emb


# -- memory-failure fallback --------------------------------------------------

class _Alive:
    def poll(self):
        return None


def _fake_proc(m, attempt):
    return _Proc(m.id, m.name, m.kind, 9001, _Alive(), "http://127.0.0.1:9001",
                 kv_cache=attempt[1] if attempt else "")


def test_launch_backs_off_on_memory_failure_only():
    srv = LocalModelServer(dirs_provider=lambda: [])
    m = _m("Swift", native=262144)
    tried = []

    def launch_once(mm, attempt):
        tried.append(attempt)
        if len(tried) < 3:
            raise RuntimeError("llama-server exited early (code 1); full log: /tmp/x\n"
                               "ggml_backend_metal_buffer_type_alloc_buffer: failed to allocate buffer")
        return _fake_proc(mm, attempt)

    with patch.object(srv, "_launch_once", side_effect=launch_once), \
         patch.object(server_manager, "get_local_context", return_value=262144), \
         patch.object(server_manager, "get_local_kv_cache", return_value="q8_0"):
        proc = srv._launch(m)
    assert tried == [(262144, "q8_0"), (262144, "f16"), (0, "q8_0")]
    assert proc.kv_cache == "q8_0"

    tried.clear()

    def bad_arch(mm, attempt):
        tried.append(attempt)
        raise RuntimeError("llama-server exited early (code 1)\nerror loading model: unknown model architecture: 'k2-horizon'")

    with patch.object(srv, "_launch_once", side_effect=bad_arch), \
         patch.object(server_manager, "get_local_context", return_value=16384), \
         patch.object(server_manager, "get_local_kv_cache", return_value="q8_0"):
        try:
            srv._launch(m)
        except RuntimeError as e:
            assert "unknown model architecture" in str(e)
        else:
            raise AssertionError("should have raised")
    assert tried == [(16384, "q8_0")]  # no pointless retries


def test_auto_context_with_f16_has_a_single_attempt():
    srv = LocalModelServer(dirs_provider=lambda: [])
    tried = []

    def launch_once(mm, attempt):
        tried.append(attempt)
        raise RuntimeError("failed to allocate")

    with patch.object(srv, "_launch_once", side_effect=launch_once), \
         patch.object(server_manager, "get_local_context", return_value=0), \
         patch.object(server_manager, "get_local_kv_cache", return_value="f16"):
        try:
            srv._launch(_m("Swift"))
        except RuntimeError:
            pass
    assert tried == [(0, "f16")]


# -- compaction that keeps history --------------------------------------------

def _run(coro):
    return asyncio.run(coro)


def _long_chat(n=40, chars=1500):
    msgs = [{"role": "system", "content": "sys"}]
    for i in range(n):
        msgs.append({"role": "user" if i % 2 == 0 else "assistant", "content": f"m{i} " + "x" * chars})
    return msgs


def test_compaction_summarises_in_bounded_chunks(monkeypatch):
    calls = []

    async def fake_llm(url, model, messages, **kw):
        calls.append((messages[-1]["content"], kw.get("timeout")))
        return f"summary {len(calls)}"

    monkeypatch.setattr(context_compactor, "llm_call_async", fake_llm)
    monkeypatch.setattr(context_compactor, "get_context_length", lambda u, m: 4000)
    monkeypatch.setattr(context_compactor, "resolve_endpoint", lambda role: (None, None, None))
    msgs = _long_chat()
    out, ctx, compacted = _run(context_compactor.maybe_compact(None, "local://llama.cpp", "Swift", msgs))
    assert compacted is True
    assert len(calls) >= 2, "older half should be summarised in more than one chunk"
    for text, timeout in calls:
        assert context_compactor.estimate_tokens([{"role": "user", "content": text}]) <= context_compactor.COMPACT_CHUNK_TOKENS + 50
        assert timeout == 180  # local models get time
    summary = next(m for m in out if m["role"] == "system" and "[Conversation summary" in m["content"])
    assert "summary 1" in summary["content"] and f"summary {len(calls)}" in summary["content"]


def test_compaction_failure_keeps_the_conversation(monkeypatch):
    async def boom(*a, **kw):
        raise TimeoutError("slow")

    monkeypatch.setattr(context_compactor, "llm_call_async", boom)
    monkeypatch.setattr(context_compactor, "get_context_length", lambda u, m: 4000)
    monkeypatch.setattr(context_compactor, "resolve_endpoint", lambda role: (None, None, None))
    msgs = _long_chat()
    out, ctx, compacted = _run(context_compactor.maybe_compact(None, "http://127.0.0.1:1/v1", "Swift", msgs))
    assert compacted is False
    assert out == msgs  # nothing silently dropped; trim_for_context decides


# -- settings API -------------------------------------------------------------

def test_kv_cache_api(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import routes.localmodels_routes as lr

    saved, stopped = {}, []

    class _Srv:
        def status(self):
            return {"lm_a": {"name": "Swift", "kind": "chat"}}

        def stop(self, mid):
            stopped.append(mid)
            return True

    monkeypatch.delenv("APOLLO_LLAMA_KV_CACHE", raising=False)
    monkeypatch.setattr(lr, "require_admin", lambda request: None)
    monkeypatch.setattr(config, "load_settings", lambda: dict(saved))
    monkeypatch.setattr(config, "save_settings", saved.update)
    monkeypatch.setattr(lr, "get_server", lambda: _Srv())
    app = FastAPI()
    app.include_router(lr.setup_localmodels_routes())
    c = TestClient(app)
    assert c.get("/api/local-models/kv-cache").json() == {"kv_cache": "q8_0"}
    assert c.put("/api/local-models/kv-cache", json={"kv_cache": "f16"}).json() == {
        "ok": True, "kv_cache": "f16", "restarted": ["Swift"]}
    assert stopped == ["lm_a"]
    assert c.put("/api/local-models/kv-cache", json={"kv_cache": "q4"}).status_code == 400
