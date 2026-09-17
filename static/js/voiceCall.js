// static/js/voiceCall.js
//
// Hands-free voice "call mode". Two layers:
//   createCallMachine() — pure state machine over injected effects (unit-tested).
//   startCall()/endCall() — browser wiring (mic, VAD, STT, submit, TTS, overlay).
// Only the wiring touches the DOM/mic, and only when called, so this module
// imports cleanly in Node for testing the machine.

// Voice-assign: a spoken "assign a task …" / "have the agent …" hands the
// rest of the utterance to a background agent task (POST /api/tasks/assign)
// instead of the chat, so you can delegate work mid-call and keep talking.
const _ASSIGN_RE = /^(?:hey[,\s]+)?(?:apollo[,!\s]+)?(?:assign\s+(?:a\s+)?task(?:\s+to)?(?:\s+the\s+agent)?|assign\s+to\s+(?:the\s+)?agent|background\s+task|have\s+the\s+agent)[:,]?\s+(.+)$/i;

export function parseVoiceAssign(text) {
  const m = _ASSIGN_RE.exec((text || '').trim());
  return m ? m[1].trim() : null;
}

export function createCallMachine(effects = {}) {
  const eff = {
    startCapture() {},
    stopCapture() {},
    submitMessage() {},
    speak() {},
    stopSpeak() {},
    teardown() {},
    onState() {},
    ...effects,
  };

  let state = 'idle';
  const set = (next) => {
    if (next !== state) {
      state = next;
      eff.onState(state);
    }
  };

  function dispatch(event, payload) {
    switch (state) {
      case 'idle':
        if (event === 'start') set('listening');
        break;
      case 'listening':
        if (event === 'speechStart') {
          eff.startCapture();
          set('capturing');
        } else if (event === 'end') {
          eff.teardown();
          set('idle');
        }
        break;
      case 'capturing':
        if (event === 'speechEnd') {
          eff.stopCapture();
          set('transcribing');
        } else if (event === 'end') {
          eff.stopCapture();
          eff.teardown();
          set('idle');
        }
        break;
      case 'transcribing':
        if (event === 'transcribed') {
          const text = ((payload && payload.text) || '').trim();
          if (text) {
            eff.submitMessage(text);
            set('thinking');
          } else {
            set('listening');
          }
        } else if (event === 'error') {
          set('listening');
        } else if (event === 'end') {
          eff.teardown();
          set('idle');
        }
        break;
      case 'thinking':
        if (event === 'assistantComplete') {
          const text = ((payload && payload.text) || '').trim();
          if (text) {
            // Enter `speaking` BEFORE invoking speak(): a speak() that fires
            // speakEnd synchronously (e.g. TTS disabled) must land in `speaking`,
            // or the machine would park in `speaking` forever.
            set('speaking');
            eff.speak(text);
          } else {
            set('listening');
          }
        } else if (event === 'error') {
          set('listening');
        } else if (event === 'end') {
          eff.teardown();
          set('idle');
        }
        break;
      case 'speaking':
        if (event === 'speakEnd') {
          set('listening');
        } else if (event === 'speechStart') {
          eff.stopSpeak();
          eff.startCapture();
          set('capturing');
        } else if (event === 'end') {
          eff.stopSpeak();
          eff.teardown();
          set('idle');
        }
        break;
    }
    return state;
  }

  return {
    dispatch,
    get state() {
      return state;
    },
  };
}

import { createVadGate, createMicVad } from './vad.js';

let _active = null; // { machine, mic, stream, recorder, chunks, prevAutoPlay }

// Default VAD tuning, matching the pure createVadGate() defaults.
export const VAD_DEFAULTS = { threshold: 0.02, silenceMs: 1200 };

// Pure helper: raw mic RMS → 0..1 ring intensity for the overlay orb's
// --vc-level CSS var. Threshold-anchored log mapping, not a flat linear scale:
// anything at or below the VAD's own speech threshold reads as silence (0),
// `ceiling` (a loud-but-normal speaking level) reads as fully lit (1), and the
// range between grows logarithmically so quiet speech is still visibly above
// zero instead of needing to get loud before the ring reacts. Pure/testable —
// no browser globals.
export function levelToRing(rms, threshold = VAD_DEFAULTS.threshold, ceiling = 0.3) {
  if (rms <= threshold) return 0;
  const ratio = Math.log(rms / threshold) / Math.log(ceiling / threshold);
  return Math.max(0, Math.min(1, ratio));
}

// Pure resolver: given a toggle-state object, return the effective VAD config,
// falling back to defaults for missing / non-finite / non-positive values. No
// browser globals — unit-testable in Node.
export function resolveVadConfig(toggles) {
  const st = toggles || {};
  const threshold = Number(st.voiceVadThreshold);
  const silenceMs = Number(st.voiceSilenceMs);
  return {
    threshold: Number.isFinite(threshold) && threshold > 0 ? threshold : VAD_DEFAULTS.threshold,
    silenceMs: Number.isFinite(silenceMs) && silenceMs > 0 ? silenceMs : VAD_DEFAULTS.silenceMs,
  };
}

// Read persisted VAD tuning from the shared toggle-state blob (localStorage key
// `apollo-toggles`). Falls back to defaults on any error so the call still works
// if nothing is configured. Browser-only — only invoked from startCall(), never
// at import, so the module still loads cleanly in Node.
function _readVadConfig() {
  try {
    const raw = localStorage.getItem('apollo-toggles');
    return resolveVadConfig(raw ? JSON.parse(raw) : {});
  } catch (e) {
    return { ...VAD_DEFAULTS };
  }
}

function _overlay() { return document.getElementById('voice-call-overlay'); }

// True when the platform/user has asked for reduced motion. Guards the level
// painter below in addition to the CSS `@media (prefers-reduced-motion)` rule
// in voice.css that forces --vc-level to 0 on .vc-orb — belt and suspenders,
// since this also skips the (harmless but pointless) style write.
function _reducedMotion() {
  return typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches;
}

// Builds the live mic-level painter for one call. There is no rAF loop: the
// VAD's onLevel tap (per animation frame, but owned by vad.js, not here)
// calls this directly with the raw rms. A light EMA smooths frame-to-frame
// jitter without depending on the CSS transition for it, levelToRing() maps
// the smoothed value through the VAD's own threshold, and the last painted
// string is memoized so an unchanged level skips the DOM write.
function _makeLevelPainter(threshold) {
  let ema = 0;
  let lastPainted = null;
  return function paint(rms, { reset = false } = {}) {
    const ov = _overlay();
    if (!ov || _reducedMotion()) return;
    ema = reset ? 0 : 0.7 * ema + 0.3 * rms;
    const value = levelToRing(ema, threshold).toFixed(3);
    if (value === lastPainted) return;
    lastPainted = value;
    ov.style.setProperty('--vc-level', value);
  };
}

function _setState(state) {
  const ov = _overlay();
  if (ov) {
    ov.dataset.state = state;
    // The overlay's label/transcript/controls are only meaningful while a
    // call is up; expose them to assistive tech then, not while idle+hidden.
    ov.setAttribute('aria-hidden', state === 'idle' ? 'true' : 'false');
  }
  const label = document.getElementById('vc-state-label');
  if (label) {
    label.textContent = {
      listening: 'Listening…', capturing: 'Listening…', transcribing: 'Transcribing…',
      thinking: 'Thinking…', speaking: 'Speaking…', idle: '',
    }[state] || '';
  }
  if (state === 'idle' && ov) ov.style.removeProperty('--vc-level');
}
function _setTranscript(text) {
  const t = document.getElementById('vc-transcript');
  if (t) t.textContent = text || '';
}

async function _transcribe(blob) {
  const fd = new FormData();
  fd.append('file', blob, 'audio.webm');
  const res = await fetch('/api/stt/transcribe', { method: 'POST', credentials: 'same-origin', body: fd });
  if (!res.ok) throw new Error('transcribe failed');
  const data = await res.json();
  return data.text || '';
}

export async function startCall() {
  if (_active) return;
  if (!window.isSecureContext) {
    window.uiModule?.showError?.('Microphone requires HTTPS or localhost.');
    return;
  }
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true },
    });
  } catch (e) {
    window.uiModule?.showError?.('Microphone unavailable: ' + (e && e.message || e));
    return;
  }

  // Refresh TTS availability so the call reflects the CURRENT provider. The
  // aiTTSManager only probes once at page load, so a provider enabled later in
  // the session would otherwise be seen as unavailable and the reply silently
  // skipped.
  try { await window.aiTTSManager?.checkAvailability?.(); } catch {}

  const vadConfig = _readVadConfig();
  const gate = createVadGate(vadConfig);
  const paintLevel = _makeLevelPainter(vadConfig.threshold);
  const prevAutoPlay = window.aiTTSManager ? window.aiTTSManager.autoPlay : false;
  if (window.aiTTSManager) window.aiTTSManager.autoPlay = false; // we drive TTS explicitly

  const machine = createCallMachine({
    onState: _setState,
    startCapture() {
      // Chunks live in the closure, not on _active — so a call torn down
      // mid-utterance (End / barge-in) can't leave onstop dereferencing a
      // nulled _active. onstop still guards _active before touching the UI or
      // the machine, since the call may have ended before it fires.
      const chunks = [];
      const rec = new MediaRecorder(stream, { mimeType: 'audio/webm' });
      _active.recorder = rec;
      rec.ondataavailable = (ev) => { if (ev.data.size > 0) chunks.push(ev.data); };
      rec.onstop = async () => {
        if (!_active) return; // call ended mid-utterance — nothing to transcribe
        const blob = new Blob(chunks, { type: 'audio/webm' });
        try {
          const text = await _transcribe(blob);
          if (!_active) return; // ended while transcribing
          _setTranscript(text);
          machine.dispatch('transcribed', { text });
        } catch (e) {
          if (_active) machine.dispatch('error');
        }
      };
      rec.start();
    },
    stopCapture() {
      const rec = _active && _active.recorder;
      if (rec && rec.state === 'recording') rec.stop();
    },
    submitMessage(text) {
      const task = parseVoiceAssign(text);
      if (task) {
        // Delegate to a background agent task; the confirmation rides the
        // normal thinking → speaking path via assistantComplete.
        fetch('/api/tasks/assign', {
          method: 'POST', credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ prompt: task })
        }).then((r) => { if (!r.ok) throw new Error('assign failed'); return r.json(); })
          .then((d) => {
            const name = (d.task && d.task.name) || 'Your task';
            machine.dispatch('assistantComplete', { text: `${name} is running in the background. Check the Tasks panel for results.` });
          })
          .catch(() => machine.dispatch('assistantComplete', { text: 'Sorry, I could not assign that task.' }));
        return;
      }
      window.apolloSendMessage?.(text);
    },
    speak(text) {
      const mgr = window.aiTTSManager;
      if (!mgr || !mgr.available || mgr._provider === 'disabled') { machine.dispatch('speakEnd'); return; }
      mgr.enqueue(text, document.createElement('button'), () => machine.dispatch('speakEnd'));
    },
    stopSpeak() { window.aiTTSManager?.stop?.(); },
    teardown() { endCall(); },
  });

  const mic = createMicVad({
    stream,
    gate,
    onEvent: (ev) => machine.dispatch(ev === 'speechstart' ? 'speechStart' : 'speechEnd'),
    onLevel: paintLevel,
  });

  _active = { machine, mic, stream, recorder: null, prevAutoPlay, paintLevel };

  window.addEventListener('apollo:assistant-complete', _onAssistantComplete);
  _wireOverlayButtons();
  machine.dispatch('start');
}

function _onAssistantComplete(e) {
  if (_active) _active.machine.dispatch('assistantComplete', { text: e.detail && e.detail.text });
}

function _wireOverlayButtons() {
  const mute = document.getElementById('vc-mute-btn');
  const end = document.getElementById('vc-end-btn');
  if (mute) mute.onclick = () => {
    if (!_active) return;
    const muted = mute.classList.toggle('vc-active');
    if (muted) {
      _active.mic.pause();
      _active.paintLevel(0, { reset: true }); // collapse the ring instead of freezing it mid-level
    } else {
      _active.mic.resume();
    }
    mute.textContent = muted ? 'Unmute' : 'Mute';
  };
  if (end) end.onclick = () => { if (_active) _active.machine.dispatch('end'); };
}

export function endCall() {
  if (!_active) return;
  window.removeEventListener('apollo:assistant-complete', _onAssistantComplete);
  try { _active.mic.destroy(); } catch {}
  try { _active.stream.getTracks().forEach((t) => t.stop()); } catch {}
  try { window.aiTTSManager?.stop?.(); } catch {}
  if (window.aiTTSManager) window.aiTTSManager.autoPlay = _active.prevAutoPlay;
  _setState('idle');
  _setTranscript('');
  _active = null;
}

const voiceCallModule = { startCall, endCall, createCallMachine, createVadGate };
// Guard the global assignment so this module imports cleanly in Node (the pure
// createCallMachine is unit-tested there). `window` only exists in the browser.
if (typeof window !== 'undefined') window.voiceCallModule = voiceCallModule;
export default voiceCallModule;
