from services.localmodels.scanner import LocalModel
from services.localmodels.server_manager import LocalModelServer, _Proc


def _model(mid, name, kind="chat"):
    return LocalModel(id=mid, name=name, path=f"/m/{name}.gguf",
                      quant="Q4_K_M", kind=kind, size_bytes=1, directory="/m")


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


def test_unknown_model_raises():
    srv = _server_with([_model("a", "ModelA")], [])
    srv._dirs_provider = lambda: []  # refresh finds nothing
    try:
        srv.ensure_running("Nope")
        assert False, "expected LookupError"
    except LookupError:
        pass
