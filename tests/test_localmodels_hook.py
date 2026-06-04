from src.llm_core import materialize_local_url


def test_passthrough_for_normal_url():
    assert materialize_local_url("https://api.openai.com/v1/chat/completions",
                                 "gpt-4o") == \
        "https://api.openai.com/v1/chat/completions"


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
