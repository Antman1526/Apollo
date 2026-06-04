import pytest

from src.llm_core import materialize_local_url


def test_passthrough_for_normal_url():
    assert materialize_local_url("https://api.openai.com/v1/chat/completions",
                                 "gpt-4o") == \
        "https://api.openai.com/v1/chat/completions"


def test_non_llamacpp_local_sentinel_passes_through():
    # Unrelated local:// sentinels (e.g. embeddings) must NOT be materialized.
    assert materialize_local_url("local://fastembed", "x") == "local://fastembed"


def test_sync_llm_call_materializes_local_url(monkeypatch):
    # Regression: the synchronous llm_call path (utility/task/vision models)
    # must materialize a local:// sentinel before issuing any HTTP request.
    import src.llm_core as llm

    captured = {}

    def fake_mat(url, model):
        captured["args"] = (url, model)
        raise RuntimeError("MATERIALIZE_CALLED")

    monkeypatch.setattr(llm, "materialize_local_url", fake_mat)
    with pytest.raises(RuntimeError, match="MATERIALIZE_CALLED"):
        llm.llm_call("local://llama.cpp/chat/completions", "ModelX",
                     [{"role": "user", "content": "hi"}])
    assert captured["args"] == ("local://llama.cpp/chat/completions", "ModelX")


def test_local_sentinel_materializes(monkeypatch):
    class _FakeServer:
        def ensure_running(self, ref):
            assert ref == "Qwen3.5-9B-Q4_K_M"
            return "http://127.0.0.1:9999"

    import services.localmodels.server_manager as sm
    monkeypatch.setattr(sm, "get_server", lambda: _FakeServer())
    url = materialize_local_url("local://llama.cpp/chat/completions",
                                "Qwen3.5-9B-Q4_K_M")
    assert url == "http://127.0.0.1:9999/v1/chat/completions"
