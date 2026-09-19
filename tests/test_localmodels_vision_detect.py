"""Image support for Apollo-managed local models comes from what is served,
not the model's name: "Dirk-Qwen3.8-27B" ships a projector but matches no
vision keyword, so its images were swapped for a caption it never saw."""
from unittest.mock import patch

from services.localmodels.scanner import LocalModel
from services.localmodels.server_manager import LocalModelServer
from src import chat_helpers


def _m(name, backend="llama.cpp", mmproj=None):
    return LocalModel(id="lm_" + name, name=name, path=f"/m/{name}", quant="", kind="chat",
                      size_bytes=1, directory="/m", arch="qwen35", mmproj=mmproj, backend=backend)


def test_llama_model_with_projector_accepts_images(tmp_path):
    proj = tmp_path / "mmproj-F16.gguf"
    proj.write_bytes(b"")
    srv = LocalModelServer(dirs_provider=lambda: [])
    srv.set_catalog([_m("Dirk-Qwen3.8-27B", mmproj=str(proj)), _m("Qwopus3.8-27B"),
                     _m("Gone-Proj", mmproj=str(tmp_path / "deleted.gguf")),
                     _m("Qwen3.6-27B-MLX-6bit", backend="mlx")])
    assert srv.supports_vision("Dirk-Qwen3.8-27B") is True
    assert srv.supports_vision("Qwopus3.8-27B") is False
    assert srv.supports_vision("Gone-Proj") is False
    assert srv.supports_vision("Qwen3.6-27B-MLX-6bit") is False  # mlx_lm is text-only
    assert srv.supports_vision("not-a-local-model") is None


def test_local_endpoint_uses_served_capability_over_the_name():
    class _Srv:
        def supports_vision(self, name):
            return {"Dirk-Qwen3.8-27B": True, "Qwen3-VL-looking-name-MLX": False}.get(name)

    with patch("services.localmodels.server_manager.get_server", return_value=_Srv()):
        assert chat_helpers.model_supports_vision("Dirk-Qwen3.8-27B", "local://llama.cpp") is True
        assert chat_helpers.model_supports_vision("Qwen3-VL-looking-name-MLX", "local://llama.cpp") is False
        # Unknown to the local server: fall back to the name heuristic.
        assert chat_helpers.model_supports_vision("llava-thing", "local://llama.cpp") is True


def test_remote_endpoints_keep_name_detection():
    with patch("services.localmodels.server_manager.get_server") as gs:
        assert chat_helpers.model_supports_vision("gpt-4o", "https://api.openai.com/v1") is True
        gs.assert_not_called()
