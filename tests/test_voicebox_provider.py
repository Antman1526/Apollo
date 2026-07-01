"""Tests for the Voicebox (local voice studio) TTS + STT provider.

No live Voicebox: the httpx calls are monkeypatched. These assert the adapter
posts the right JSON/multipart + client-id header and parses the reply
tolerantly.
"""

import io

import pytest


# ── Fakes ──

class _FakeResp:
    def __init__(self, *, status_code=200, content=b"", json_data=None, text=""):
        self.status_code = status_code
        self.content = content
        self._json = json_data
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        if self._json is None:
            raise ValueError("no json")
        return self._json


def _tts():
    from services.tts.tts_service import TTSService
    return TTSService(cache_dir="/tmp/apollo-test-voicebox-ttscache")


def _stt():
    from services.stt.stt_service import STTService
    return STTService()


# ── TTS ──

def test_synthesize_voicebox_posts_generate(monkeypatch):
    svc = _tts()
    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None, **kw):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        return _FakeResp(content=b"AUDIOBYTES")

    import services.tts.tts_service as mod
    monkeypatch.setattr(mod.httpx, "post", fake_post)

    out = svc._synthesize_voicebox("hi there", "narrator", "http://127.0.0.1:17493")

    assert out == b"AUDIOBYTES"
    assert captured["url"] == "http://127.0.0.1:17493/generate"
    assert captured["json"] == {"text": "hi there", "profile_id": "narrator", "language": "en"}
    assert captured["headers"]["X-Voicebox-Client-Id"] == "apollo"


def test_synthesize_voicebox_falls_back_to_first_profile(monkeypatch):
    svc = _tts()
    import services.tts.tts_service as mod
    monkeypatch.setattr(svc, "_voicebox_profiles", lambda url: [{"id": "auto1"}, {"id": "auto2"}])

    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None, **kw):
        captured["json"] = json
        return _FakeResp(content=b"X")

    monkeypatch.setattr(mod.httpx, "post", fake_post)
    svc._synthesize_voicebox("hello", "", "http://127.0.0.1:17493")
    assert captured["json"]["profile_id"] == "auto1"


def test_synthesize_dispatch_routes_to_voicebox(monkeypatch):
    svc = _tts()
    monkeypatch.setattr(svc, "_load_settings", lambda: {
        "tts_enabled": True, "tts_provider": "voicebox",
        "tts_model": "tts-1", "tts_voice": "narrator", "tts_speed": "1",
        "voicebox_url": "http://box:1/",
    })
    seen = {}

    def fake_syn(text, voice, url=None):
        seen["args"] = (text, voice, url)
        return b"OK"

    monkeypatch.setattr(svc, "_synthesize_voicebox", fake_syn)
    out = svc.synthesize("read this", use_cache=False)
    assert out == b"OK"
    assert seen["args"] == ("read this", "narrator", "http://box:1/")


def test_tts_available_false_when_unreachable(monkeypatch):
    svc = _tts()
    monkeypatch.setattr(svc, "_load_settings", lambda: {
        "tts_enabled": True, "tts_provider": "voicebox",
        "tts_model": "tts-1", "tts_voice": "x", "tts_speed": "1",
        "voicebox_url": "http://127.0.0.1:17493",
    })
    import services.tts.tts_service as mod

    def boom(*a, **k):
        raise ConnectionError("refused")

    monkeypatch.setattr(mod.httpx, "get", boom)
    assert svc.available is False


def test_tts_available_true_when_profiles_ok(monkeypatch):
    svc = _tts()
    monkeypatch.setattr(svc, "_load_settings", lambda: {
        "tts_enabled": True, "tts_provider": "voicebox",
        "tts_model": "tts-1", "tts_voice": "x", "tts_speed": "1",
        "voicebox_url": "http://127.0.0.1:17493",
    })
    import services.tts.tts_service as mod
    monkeypatch.setattr(mod.httpx, "get", lambda *a, **k: _FakeResp(status_code=200, json_data=[]))
    assert svc.available is True


# ── STT ──

def test_transcribe_voicebox_posts_multipart_and_parses_text(monkeypatch):
    svc = _stt()
    captured = {}

    def fake_post(url, headers=None, files=None, data=None, timeout=None, **kw):
        captured["url"] = url
        captured["headers"] = headers
        captured["files"] = files
        captured["data"] = data
        return _FakeResp(json_data={"text": "hello world"})

    import services.stt.stt_service as mod
    monkeypatch.setattr(mod.httpx, "post", fake_post)

    out = svc._transcribe_voicebox(b"rawaudio", "base", "http://127.0.0.1:17493")
    assert out == "hello world"
    assert captured["url"] == "http://127.0.0.1:17493/transcribe"
    assert captured["headers"]["X-Voicebox-Client-Id"] == "apollo"
    assert "audio" in captured["files"]
    assert captured["data"] == {"model": "base"}


def test_transcribe_voicebox_transcription_fallback(monkeypatch):
    svc = _stt()
    import services.stt.stt_service as mod
    monkeypatch.setattr(mod.httpx, "post",
                        lambda *a, **k: _FakeResp(json_data={"transcription": "via transcription"}))
    assert svc._transcribe_voicebox(b"x", "base", "http://x") == "via transcription"


def test_transcribe_voicebox_segments_fallback(monkeypatch):
    svc = _stt()
    import services.stt.stt_service as mod
    resp = _FakeResp(json_data={"segments": [{"text": "one"}, {"text": "two"}]})
    monkeypatch.setattr(mod.httpx, "post", lambda *a, **k: resp)
    assert svc._transcribe_voicebox(b"x", "base", "http://x") == "one two"
    assert svc._parse_voicebox_text({"segments": [{"text": "one"}, {"text": "two"}]}) == "one two"


def test_transcribe_dispatch_routes_to_voicebox(monkeypatch):
    svc = _stt()
    monkeypatch.setattr(svc, "_load_settings", lambda: {
        "stt_enabled": True, "stt_provider": "voicebox",
        "stt_model": "small", "stt_language": "",
        "voicebox_url": "http://box:2/",
    })
    seen = {}

    def fake_tx(audio_bytes, model="base", url=None):
        seen["args"] = (audio_bytes, model, url)
        return "text out"

    monkeypatch.setattr(svc, "_transcribe_voicebox", fake_tx)
    out = svc.transcribe(b"audio")
    assert out == "text out"
    assert seen["args"] == (b"audio", "small", "http://box:2/")


def test_stt_available_false_when_unreachable(monkeypatch):
    svc = _stt()
    monkeypatch.setattr(svc, "_load_settings", lambda: {
        "stt_enabled": True, "stt_provider": "voicebox",
        "stt_model": "base", "stt_language": "",
        "voicebox_url": "http://127.0.0.1:17493",
    })
    import services.stt.stt_service as mod

    def boom(*a, **k):
        raise ConnectionError("refused")

    monkeypatch.setattr(mod.httpx, "get", boom)
    assert svc.available is False
