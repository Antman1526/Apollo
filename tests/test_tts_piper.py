"""Tests for the Piper (local, CPU) TTS provider — dispatch + graceful fallback.

No real audio synthesis: the pipeline is stubbed, so these run anywhere.
"""


def _service():
    from services.tts.tts_service import TTSService
    return TTSService(cache_dir="/tmp/apollo-test-ttscache")


def test_piper_provider_routes_to_pipeline(monkeypatch):
    svc = _service()
    monkeypatch.setattr(svc, "_load_settings", lambda: {
        "tts_enabled": True, "tts_provider": "piper",
        "tts_model": "piper", "tts_voice": "/voices/amy.onnx", "tts_speed": "1",
    })

    captured = {}

    class _FakePiper:
        available = True

        def synthesize_raw(self, text, voice_path):
            captured["args"] = (text, voice_path)
            return b"RIFFfakewav"

    monkeypatch.setattr(svc, "_get_piper", lambda: _FakePiper())
    out = svc.synthesize("hello", use_cache=False)
    assert out == b"RIFFfakewav"
    assert captured["args"] == ("hello", "/voices/amy.onnx")


def test_piper_unavailable_returns_none(monkeypatch):
    svc = _service()
    monkeypatch.setattr(svc, "_load_settings", lambda: {
        "tts_enabled": True, "tts_provider": "piper",
        "tts_model": "piper", "tts_voice": "/voices/amy.onnx", "tts_speed": "1",
    })

    class _Unavailable:
        available = False

        def synthesize_raw(self, text, voice_path):
            return None  # missing deps / voice

    monkeypatch.setattr(svc, "_get_piper", lambda: _Unavailable())
    assert svc.synthesize("hello", use_cache=False) is None


def test_pipeline_missing_voice_is_graceful():
    from services.tts.tts_service import _PiperPipeline
    p = _PiperPipeline()
    # Even when piper-tts is installed, a non-existent voice path must not raise.
    assert p.synthesize_raw("hi", "/no/such/voice.onnx") is None
