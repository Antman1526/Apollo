# Voice Call Mode — Design Spec

**Date:** 2026-06-30
**Status:** Approved (design), pending implementation plan
**Scope:** Frontend-only feature. No backend changes.

## 1. Summary

A hands-free, real-time voice **call mode** for Apollo: the user taps a call
button, an overlay opens, and they hold a spoken back-and-forth with Apollo —
speak a turn, Apollo transcribes it locally, answers in the normal chat thread,
and speaks the reply aloud, then automatically listens for the next turn. The
user can interrupt Apollo mid-reply by talking (barge-in).

This is an **orchestrator over existing pieces**, not a new subsystem. Apollo
already ships local STT (`faster-whisper` via `/api/stt/transcribe`), streaming
TTS with auto-play (`aiTTSManager.autoPlay` + sentence streaming in
`static/js/tts-ai.js`), and the chat submit + SSE stream path. Call mode wires
them into a continuous loop with a dedicated UI.

## 2. Goals / non-goals

### Goals
- Continuous, hands-free spoken conversation (no tap per turn).
- Fully **on-device** speech recognition (local Whisper) — no audio leaves the
  machine. Preserves Apollo's local-first stance.
- Automatic end-of-turn detection (voice-activity detection / VAD).
- Barge-in: talking over Apollo stops its speech and starts a new turn.
- A focused call overlay that reads instantly as "you're on a call."
- Each spoken turn is a real message in the underlying chat thread (nothing is
  lost when the call ends).

### Non-goals (YAGNI — explicitly deferred)
- Wake-word activation ("Hey Apollo").
- A native `whisper.cpp` sidecar (the Python `faster-whisper` path is reused).
- Per-call language/voice pickers (call mode uses the existing STT/TTS
  settings).
- Multi-party / multiple simultaneous calls.
- Browser Web Speech API STT (rejected: streams mic audio to Google, breaks
  local-first; may be reconsidered later as an opt-in provider).

## 3. Decisions (locked)

| Decision | Choice | Rationale |
|---|---|---|
| STT path | Local `faster-whisper` via `/api/stt/transcribe` | On-device, matches local-first ethos |
| End-of-turn | Automatic energy-based VAD + manual stop fallback | Hands-free; zero-dependency; fits no-build vanilla-JS frontend |
| Barge-in | ON, with `getUserMedia` `echoCancellation:true` | Makes it feel like a real call; echo-cancel handles speaker feedback |
| UI | Dedicated call overlay with a state-animated orb | The "wow" surface; self-contained, doesn't disturb chat UI |

## 4. Architecture

Call mode is entirely client-side. No new routes, services, or DB changes.

### New components (all under `static/`)

1. **`js/vad.js` — voice-activity detector (pure logic + thin Web Audio wrap)**
   - Uses Web Audio `AnalyserNode` on the mic `MediaStream` to compute per-frame
     RMS energy.
   - Emits `speechstart` when energy crosses a threshold and `speechend` when
     energy stays below threshold for a debounce window (~1.2s, configurable).
   - The threshold/debounce math is a **pure function** over a frame buffer so it
     can be unit-tested without a microphone.
   - Exposes: `createVAD({ onSpeechStart, onSpeechEnd, threshold, silenceMs })`,
     `.attach(stream)`, `.pause()`, `.resume()`, `.destroy()`.

2. **`js/voiceCall.js` — call state machine + orchestrator**
   - States: `idle → listening → capturing → transcribing → thinking →
     speaking → listening`, plus terminal `idle` on end-call.
   - Owns one `getUserMedia({ audio: { echoCancellation: true,
     noiseSuppression: true } })` stream for the whole call.
   - Drives the VAD; on `speechstart` marks capturing (orb pulses); on
     `speechend` stops the per-utterance `MediaRecorder` and moves to
     transcribing.
   - Posts the utterance blob to `/api/stt/transcribe` (reusing the
     `transcribeOnServer` pattern from `voiceRecorder.js`).
   - Injects the transcript as a chat message through the **existing submit
     path** (same entry point `app.js` uses for typed messages) so the turn is a
     normal message with normal streaming.
   - Watches the chat stream for completion; hands the assistant text to
     `aiTTSManager` with `autoPlay` on (sentence-streaming TTS already exists).
   - Barge-in: VAD stays live during `speaking`; a `speechstart` calls
     `aiTTSManager.stop()` and transitions back to `listening`/`capturing`.
   - Teardown: stops the mic stream, destroys the VAD, cancels in-flight TTS.

3. **Call overlay UI (`index.html` + styles)**
   - Central orb whose class reflects state (listening = pulse to mic level,
     thinking = spinner, speaking = waveform), a live transcript line, a Mute
     button, and an End-call button.
   - Driven declaratively by `voiceCall.js` state changes (set a `data-state`
     attribute; CSS handles the visuals).

4. **Entry point**
   - A phone/call button near the chat input, shown only when STT is enabled
     (same gate as the existing send/mic button: `_isSttEnabled()` in `app.js`).
   - Click → open overlay, start the call loop.

### Reused (not rebuilt)
- `voiceRecorder.js`: `getUserMedia` handling, secure-context (HTTPS) guard,
  `transcribeOnServer` POST to `/api/stt/transcribe`.
- `tts-ai.js` / `aiTTSManager`: `autoPlay`, `feedStream`, sentence-by-sentence
  playback, `stop()`.
- `app.js` chat submit path and the SSE consumer in `chat.js`.

## 5. Data flow (one turn)

```
mic stream ──▶ VAD.speechstart ──▶ (orb: listening→capturing, MediaRecorder.start)
           ──▶ VAD.speechend   ──▶ MediaRecorder.stop → utterance blob
                                 ──▶ POST /api/stt/transcribe (orb: thinking)
                                 ──▶ inject transcript as user message + submit
                                 ──▶ SSE streams assistant reply
                                 ──▶ aiTTSManager speaks sentences (orb: speaking)
   barge-in: VAD.speechstart during speaking ──▶ aiTTSManager.stop() ──▶ listening
                                 ──▶ TTS end ──▶ back to listening (next turn)
```

## 6. Error handling & guards

- **Secure context:** require `window.isSecureContext` (HTTPS or localhost);
  otherwise show the existing "Microphone requires HTTPS" message and do not
  enter call mode. (Reuse `voiceRecorder.js` check.)
- **Mic denied / no device:** toast the reason (`NotAllowedError` /
  `NotFoundError`) and exit call mode cleanly.
- **STT provider disabled:** the call entry button is hidden (same gate as the
  send/mic button), so call mode is unreachable when STT is off.
- **STT failure / empty transcript:** stay in `listening`, brief "didn't catch
  that" indicator, no message sent.
- **TTS provider disabled:** call still works (input side); replies just aren't
  spoken — the transcript/reply still appear in chat. Optionally surface a hint.
- **Single call:** only one active call; entering guards against re-entry;
  End-call fully tears down mic + VAD + TTS.
- **Echo/feedback:** `echoCancellation` + `noiseSuppression` mic constraints;
  barge-in gated behind the VAD threshold so a cough/keyboard doesn't cut Apollo
  off. Headphones give the cleanest experience (documented).

## 7. Testing

- **`vad.js`** — real unit tests: feed synthetic RMS frame sequences and assert
  `speechstart` fires on threshold crossing and `speechend` fires after the
  silence debounce; assert threshold/debounce configurability. Pure logic, no
  mic/DOM.
- **`voiceCall.js` state machine** — extract transition logic so state changes
  (given events: speechstart, speechend, transcript, streamDone, bargeIn,
  endCall) are unit-testable without DOM/mic (inject the STT/TTS/submit
  collaborators).
- **Overlay + integration** — verified in-app via the `/verify` flow (mic +
  DOM + audio can't be meaningfully unit-tested). Keep DOM-touching code thin so
  the tested logic is separated from the untestable glue.

## 8. Implementation notes

- No build step: plain ES modules, matching the existing `static/js/` pattern
  (`import` in `app.js`, `<script type="module">` in `index.html`).
- Keep `voiceCall.js` focused on orchestration; put pure logic in `vad.js` and a
  small state-machine helper so files stay small and testable.
- Persist nothing new server-side; call mode is ephemeral UI state (an in-call
  "mute" flag, current state). The conversation persists through the normal chat
  message path.
