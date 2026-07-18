from services.localmodels.scanner import LocalModel
from services.localmodels.server_manager import LocalModelServer, _Proc


def _model(mid, name, kind="chat", arch=""):
    return LocalModel(id=mid, name=name, path=f"/m/{name}.gguf",
                      quant="Q4_K_M", kind=kind, size_bytes=1, directory="/m",
                      arch=arch)


class _FakeProcess:
    def __init__(self):
        self._alive = True
        self.terminated = False

    def poll(self):
        return None if self._alive else 0

    def terminate(self):
        self.terminated = True
        self._alive = False

    def wait(self, timeout=None):
        self._alive = False


def _server_with(models, launched):
    srv = LocalModelServer(dirs_provider=lambda: [])
    srv._catalog = {m.id: m for m in models}

    def fake_launch(m):
        p = _FakeProcess()
        proc = _Proc(model_id=m.id, name=m.name, kind=m.kind,
                     port=9000 + len(launched), proc=p,
                     base_url=f"http://127.0.0.1:{9000 + len(launched)}")
        launched.append(proc)
        return proc

    srv._launch = fake_launch  # type: ignore[assignment]
    return srv


def test_ensure_running_starts_and_reuses_when_warm():
    launched = []
    srv = _server_with([_model("a", "ModelA")], launched)
    url1 = srv.ensure_running("ModelA")
    url2 = srv.ensure_running("ModelA")  # already warm → no new launch
    assert url1 == url2
    assert len(launched) == 1


def test_new_chat_model_evicts_previous():
    launched = []
    srv = _server_with([_model("a", "ModelA"), _model("b", "ModelB")], launched)
    srv.ensure_running("ModelA")
    srv.ensure_running("ModelB")
    assert launched[0].proc.terminated is True   # A was stopped
    assert launched[1].proc.terminated is False  # B is warm


def test_embedding_does_not_evict_chat():
    launched = []
    srv = _server_with(
        [_model("a", "ModelA", "chat"), _model("e", "Embed", "embedding")], launched
    )
    srv.ensure_running("ModelA")
    srv.ensure_running("Embed")
    assert launched[0].proc.terminated is False  # chat stays warm
    assert len(launched) == 2


def test_stop_falls_back_to_kill_when_terminate_fails(caplog):
    class FailingProcess:
        killed = False

        def terminate(self):
            raise OSError("terminate failed")

        def wait(self, timeout=None):
            raise AssertionError("wait should not run after terminate failure")

        def kill(self):
            self.killed = True

        def poll(self):
            return None

    srv = LocalModelServer(dirs_provider=lambda: [])
    process = FailingProcess()
    slot = _Proc("a", "ModelA", "chat", 9000, process, "http://127.0.0.1:9000")
    srv._chat = slot

    srv._stop_proc(slot)

    assert process.killed is True
    assert srv._chat is None
    assert "local_model_terminate_failed" in caplog.text


def test_unknown_model_raises():
    srv = _server_with([_model("a", "ModelA")], [])
    srv._dirs_provider = lambda: []  # refresh finds nothing
    try:
        srv.ensure_running("Nope")
        assert False, "expected LookupError"
    except LookupError:
        pass


def test_unsupported_model_raises_value_error():
    """ensure_running must raise ValueError (not launch) for unsupported architectures."""
    launched = []
    srv = _server_with(
        [_model("d", "google.diffusiongemma-2b-Q4_K_M",
                kind="unsupported", arch="diffusion-gemma")],
        launched,
    )
    try:
        srv.ensure_running("google.diffusiongemma-2b-Q4_K_M")
        assert False, "expected ValueError"
    except ValueError as e:
        assert "diffusion-gemma" in str(e)
        assert "llama-server" in str(e)
    assert len(launched) == 0  # nothing was launched


def test_serving_context_uses_known_window_capped(monkeypatch):
    srv = LocalModelServer(dirs_provider=lambda: [])
    # Llama 3.2 has a huge known window — capped to the env limit (default 16384).
    big = _model("m1", "Llama-3.2-3B-Instruct-Q4_K_M")
    assert srv._serving_context(big) == 16384
    # Unknown models fall back to the cap, never below the configured floor.
    unknown = _model("m2", "Totally-Unknown-Model-Q4_K_M")
    assert srv._serving_context(unknown) >= 4096
    # The cap is tunable.
    monkeypatch.setenv("APOLLO_LLAMA_CONTEXT", "8192")
    assert srv._serving_context(big) == 8192
    # A bogus env value falls back without crashing.
    monkeypatch.setenv("APOLLO_LLAMA_CONTEXT", "not-a-number")
    assert srv._serving_context(big) == 16384
