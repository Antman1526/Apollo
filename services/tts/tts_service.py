# src/tts_service.py
"""Multi-provider TTS service — dispatches to local Kokoro, OpenAI-compatible API, or browser."""

import io
import os
import wave
import logging
import hashlib
import httpx
from pathlib import Path
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


class TTSService:
    """Multi-provider TTS service.

    Reads provider config from data/settings.json on each call.
    Providers:
      "disabled"        — no TTS
      "browser"         — client-side Web Speech API (no server synthesis)
      "local"           — Kokoro-82M on GPU
      "endpoint:<id>"   — OpenAI-compatible /audio/speech via ModelEndpoint
    """

    def __init__(self, cache_dir: str = "data/tts_cache"):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._kokoro = None  # lazy-init

    # ── Settings ──

    def _load_settings(self) -> dict:
        from src.settings import load_settings
        saved = load_settings()
        return {
            "tts_enabled": saved.get("tts_enabled", True),
            "tts_provider": saved.get("tts_provider", "disabled"),
            "tts_model": saved.get("tts_model", "tts-1"),
            "tts_voice": saved.get("tts_voice", "alloy"),
            "tts_speed": saved.get("tts_speed", "1"),
            "voicebox_url": saved.get("voicebox_url", "http://127.0.0.1:17493"),
        }

    @property
    def available(self) -> bool:
        settings = self._load_settings()
        if settings.get("tts_enabled") is False:
            return False
        provider = settings["tts_provider"]
        if provider == "disabled":
            return False
        if provider == "browser":
            return True  # handled client-side
        if provider == "local":
            kokoro = self._get_kokoro()
            return kokoro is not None and kokoro.available
        if provider == "piper":
            return self._get_piper().available
        if provider == "voicebox":
            return self._voicebox_reachable(settings.get("voicebox_url"))
        if provider.startswith("endpoint:"):
            return True  # assume reachable; errors surface at synthesis time
        return False

    # ── Cache ──

    def _cache_key(self, text: str, provider: str, model: str, voice: str, speed: float = 1.0) -> str:
        raw = f"{provider}|{model}|{voice}|{speed}|{text}"
        return hashlib.sha256(raw.encode()).hexdigest()

    def _get_cached(self, key: str) -> Optional[bytes]:
        for ext in (".mp3", ".wav"):
            path = self.cache_dir / f"{key}{ext}"
            if path.exists():
                return path.read_bytes()
        return None

    def _put_cache(self, key: str, data: bytes):
        ext = ".mp3" if (len(data) >= 3 and (data[:3] == b'ID3' or (data[0] == 0xff and (data[1] & 0xe0) == 0xe0))) else ".wav"
        (self.cache_dir / f"{key}{ext}").write_bytes(data)

    def clear_cache(self):
        count = 0
        for f in self.cache_dir.glob("*.*"):
            f.unlink()
            count += 1
        logger.info(f"Cleared {count} cached TTS files")

    # ── Kokoro (local) ──

    def _get_kokoro(self):
        if self._kokoro is None:
            self._kokoro = _KokoroPipeline()
        return self._kokoro

    # ── Piper (local, CPU / Mac-friendly) ──

    def _get_piper(self):
        if getattr(self, "_piper", None) is None:
            self._piper = _PiperPipeline()
        return self._piper

    # ── API endpoint ──

    def _synthesize_api(self, text: str, endpoint_id: str, model: str, voice: str, speed: float = 1.0) -> Optional[bytes]:
        from src.database import SessionLocal, ModelEndpoint

        db = SessionLocal()
        try:
            ep = db.query(ModelEndpoint).filter(ModelEndpoint.id == endpoint_id).first()
            if not ep:
                logger.error(f"TTS endpoint {endpoint_id} not found")
                return None
            base_url = ep.base_url.rstrip("/")
            api_key = ep.api_key
        finally:
            db.close()

        url = base_url + "/audio/speech"
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        payload = {
            "model": model,
            "input": text,
            "voice": voice,
            "response_format": "mp3",
            "speed": speed,
        }

        try:
            r = httpx.post(url, json=payload, headers=headers, timeout=60)
            r.raise_for_status()
            logger.info(f"API TTS: {len(r.content)} bytes from {base_url}")
            return r.content
        except Exception as e:
            logger.error(f"API TTS synthesis failed: {e}")
            return None

    # ── Voicebox (local voice studio) ──

    _VOICEBOX_HEADERS = {"X-Voicebox-Client-Id": "apollo"}

    @staticmethod
    def _voicebox_base(url: Optional[str]) -> str:
        return (url or "http://127.0.0.1:17493").rstrip("/")

    def _voicebox_reachable(self, url: Optional[str]) -> bool:
        base = self._voicebox_base(url)
        try:
            r = httpx.get(base + "/profiles", headers=self._VOICEBOX_HEADERS, timeout=2.0)
            return r.status_code == 200
        except Exception as e:
            logger.debug(f"Voicebox unreachable at {base}: {e}")
            return False

    def _voicebox_profiles(self, url: Optional[str]) -> list:
        """Best-effort list of Voicebox voice profiles (for default selection)."""
        base = self._voicebox_base(url)
        try:
            r = httpx.get(base + "/profiles", headers=self._VOICEBOX_HEADERS, timeout=5.0)
            r.raise_for_status()
            data = r.json()
            if isinstance(data, dict):
                # tolerate {"profiles": [...]} or a bare mapping
                return data.get("profiles") or data.get("data") or []
            return data if isinstance(data, list) else []
        except Exception as e:
            logger.warning(f"Voicebox profiles fetch failed: {e}")
            return []

    @staticmethod
    def _voicebox_profile_id(profile) -> Optional[str]:
        if isinstance(profile, str):
            return profile
        if isinstance(profile, dict):
            for k in ("id", "profile_id", "name", "slug"):
                v = profile.get(k)
                if v:
                    return str(v)
        return None

    def _synthesize_voicebox(self, text: str, voice: str, url: Optional[str] = None) -> Optional[bytes]:
        base = self._voicebox_base(url)
        profile_id = voice
        if not profile_id:
            # Fall back to the first available profile.
            profiles = self._voicebox_profiles(url)
            if profiles:
                profile_id = self._voicebox_profile_id(profiles[0])
        payload = {"text": text, "profile_id": profile_id, "language": "en"}
        try:
            r = httpx.post(
                base + "/generate",
                json=payload,
                headers=self._VOICEBOX_HEADERS,
                timeout=120,
            )
            r.raise_for_status()
            logger.info(f"Voicebox TTS: {len(r.content)} bytes (profile={profile_id})")
            return r.content
        except Exception as e:
            logger.error(f"Voicebox TTS synthesis failed: {e}")
            return None

    # ── Public interface ──

    def synthesize(self, text: str, use_cache: bool = True) -> Optional[bytes]:
        settings = self._load_settings()
        if settings.get("tts_enabled") is False:
            return None
        provider = settings["tts_provider"]
        model = settings["tts_model"]
        voice = settings["tts_voice"]
        speed = float(settings.get("tts_speed", "1"))

        if provider in ("disabled", "browser"):
            return None

        if len(text) > 5000:
            text = text[:5000]

        if use_cache:
            key = self._cache_key(text, provider, model, voice, speed)
            cached = self._get_cached(key)
            if cached:
                logger.info(f"TTS cache hit ({len(text)} chars)")
                return cached

        audio_data = None

        if provider == "local":
            kokoro = self._get_kokoro()
            if kokoro and kokoro.available:
                audio_data = kokoro.synthesize_raw(text, voice)
            else:
                logger.warning("Kokoro TTS not available")
                return None
        elif provider == "piper":
            # `voice` holds the path to a Piper `.onnx` voice (with its
            # `.onnx.json` beside it). CPU-only, works on Apple Silicon.
            audio_data = self._get_piper().synthesize_raw(text, voice)
            if audio_data is None:
                logger.warning("Piper TTS not available or voice failed to load")
                return None
        elif provider == "voicebox":
            audio_data = self._synthesize_voicebox(text, voice, settings.get("voicebox_url"))
        elif provider.startswith("endpoint:"):
            endpoint_id = provider.split(":", 1)[1]
            audio_data = self._synthesize_api(text, endpoint_id, model, voice, speed)
        else:
            logger.error(f"Unknown TTS provider: {provider}")
            return None

        if audio_data and use_cache:
            key = self._cache_key(text, provider, model, voice, speed)
            self._put_cache(key, audio_data)

        return audio_data

    def synthesize_to_base64(self, text: str) -> Optional[str]:
        import base64
        audio = self.synthesize(text)
        if audio:
            return base64.b64encode(audio).decode("utf-8")
        return None

    def set_voice(self, voice: str):
        """Legacy no-op — voice is now managed via admin settings."""

    def get_stats(self) -> Dict[str, Any]:
        settings = self._load_settings()
        provider = settings["tts_provider"]
        tts_enabled = settings.get("tts_enabled", True)

        cache_files = list(self.cache_dir.glob("*.wav")) + list(self.cache_dir.glob("*.mp3"))
        cache_size = sum(f.stat().st_size for f in cache_files)

        is_available = self.available and tts_enabled
        stats = {
            "available": is_available,
            "ready": is_available,
            "provider": provider,
            "model": settings["tts_model"],
            "voice": settings["tts_voice"],
            "speed": float(settings.get("tts_speed", "1")),
            "cache_entries": len(cache_files),
            "cache_size_mb": round(cache_size / (1024 * 1024), 2),
        }

        if provider == "local":
            kokoro = self._get_kokoro()
            stats["model"] = "Kokoro-82M (GPU)" if (kokoro and kokoro.available) else "Kokoro (not loaded)"
        elif provider == "piper":
            import os as _os
            stats["model"] = f"Piper ({_os.path.basename(settings['tts_voice'] or 'no voice set')})"
        elif provider == "browser":
            stats["model"] = "Browser (Web Speech API)"
        elif provider == "voicebox":
            stats["model"] = f"Voicebox ({settings['tts_voice'] or 'default profile'})"
        elif provider.startswith("endpoint:"):
            stats["endpoint_id"] = provider.split(":", 1)[1]

        return stats


class _KokoroPipeline:
    """Encapsulates the Kokoro-82M local GPU pipeline."""

    def __init__(self):
        self.pipeline = None
        self.available = False
        self.device = None
        self._init()

    def _init(self):
        try:
            import torch
            from kokoro import KPipeline

            if not torch.cuda.is_available():
                logger.warning("CUDA not available for Kokoro TTS")
                return

            self.device = torch.device("cuda:0")
            with torch.cuda.device(0):
                self.pipeline = KPipeline(lang_code="a")
                if hasattr(self.pipeline, "model"):
                    self.pipeline.model = self.pipeline.model.to(self.device)
            self.available = True
            logger.info("Kokoro-82M TTS pipeline loaded")
        except ImportError as e:
            logger.warning(f"Kokoro TTS not available: {e}")
            logger.warning("Install with: pip install kokoro soundfile")
        except Exception as e:
            logger.error(f"Kokoro init failed: {e}", exc_info=True)

    def synthesize_raw(self, text: str, voice: str = "af_heart") -> Optional[bytes]:
        if not self.available:
            return None
        try:
            import torch
            import numpy as np

            with torch.cuda.device(self.device):
                chunks = []
                for _, _, audio in self.pipeline(text, voice=voice):
                    chunks.append(audio)

            if not chunks:
                return None

            full = np.concatenate(chunks)
            buf = io.BytesIO()
            with wave.open(buf, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(24000)
                wf.writeframes((full * 32767).astype(np.int16).tobytes())
            return buf.getvalue()
        except Exception as e:
            logger.error(f"Kokoro synthesis failed: {e}", exc_info=True)
            return None


class _PiperPipeline:
    """Loads Piper ONNX voices on demand and synthesizes WAV bytes.

    CPU-only (no CUDA/torch), so this is the local-TTS path on Apple Silicon.
    Voices are cached by their `.onnx` path; the matching `.onnx.json` must sit
    beside the model file.
    """

    def __init__(self):
        self._voices = {}  # path -> PiperVoice
        self._import_error = None
        try:
            from piper import PiperVoice  # noqa: F401
        except Exception as e:  # ImportError or a broken install
            self._import_error = e
            logger.warning("Piper TTS not available: %s (pip install piper-tts)", e)

    @property
    def available(self) -> bool:
        return self._import_error is None

    def _load(self, voice_path: str):
        if not self.available:
            return None
        if not voice_path or not os.path.isfile(voice_path):
            logger.warning("Piper voice not found: %r", voice_path)
            return None
        cached = self._voices.get(voice_path)
        if cached is not None:
            return cached
        try:
            from piper import PiperVoice
            voice = PiperVoice.load(voice_path)
            self._voices[voice_path] = voice
            logger.info("Piper voice loaded: %s", os.path.basename(voice_path))
            return voice
        except Exception as e:
            logger.error("Failed to load Piper voice %s: %s", voice_path, e, exc_info=True)
            return None

    def synthesize_raw(self, text: str, voice_path: str) -> Optional[bytes]:
        voice = self._load(voice_path)
        if voice is None:
            return None
        try:
            buf = io.BytesIO()
            with wave.open(buf, "wb") as wf:
                voice.synthesize_wav(text, wf)  # sets WAV format itself
            return buf.getvalue()
        except Exception as e:
            logger.error("Piper synthesis failed: %s", e, exc_info=True)
            return None


# Module-level singleton
_tts_service = None

def get_tts_service() -> TTSService:
    global _tts_service
    if _tts_service is None:
        _tts_service = TTSService()
    return _tts_service
