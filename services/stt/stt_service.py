# services/stt/stt_service.py
"""Multi-provider Speech-to-Text service — dispatches to local Whisper, OpenAI-compatible API, or browser."""

import io
import logging
import httpx
import tempfile
from pathlib import Path
from typing import Optional, Dict, Any

from src.observability import report_exception

logger = logging.getLogger(__name__)

# Known STT provider identifiers. Endpoint providers are expressed as
# "endpoint:<id>" and matched by the ENDPOINT_PREFIX below rather than by an
# exact name. Centralized here so `available`, `transcribe`, and `get_stats`
# share one source of truth instead of scattering the literals.
PROVIDER_DISABLED = "disabled"
PROVIDER_BROWSER = "browser"
PROVIDER_LOCAL = "local"
PROVIDER_VOICEBOX = "voicebox"
ENDPOINT_PREFIX = "endpoint:"

# Providers that produce no server-side transcript (short-circuit in transcribe()).
_NON_TRANSCRIBE_PROVIDERS = (PROVIDER_DISABLED, PROVIDER_BROWSER)


class STTService:
    """Multi-provider STT service.

    Reads provider config from data/settings.json on each call.
    Providers:
      "disabled"        — no STT
      "browser"         — client-side Web Speech API (no server transcription)
      "local"           — faster-whisper on CPU/GPU
      "endpoint:<id>"   — OpenAI-compatible /audio/transcriptions via ModelEndpoint
    """

    def __init__(self):
        self._whisper_model = None  # lazy-init

    # ── Settings ──

    def _load_settings(self) -> dict:
        from src.settings import load_settings
        saved = load_settings()
        return {
            "stt_enabled": saved.get("stt_enabled", False),
            "stt_provider": saved.get("stt_provider", "disabled"),
            "stt_model": saved.get("stt_model", "base"),
            "stt_language": saved.get("stt_language", ""),
            "voicebox_url": saved.get("voicebox_url", "http://127.0.0.1:17493"),
        }

    @property
    def available(self) -> bool:
        settings = self._load_settings()
        if settings.get("stt_enabled") is False:
            return False
        provider = settings["stt_provider"]
        if provider == PROVIDER_DISABLED:
            return False
        if provider == PROVIDER_BROWSER:
            return True  # handled client-side
        if provider == PROVIDER_LOCAL:
            return self._get_whisper() is not None
        if provider == PROVIDER_VOICEBOX:
            return self._voicebox_reachable(settings.get("voicebox_url"))
        if provider.startswith(ENDPOINT_PREFIX):
            return True  # assume reachable
        return False

    # ── Local Whisper ──

    def _get_whisper(self):
        if self._whisper_model is None:
            try:
                from faster_whisper import WhisperModel
            except ImportError:
                logger.warning("faster-whisper not installed. Install with: pip install faster-whisper")
                return None
            try:
                settings = self._load_settings()
                model_size = settings.get("stt_model", "base")
                # faster-whisper runs on CTranslate2, not torch. torch is only
                # used (optionally) to detect a CUDA device for acceleration —
                # if it's missing or unusable we just run on CPU. Keeping this
                # probe separate (and tolerant of any failure, e.g. a broken
                # CUDA/torch install that raises OSError on import) means a
                # torch-less or torch-broken machine still does CPU
                # transcription instead of failing with a misleading
                # "faster-whisper not installed" error.
                try:
                    import torch
                    use_cuda = torch.cuda.is_available()
                except Exception as error:
                    report_exception(
                        logger,
                        "stt_cuda_probe_failed",
                        error,
                        outcome="best_effort",
                    )
                    use_cuda = False
                device = "cuda" if use_cuda else "cpu"
                compute_type = "float16" if device == "cuda" else "int8"
                self._whisper_model = WhisperModel(model_size, device=device, compute_type=compute_type)
                logger.info(f"faster-whisper model '{model_size}' loaded on {device}")
            except Exception as e:
                logger.error(f"Failed to load whisper model: {e}")
                return None
        return self._whisper_model

    def _transcribe_local(self, audio_bytes: bytes, language: str = "") -> Optional[str]:
        model = self._get_whisper()
        if not model:
            return None
        tmp_path = None
        try:
            # Write to temp file (faster-whisper needs a file path or file-like)
            with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as tmp:
                tmp.write(audio_bytes)
                tmp_path = tmp.name

            kwargs = {}
            if language:
                kwargs["language"] = language

            segments, info = model.transcribe(tmp_path, **kwargs)
            text = " ".join(seg.text.strip() for seg in segments)

            logger.info(f"Local STT: {len(text)} chars, lang={info.language}, prob={info.language_probability:.2f}")
            return text
        except Exception as e:
            logger.error(f"Local STT transcription failed: {e}", exc_info=True)
            return None
        finally:
            if tmp_path:
                Path(tmp_path).unlink(missing_ok=True)

    # ── API endpoint ──

    def _transcribe_api(self, audio_bytes: bytes, endpoint_id: str, model: str, language: str = "") -> Optional[str]:
        from src.database import SessionLocal, ModelEndpoint

        db = SessionLocal()
        try:
            ep = db.query(ModelEndpoint).filter(ModelEndpoint.id == endpoint_id).first()
            if not ep:
                logger.error(f"STT endpoint {endpoint_id} not found")
                return None
            base_url = ep.base_url.rstrip("/")
            api_key = ep.api_key
        finally:
            db.close()

        url = base_url + "/audio/transcriptions"
        headers = {}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        files = {"file": ("audio.webm", io.BytesIO(audio_bytes), "audio/webm")}
        data = {"model": model or "whisper-1"}
        if language:
            data["language"] = language

        try:
            r = httpx.post(url, headers=headers, files=files, data=data, timeout=60)
            r.raise_for_status()
            result = r.json()
            text = result.get("text", "")
            logger.info(f"API STT: {len(text)} chars from {base_url}")
            return text
        except Exception as e:
            logger.error(f"API STT transcription failed: {e}")
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

    @staticmethod
    def _parse_voicebox_text(result) -> str:
        """Tolerantly pull the transcript out of a Voicebox /transcribe reply."""
        if isinstance(result, str):
            return result.strip()
        if isinstance(result, dict):
            text = result.get("text")
            if text:
                return str(text).strip()
            transcription = result.get("transcription")
            if transcription:
                return str(transcription).strip()
            segments = result.get("segments")
            if isinstance(segments, list):
                return " ".join(
                    str(seg.get("text", "")).strip()
                    for seg in segments
                    if isinstance(seg, dict)
                ).strip()
        return ""

    def _transcribe_voicebox(self, audio_bytes: bytes, model: str = "base", url: Optional[str] = None) -> Optional[str]:
        base = self._voicebox_base(url)
        files = {"audio": ("audio.webm", io.BytesIO(audio_bytes), "audio/webm")}
        data = {"model": model or "base"}
        try:
            r = httpx.post(
                base + "/transcribe",
                headers=self._VOICEBOX_HEADERS,
                files=files,
                data=data,
                timeout=120,
            )
            r.raise_for_status()
            try:
                result = r.json()
            except ValueError:
                result = r.text
            text = self._parse_voicebox_text(result)
            logger.info(f"Voicebox STT: {len(text)} chars from {base}")
            return text
        except Exception as e:
            logger.error(f"Voicebox STT transcription failed: {e}")
            return None

    # ── Public interface ──

    def transcribe(self, audio_bytes: bytes) -> Optional[str]:
        settings = self._load_settings()
        if settings.get("stt_enabled") is False:
            return None
        provider = settings["stt_provider"]
        model = settings["stt_model"]
        language = settings.get("stt_language", "")

        if provider in _NON_TRANSCRIBE_PROVIDERS:
            return None

        if provider == PROVIDER_LOCAL:
            return self._transcribe_local(audio_bytes, language)
        elif provider == PROVIDER_VOICEBOX:
            return self._transcribe_voicebox(audio_bytes, model, settings.get("voicebox_url"))
        elif provider.startswith(ENDPOINT_PREFIX):
            endpoint_id = provider.split(":", 1)[1]
            return self._transcribe_api(audio_bytes, endpoint_id, model, language)
        else:
            logger.error(f"Unknown STT provider: {provider}")
            return None

    def get_stats(self) -> Dict[str, Any]:
        settings = self._load_settings()
        provider = settings["stt_provider"]
        stt_enabled = settings.get("stt_enabled", False)
        # If toggle is off, report as disabled
        effective_provider = provider if stt_enabled else PROVIDER_DISABLED

        stats = {
            "available": self.available and stt_enabled,
            "provider": effective_provider,
            "model": settings["stt_model"],
            "language": settings.get("stt_language", ""),
        }

        if provider == PROVIDER_LOCAL:
            whisper = self._get_whisper()
            stats["model_loaded"] = whisper is not None
        elif provider == PROVIDER_BROWSER:
            stats["model"] = "Browser (Web Speech API)"
        elif provider.startswith(ENDPOINT_PREFIX):
            stats["endpoint_id"] = provider.split(":", 1)[1]

        return stats


# Module-level singleton
_stt_service = None

def get_stt_service() -> STTService:
    global _stt_service
    if _stt_service is None:
        _stt_service = STTService()
    return _stt_service
