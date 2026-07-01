# Voicebox Integration — Design + Plan

**Goal:** Add [Voicebox](https://github.com/jamiepine/voicebox) as a selectable **TTS + STT provider** in Apollo, so voice call mode, read-aloud, and dictation can use its voice-cloning + 7 TTS engines. Apollo acts as a client of a locally-running Voicebox.

## Voicebox API (grounded)
Base (default): `http://127.0.0.1:17493` — localhost, no auth. Header on requests: `X-Voicebox-Client-Id: apollo`.
- **TTS:** `POST /generate` → body `{"text": "...", "profile_id": "<voice>", "language": "en"}` → returns audio bytes.
- **STT:** `POST /transcribe` → `multipart/form-data` with `audio` file + `model` → returns transcription (JSON `{text}` — parse tolerantly: `text`/`transcription`/segments).
- **Voices:** `GET /profiles` → list of voice profiles (for the voice picker).
- NOT OpenAI-compatible. It's a desktop app — must be running for Apollo to reach it.

## Design (mirror the existing provider pattern)
Apollo's `services/tts/tts_service.py` and `services/stt/stt_service.py` already dispatch by `tts_provider`/`stt_provider` (`disabled`/`browser`/`local`/`piper`/`endpoint:<id>`). Add a **`voicebox`** provider to both — no new infra, same shape as the existing `endpoint` branch.

### Settings (new)
- `voicebox_url` (default `http://127.0.0.1:17493`) — the running Voicebox base URL.
- `tts_provider = "voicebox"`, `tts_voice = "<profile_id>"`.
- `stt_provider = "voicebox"`.

### TTS (`services/tts/tts_service.py`)
- `available`: `if provider == "voicebox": return _voicebox_reachable(url)` (GET `/profiles` with a short timeout → 200).
- `synthesize`: `elif provider == "voicebox": audio = self._synthesize_voicebox(text, voice)` → POST `/generate` `{text, profile_id: voice or default, language}` with the client-id header → return `response.content` (audio bytes). Reuse the existing cache.
- Reuse `httpx` (project convention).

### STT (`services/stt/stt_service.py`)
- `available`: reachable check (same GET `/profiles`).
- `transcribe`: `elif provider == "voicebox": _transcribe_voicebox(audio_bytes)` → POST `/transcribe` multipart(`audio`, `model=stt_model or "base"`) with the header → parse `text`.

### Frontend (settings)
- Add **"Voicebox (local voice studio)"** to both the TTS and STT provider `<select>`s (`static/js/settings.js` / `static/index.html`).
- A **Voicebox URL** text field (bound to `voicebox_url`), shown when either provider is `voicebox`.
- For TTS voice: fetch `GET <voicebox_url>/profiles` and populate the voice picker (fallback to a free-text profile_id field).
- The `/api/tts/stats` + `/api/stt/stats` responses should report `provider: voicebox` + availability so the call-mode UI and toggles light up (they already key off these).

## Why this is the right shape
- Call mode (`voiceCall.js` → `/api/stt/transcribe` + `aiTTSManager`), read-aloud, and dictation ALL route through `stt_service`/`tts_service`. Adding the provider there lights up every voice surface at once — zero changes to the voice UI.
- Mirrors the existing `endpoint`/`piper` providers, so it's a small, low-risk addition.

## Tests
- Pure-ish adapter tests with `httpx` mocked (respx or monkeypatched client): `_synthesize_voicebox` posts the right body/headers to `/generate` and returns the audio bytes; `_transcribe_voicebox` posts multipart and parses `text`; `available` returns False when `/profiles` is unreachable. No live Voicebox needed.

## Out of scope (note only)
- Bundling/launching Voicebox as a sidecar (it's a separate Tauri app; user runs it).
- Registering Voicebox's HTTP MCP (`/mcp`) for agent voice tools — that needs no code; add it in Apollo's MCP settings. Mention in docs.
