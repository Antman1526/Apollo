"""Fork runtimes per architecture, launch-error surfacing, and tool-call caps."""
import io
import json
import os
from unittest.mock import patch

from routes.localmodels_routes import _launch_error
from services.localmodels import config, server_manager
from services.localmodels.scanner import LocalModel
from services.localmodels.server_manager import LocalModelServer, _Proc


def _model(name, arch="llama", path=None):
    return LocalModel(id="lm_" + name, name=name, path=path or f"/m/{name}.gguf",
                      quant="Q6_K", kind="chat", size_bytes=1, directory="/m",
                      arch=arch)


# -- per-architecture runtime -------------------------------------------------

def test_arch_path_reads_k2_setting():
    with patch.object(config, "load_settings",
                      return_value={"llama_server_k2_path": " /rt/k2/llama-server "}):
        assert config.get_arch_llama_server_path("k2-horizon") == "/rt/k2/llama-server"
        assert config.get_arch_llama_server_path("K2-Horizon") == "/rt/k2/llama-server"
        assert config.get_arch_llama_server_path("llama") == ""


def test_arch_path_empty_when_unset():
    with patch.object(config, "load_settings", return_value={}):
        assert config.get_arch_llama_server_path("k2-horizon") == ""


def test_find_binary_uses_fork_for_its_arch(tmp_path):
    fork = tmp_path / "llama-server-k2"
    fork.write_text("")
    srv = LocalModelServer(dirs_provider=lambda: [])
    with patch.object(server_manager, "get_arch_llama_server_path",
                      side_effect=lambda a: str(fork) if a == "k2-horizon" else ""), \
         patch.object(server_manager, "get_llama_server_path", return_value="/stock/llama-server"), \
         patch("os.path.isfile", side_effect=lambda p: p in (str(fork), "/stock/llama-server")):
        assert srv.find_binary("k2-horizon") == str(fork)
        assert srv.find_binary("llama") == "/stock/llama-server"
        assert srv.find_binary() == "/stock/llama-server"


def test_find_binary_falls_back_when_fork_missing():
    srv = LocalModelServer(dirs_provider=lambda: [])
    with patch.object(server_manager, "get_arch_llama_server_path",
                      return_value="/gone/llama-server"), \
         patch.object(server_manager, "get_llama_server_path", return_value="/stock/llama-server"), \
         patch("os.path.isfile", side_effect=lambda p: p == "/stock/llama-server"):
        assert srv.find_binary("k2-horizon") == "/stock/llama-server"


# -- launch error surfacing ---------------------------------------------------

def test_launch_error_names_the_llama_cpp_cause():
    err = RuntimeError(
        "llama-server exited early (code 1); full log: /tmp/apollo-llama-1.log\n"
        "llama_model_load: error loading model: unknown model architecture: 'k2-horizon'\n"
        "srv load_model: failed to load model\n"
    )
    out = _launch_error(err)
    assert out["error"] == ("llama_model_load: error loading model: "
                            "unknown model architecture: 'k2-horizon'")
    assert "/tmp/apollo-llama-1.log" in out["detail"]


def test_launch_error_keeps_short_messages_whole():
    out = _launch_error(LookupError("Unknown local model: 'Nope'"))
    assert out["error"] == "Unknown local model: 'Nope'"


def test_launch_error_generic_when_empty():
    assert _launch_error(RuntimeError("")) == {"error": "Model could not be started"}


# -- tool-call capabilities from /props --------------------------------------

def _props(caps):
    return io.BytesIO(json.dumps({"chat_template_caps": caps}).encode())


def test_probe_reads_supports_tool_calls():
    with patch("urllib.request.urlopen", return_value=_props({"supports_tool_calls": False})):
        assert server_manager._probe_tool_calls("http://x") is False
    with patch("urllib.request.urlopen", return_value=_props({"supports_tool_calls": True})):
        assert server_manager._probe_tool_calls("http://x") is True


def test_probe_unknown_on_old_server_or_error():
    with patch("urllib.request.urlopen", return_value=_props({})):
        assert server_manager._probe_tool_calls("http://x") is None
    with patch("urllib.request.urlopen", side_effect=OSError("refused")):
        assert server_manager._probe_tool_calls("http://x") is None


def test_ensure_running_records_tool_caps():
    class _Alive:
        def poll(self):
            return None

    m = _model("Hermes-3")
    srv = LocalModelServer(dirs_provider=lambda: [])
    srv.set_catalog([m])
    srv._launch = lambda mm: _Proc(mm.id, mm.name, mm.kind, 9001, _Alive(),  # type: ignore[assignment]
                                   "http://127.0.0.1:9001")
    assert srv.supports_tool_calls("Hermes-3") is None
    with patch.object(server_manager, "_probe_tool_calls", return_value=False):
        srv.ensure_running("Hermes-3")
    assert srv.supports_tool_calls("Hermes-3") is False
    assert srv.supports_tool_calls("never-seen") is None


# -- agent loop-breaker -------------------------------------------------------

def test_exact_repeat_of_previous_call_is_stuck_even_with_text():
    from collections import deque
    from src.agent_loop import _round_is_stuck
    sig = "python:print(48271 * 9973)"
    assert _round_is_stuck(sig, deque([sig]), "The result is 481406683.",
                           prompt_mode=True) is True


def test_progress_is_not_stuck():
    from collections import deque
    from src.agent_loop import _round_is_stuck
    a, b = "bash:ls", "bash:cat x"
    assert _round_is_stuck(a, deque(), "") is False             # first call
    assert _round_is_stuck(b, deque([a]), "") is False          # new distinct call
    assert _round_is_stuck(a, deque([a, b]), "found it") is False  # older repeat + text
    assert _round_is_stuck(a, deque([a, b]), "") is True        # older repeat, silent


def test_repeat_with_text_only_stuck_in_prompt_mode():
    from collections import deque
    from src.agent_loop import _round_is_stuck
    sig = "check_status:{}"
    # Native tool calls: a narrated poll of the same call is not circling.
    assert _round_is_stuck(sig, deque([sig]), "still building...") is False


def test_failed_launch_is_replayed_not_retried():
    m = _model("Broken")
    srv = LocalModelServer(dirs_provider=lambda: [])
    srv.set_catalog([m])
    calls = []

    def boom(mm):
        calls.append(mm.id)
        raise RuntimeError("llama-server did not become healthy in time")

    srv._launch = boom  # type: ignore[assignment]
    for _ in range(2):
        try:
            srv.ensure_running("Broken")
        except RuntimeError:
            pass
    assert calls == ["lm_Broken"]


def test_answer_from_reasoning_takes_last_paragraph():
    from src.agent_loop import _answer_from_reasoning
    reasoning = ("\n\n48271 * 9973 = 482710000 - 1303317\n= 481,406,683 ✓\n\n"
                 "The exact result is **481,406,683**.\n")
    assert _answer_from_reasoning(reasoning) == "The exact result is **481,406,683**."
    assert _answer_from_reasoning("") == ""
    assert _answer_from_reasoning("  \n\n ") == ""


def test_tool_markup_in_reasoning_is_not_an_answer():
    from src.agent_loop import _TOOL_MARKUP_RE
    assert _TOOL_MARKUP_RE.search("<tool_call>\n<function=python>\n</function>\n</tool_call>")
    assert _TOOL_MARKUP_RE.search("<|tool_call_start|>[python(code='1')]<|tool_call_end|>")
    assert not _TOOL_MARKUP_RE.search("The exact result is **481,406,683**.")


# -- directory status ---------------------------------------------------------

def test_dir_status_distinguishes_unmounted_from_missing(tmp_path):
    from routes.localmodels_routes import _dir_status
    m = _model("A"); m.directory = os.path.realpath(str(tmp_path))
    assert _dir_status(str(tmp_path), [m]) == {"path": str(tmp_path), "state": "ok", "models": 1}
    assert _dir_status("/Volumes/NoSuchDrive/AI_Models", []) == {
        "path": "/Volumes/NoSuchDrive/AI_Models", "state": "unmounted", "models": 0}
    assert _dir_status(str(tmp_path / "gone"), [])["state"] == "missing"


def test_token_limit_note_names_the_budget():
    from src.agent_loop import _token_limit_note
    assert "2048-token" in _token_limit_note(2048, True)
    assert "still thinking" in _token_limit_note(2048, True)
    assert "before writing" in _token_limit_note(512, False)


# -- native tool round that returned nothing ---------------------------------

def test_native_tools_returned_nothing_detection():
    from src.agent_loop import _native_tools_returned_nothing as f
    assert f("", [], "tool_calls") is True
    assert f("<think>hm</think>", [], "tool_calls") is True
    assert f("", [{"name": "python"}], "tool_calls") is False
    assert f("here you go", [], "tool_calls") is False
    assert f("", [], "stop") is False
    assert f("", [], "length") is False


def test_set_tool_caps_overrides_launch_verdict():
    m = _model("Coder-Next")
    srv = LocalModelServer(dirs_provider=lambda: [])
    srv.set_catalog([m])
    srv._tool_caps[m.path] = True
    srv.set_tool_caps("Coder-Next", False)
    assert srv.supports_tool_calls("Coder-Next") is False
    srv.set_tool_caps("unknown-model", False)  # no-op, no error


def test_runtime_tool_verdict_survives_a_relaunch():
    class _Alive:
        def poll(self):
            return None

    m = _model("Coder-Next")
    m.backend, m.tools = "mlx", True
    srv = LocalModelServer(dirs_provider=lambda: [])
    srv.set_catalog([m])
    srv._launch = lambda mm: _Proc(mm.id, mm.name, mm.kind, 9001, _Alive(),  # type: ignore[assignment]
                                   "http://127.0.0.1:9001")
    srv.ensure_running("Coder-Next")
    assert srv.supports_tool_calls("Coder-Next") is True   # launch-time guess
    srv.set_tool_caps("Coder-Next", False)                  # learned in a round
    srv.stop_all()
    srv.ensure_running("Coder-Next")
    assert srv.supports_tool_calls("Coder-Next") is False  # not reset by the relaunch
