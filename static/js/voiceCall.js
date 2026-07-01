// static/js/voiceCall.js
//
// Hands-free voice "call mode". Two layers:
//   createCallMachine() — pure state machine over injected effects (unit-tested).
//   startCall()/endCall() — browser wiring (mic, VAD, STT, submit, TTS, overlay).
// Only the wiring touches the DOM/mic, and only when called, so this module
// imports cleanly in Node for testing the machine.

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
            eff.speak(text);
            set('speaking');
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

function _overlay() { return document.getElementById('voice-call-overlay'); }
function _setState(state) {
  const ov = _overlay();
  if (ov) ov.dataset.state = state;
  const label = document.getElementById('vc-state-label');
  if (label) {
    label.textContent = {
      listening: 'Listening…', capturing: 'Listening…', transcribing: 'Transcribing…',
      thinking: 'Thinking…', speaking: 'Speaking…', idle: '',
    }[state] || '';
  }
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

  const gate = createVadGate({ threshold: 0.02, silenceMs: 1200 });
  const prevAutoPlay = window.aiTTSManager ? window.aiTTSManager.autoPlay : false;
  if (window.aiTTSManager) window.aiTTSManager.autoPlay = false; // we drive TTS explicitly

  const machine = createCallMachine({
    onState: _setState,
    startCapture() {
      _active.chunks = [];
      const rec = new MediaRecorder(stream, { mimeType: 'audio/webm' });
      _active.recorder = rec;
      rec.ondataavailable = (ev) => { if (ev.data.size > 0) _active.chunks.push(ev.data); };
      rec.onstop = async () => {
        const blob = new Blob(_active.chunks, { type: 'audio/webm' });
        try {
          const text = await _transcribe(blob);
          _setTranscript(text);
          machine.dispatch('transcribed', { text });
        } catch (e) {
          machine.dispatch('error');
        }
      };
      rec.start();
    },
    stopCapture() {
      const rec = _active && _active.recorder;
      if (rec && rec.state === 'recording') rec.stop();
    },
    submitMessage(text) { window.apolloSendMessage?.(text); },
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
  });

  _active = { machine, mic, stream, recorder: null, chunks: [], prevAutoPlay };

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
    if (muted) _active.mic.pause(); else _active.mic.resume();
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
