# Voice Call Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a hands-free, real-time voice "call mode" to Apollo — speak a turn, Apollo transcribes it locally, replies in the chat thread, and speaks the reply aloud, then auto-listens for the next turn, with barge-in.

**Architecture:** Frontend-only orchestrator over existing pieces. Pure, unit-tested logic (an energy-based VAD gate and a call state machine) drives thin browser glue that reuses Apollo's local STT endpoint (`/api/stt/transcribe`), the chat submit path, and `aiTTSManager` for speech. A dedicated overlay renders the call state.

**Tech Stack:** Vanilla ES modules (no build step), Web Audio API (`AnalyserNode`), `MediaRecorder`, Node's built-in test runner (`node --test tests/*.mjs`).

**Design spec:** `docs/superpowers/specs/2026-06-30-voice-call-mode-design.md`

---

## File Structure

**Create:**
- `static/js/vad.js` — pure `createVadGate()` (RMS → speechstart/speechend) + browser `createMicVad()` (Web Audio wrapper). Pure part is unit-tested.
- `static/js/voiceCall.js` — pure `createCallMachine()` (state machine over injected effects) + `startCall()`/`endCall()` browser wiring.
- `tests/test_voice_vad.mjs` — Node unit tests for `createVadGate`.
- `tests/test_voice_call_machine.mjs` — Node unit tests for `createCallMachine`.

**Modify:**
- `static/index.html` — call overlay markup + styles; call entry button in the input toolbar; `<script type="module">` for `voiceCall.js`.
- `static/app.js` — `window.apolloSendMessage(text)` helper; import + wire the call entry button (gated on STT enabled).
- `static/js/chat.js` — dispatch `apollo:assistant-complete` CustomEvent when an assistant message finishes streaming.
- `package.json` — add the two new `.mjs` tests to the `test:js` script.

**Reuse (unchanged):** `static/js/voiceRecorder.js` (secure-context + getUserMedia patterns), `static/js/tts-ai.js` (`window.aiTTSManager`: `.available`, `._provider`, `.autoPlay`, `.enqueue(text, btn, resetFn)`, `.stop()`).

---

## Task 1: VAD gate (pure logic)

A pure state gate: feed it RMS energy + a timestamp, it returns `'speechstart'`, `'speechend'`, or `null`. No browser globals, so Node can test it.

**Files:**
- Create: `static/js/vad.js`
- Test: `tests/test_voice_vad.mjs`

- [ ] **Step 1: Write the failing test**

Create `tests/test_voice_vad.mjs`:

```js
import assert from 'node:assert/strict';
import test from 'node:test';

import { createVadGate } from '../static/js/vad.js';

test('stays silent below threshold', () => {
  const g = createVadGate({ threshold: 0.02, silenceMs: 1000 });
  assert.equal(g.push(0.0, 0), null);
  assert.equal(g.push(0.01, 50), null);
  assert.equal(g.isSpeaking, false);
});

test('emits speechstart when energy crosses threshold', () => {
  const g = createVadGate({ threshold: 0.02, silenceMs: 1000 });
  assert.equal(g.push(0.05, 0), 'speechstart');
  assert.equal(g.isSpeaking, true);
  assert.equal(g.push(0.05, 50), null, 'no repeat speechstart while loud');
});

test('emits speechend after sustained silence', () => {
  const g = createVadGate({ threshold: 0.02, silenceMs: 1000 });
  g.push(0.05, 0);
  assert.equal(g.push(0.0, 500), null, 'silence shorter than silenceMs stays speaking');
  assert.equal(g.push(0.0, 1000), 'speechend', 'silence >= silenceMs ends the turn');
  assert.equal(g.isSpeaking, false);
});

test('a loud blip during silence resets the silence timer', () => {
  const g = createVadGate({ threshold: 0.02, silenceMs: 1000 });
  g.push(0.05, 0);
  g.push(0.0, 900);
  assert.equal(g.push(0.05, 950), null, 'still speaking, timer reset');
  assert.equal(g.push(0.0, 1500), null, 'only 550ms of silence since blip');
  assert.equal(g.push(0.0, 1950), 'speechend');
});

test('reset() returns to not-speaking', () => {
  const g = createVadGate({ threshold: 0.02, silenceMs: 1000 });
  g.push(0.05, 0);
  g.reset();
  assert.equal(g.isSpeaking, false);
  assert.equal(g.push(0.0, 10), null);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node --test tests/test_voice_vad.mjs`
Expected: FAIL — cannot find module `../static/js/vad.js`.

- [ ] **Step 3: Write minimal implementation**

Create `static/js/vad.js` (pure part only for now):

```js
// static/js/vad.js
//
// Voice-activity detection for call mode. Two exports:
//   createVadGate() — pure RMS→event gate, unit-testable, no browser globals.
//   createMicVad()  — Web Audio wrapper that feeds live mic RMS into a gate.
// Only createMicVad touches browser APIs, and only when called (never at
// import), so this module imports cleanly in Node for testing.

export function createVadGate({ threshold = 0.02, silenceMs = 1200 } = {}) {
  let speaking = false;
  let lastLoudMs = 0;

  return {
    // Returns 'speechstart' | 'speechend' | null.
    push(rms, nowMs) {
      const loud = rms >= threshold;
      if (loud) lastLoudMs = nowMs;

      if (!speaking) {
        if (loud) {
          speaking = true;
          return 'speechstart';
        }
        return null;
      }

      if (!loud && nowMs - lastLoudMs >= silenceMs) {
        speaking = false;
        return 'speechend';
      }
      return null;
    },
    get isSpeaking() {
      return speaking;
    },
    reset() {
      speaking = false;
      lastLoudMs = 0;
    },
  };
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `node --test tests/test_voice_vad.mjs`
Expected: PASS — 5 tests pass.

- [ ] **Step 5: Commit**

```bash
git add static/js/vad.js tests/test_voice_vad.mjs
git commit -m "feat(voice): pure VAD gate for call mode"
```

---

## Task 2: Call state machine (pure logic)

A pure state machine that transitions between call states on events and invokes injected effect callbacks. No browser globals — Node-testable with fake effects.

States: `idle → listening → capturing → transcribing → thinking → speaking → listening`.
Effects (injected): `startCapture, stopCapture, submitMessage, speak, stopSpeak, teardown, onState`.

**Files:**
- Modify: `static/js/voiceCall.js` (create it)
- Test: `tests/test_voice_call_machine.mjs`

- [ ] **Step 1: Write the failing test**

Create `tests/test_voice_call_machine.mjs`:

```js
import assert from 'node:assert/strict';
import test from 'node:test';

import { createCallMachine } from '../static/js/voiceCall.js';

function spyEffects() {
  const calls = [];
  const rec = (name) => (arg) => calls.push([name, arg]);
  return {
    calls,
    startCapture: rec('startCapture'),
    stopCapture: rec('stopCapture'),
    submitMessage: rec('submitMessage'),
    speak: rec('speak'),
    stopSpeak: rec('stopSpeak'),
    teardown: rec('teardown'),
    onState: rec('onState'),
  };
}

test('start moves idle -> listening', () => {
  const eff = spyEffects();
  const m = createCallMachine(eff);
  assert.equal(m.state, 'idle');
  m.dispatch('start');
  assert.equal(m.state, 'listening');
});

test('speechStart begins capture', () => {
  const eff = spyEffects();
  const m = createCallMachine(eff);
  m.dispatch('start');
  m.dispatch('speechStart');
  assert.equal(m.state, 'capturing');
  assert.ok(eff.calls.some(([n]) => n === 'startCapture'));
});

test('speechEnd stops capture and transcribes', () => {
  const eff = spyEffects();
  const m = createCallMachine(eff);
  m.dispatch('start');
  m.dispatch('speechStart');
  m.dispatch('speechEnd');
  assert.equal(m.state, 'transcribing');
  assert.ok(eff.calls.some(([n]) => n === 'stopCapture'));
});

test('non-empty transcript submits and goes to thinking', () => {
  const eff = spyEffects();
  const m = createCallMachine(eff);
  m.dispatch('start');
  m.dispatch('speechStart');
  m.dispatch('speechEnd');
  m.dispatch('transcribed', { text: '  hello  ' });
  assert.equal(m.state, 'thinking');
  const submit = eff.calls.find(([n]) => n === 'submitMessage');
  assert.deepEqual(submit, ['submitMessage', 'hello']);
});

test('empty transcript returns to listening without submit', () => {
  const eff = spyEffects();
  const m = createCallMachine(eff);
  m.dispatch('start');
  m.dispatch('speechStart');
  m.dispatch('speechEnd');
  m.dispatch('transcribed', { text: '   ' });
  assert.equal(m.state, 'listening');
  assert.ok(!eff.calls.some(([n]) => n === 'submitMessage'));
});

test('assistantComplete speaks the reply', () => {
  const eff = spyEffects();
  const m = createCallMachine(eff);
  m.dispatch('start');
  m.dispatch('speechStart');
  m.dispatch('speechEnd');
  m.dispatch('transcribed', { text: 'hi' });
  m.dispatch('assistantComplete', { text: 'hello there' });
  assert.equal(m.state, 'speaking');
  assert.deepEqual(eff.calls.find(([n]) => n === 'speak'), ['speak', 'hello there']);
});

test('speakEnd returns to listening', () => {
  const eff = spyEffects();
  const m = createCallMachine(eff);
  m.dispatch('start');
  m.dispatch('speechStart');
  m.dispatch('speechEnd');
  m.dispatch('transcribed', { text: 'hi' });
  m.dispatch('assistantComplete', { text: 'reply' });
  m.dispatch('speakEnd');
  assert.equal(m.state, 'listening');
});

test('barge-in: speaking + speechStart stops speech and captures', () => {
  const eff = spyEffects();
  const m = createCallMachine(eff);
  m.dispatch('start');
  m.dispatch('speechStart');
  m.dispatch('speechEnd');
  m.dispatch('transcribed', { text: 'hi' });
  m.dispatch('assistantComplete', { text: 'a long reply' });
  m.dispatch('speechStart');
  assert.equal(m.state, 'capturing');
  assert.ok(eff.calls.some(([n]) => n === 'stopSpeak'));
  assert.ok(eff.calls.some(([n]) => n === 'startCapture'));
});

test('end tears down and returns to idle from any state', () => {
  const eff = spyEffects();
  const m = createCallMachine(eff);
  m.dispatch('start');
  m.dispatch('speechStart');
  m.dispatch('end');
  assert.equal(m.state, 'idle');
  assert.ok(eff.calls.some(([n]) => n === 'teardown'));
});

test('assistantComplete with empty text skips speaking', () => {
  const eff = spyEffects();
  const m = createCallMachine(eff);
  m.dispatch('start');
  m.dispatch('speechStart');
  m.dispatch('speechEnd');
  m.dispatch('transcribed', { text: 'hi' });
  m.dispatch('assistantComplete', { text: '' });
  assert.equal(m.state, 'listening');
  assert.ok(!eff.calls.some(([n]) => n === 'speak'));
});

test('onState fires on each transition', () => {
  const eff = spyEffects();
  const m = createCallMachine(eff);
  m.dispatch('start');
  const states = eff.calls.filter(([n]) => n === 'onState').map(([, s]) => s);
  assert.deepEqual(states, ['listening']);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node --test tests/test_voice_call_machine.mjs`
Expected: FAIL — cannot find export `createCallMachine` from `../static/js/voiceCall.js`.

- [ ] **Step 3: Write minimal implementation**

Create `static/js/voiceCall.js` with the pure machine (browser wiring added in Task 4):

```js
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `node --test tests/test_voice_call_machine.mjs`
Expected: PASS — all machine tests pass.

- [ ] **Step 5: Register both tests in the npm script + commit**

Edit `package.json`, extend `test:js` to include the new files:

```json
    "test:js": "node --test tests/test_paperclip_floor_ui.mjs tests/test_system_status_card.mjs tests/test_system_status_actions.mjs tests/test_theme_presets.mjs tests/test_voice_vad.mjs tests/test_voice_call_machine.mjs"
```

Run: `npm run test:js`
Expected: PASS — all JS tests including the two new files.

```bash
git add static/js/voiceCall.js tests/test_voice_call_machine.mjs package.json
git commit -m "feat(voice): pure call-mode state machine"
```

---

## Task 3: Web Audio VAD wrapper (browser glue)

Add `createMicVad()` to `vad.js`. It runs an animation-frame loop computing RMS from an `AnalyserNode` and feeds a gate. Browser-only; verified in-app, not unit-tested (keep it thin so all logic lives in the tested gate).

**Files:**
- Modify: `static/js/vad.js`

- [ ] **Step 1: Append the Web Audio wrapper**

Add to the end of `static/js/vad.js`:

```js
// Browser-only: drive a gate from a live mic MediaStream. Returns handles to
// pause (mute), resume, and destroy. References Web Audio globals only here,
// inside the function body — never at module top level.
export function createMicVad({ stream, gate, onEvent }) {
  const AudioCtx = window.AudioContext || window.webkitAudioContext;
  const ctx = new AudioCtx();
  const source = ctx.createMediaStreamSource(stream);
  const analyser = ctx.createAnalyser();
  analyser.fftSize = 512;
  source.connect(analyser);

  const buf = new Float32Array(analyser.fftSize);
  let raf = 0;
  let paused = false;

  function tick() {
    analyser.getFloatTimeDomainData(buf);
    let sum = 0;
    for (let i = 0; i < buf.length; i++) sum += buf[i] * buf[i];
    const rms = Math.sqrt(sum / buf.length);
    if (!paused) {
      const ev = gate.push(rms, performance.now());
      if (ev) onEvent(ev, rms);
    }
    raf = requestAnimationFrame(tick);
  }
  raf = requestAnimationFrame(tick);

  return {
    pause() { paused = true; },
    resume() { paused = false; },
    destroy() {
      cancelAnimationFrame(raf);
      try { source.disconnect(); } catch {}
      try { ctx.close(); } catch {}
    },
  };
}
```

- [ ] **Step 2: Confirm the pure tests still pass (import must stay Node-safe)**

Run: `node --test tests/test_voice_vad.mjs`
Expected: PASS — the new export must not break Node import (no top-level browser refs).

- [ ] **Step 3: Commit**

```bash
git add static/js/vad.js
git commit -m "feat(voice): Web Audio mic VAD wrapper"
```

---

## Task 4: Assistant-complete event (chat.js seam)

Call mode needs to know when an assistant reply finishes streaming and get its text. Dispatch a `CustomEvent` at the existing completion point in `chat.js` (where `addAITTSButton(footerTarget, accumulated)` is already called).

**Files:**
- Modify: `static/js/chat.js` (around line 2487, the `addAITTSButton` completion block)

- [ ] **Step 1: Locate the completion point**

Run: `grep -n "addAITTSButton(footerTarget" static/js/chat.js`
Expected: a line near 2487 inside the message-complete block, with `accumulated` in scope.

- [ ] **Step 2: Dispatch the event right after the TTS button is added**

Immediately after the `addAITTSButton(footerTarget, accumulated);` line, add:

```js
        // Call mode listens for this to advance its state machine and speak.
        try {
          window.dispatchEvent(new CustomEvent('apollo:assistant-complete', {
            detail: { text: accumulated || '' },
          }));
        } catch (e) { /* non-fatal */ }
```

- [ ] **Step 3: Verify no syntax error / JS tests still green**

Run: `npm run test:js`
Expected: PASS (unaffected — this file isn't imported by the Node tests, but run to confirm nothing else broke).
Then load the app and confirm normal chat still streams (manual smoke, no console errors).

- [ ] **Step 4: Commit**

```bash
git add static/js/chat.js
git commit -m "feat(voice): dispatch apollo:assistant-complete on reply finish"
```

---

## Task 5: Programmatic send helper (app.js)

Call mode injects a transcript as a normal chat message. Expose one helper in `app.js` where the submit path is in scope, so `voiceCall.js` stays DOM-agnostic.

**Files:**
- Modify: `static/app.js` (near `handleSubmit` / the send-button block around line 3671-3826)

- [ ] **Step 1: Find the submit entry point**

Run: `grep -n "handleSubmit\|chatForm\|getElementById('message')\|el('message')" static/app.js | head`
Expected: `chatForm.onsubmit = handleSubmit;` and `const messageInput = el('message');` are visible near line 3671-3675.

- [ ] **Step 2: Add the helper next to the send-button wiring**

After the send-button click handler block (after the `if (sendBtn) { ... }` block near line 3827), add:

```js
  // Programmatic send for call mode: set the input to the transcript and submit
  // through the normal path so the turn is a real chat message.
  window.apolloSendMessage = function (text) {
    const clean = (text || '').trim();
    if (!clean) return;
    const input = el('message');
    if (!input) return;
    input.value = clean;
    input.dispatchEvent(new Event('input', { bubbles: true }));
    try { _updateSendBtnIcon(); } catch {}
    handleSubmit(new Event('submit'));
  };
```

- [ ] **Step 3: Verify in-app**

Reload the app. In the browser console run `window.apolloSendMessage('ping from console')`.
Expected: a chat message "ping from console" is sent and Apollo replies. No console errors.

- [ ] **Step 4: Commit**

```bash
git add static/app.js
git commit -m "feat(voice): window.apolloSendMessage programmatic send helper"
```

---

## Task 6: Call overlay markup + styles (index.html)

Add the overlay DOM and CSS. Hidden by default; shown via `data-state` set by `voiceCall.js`.

**Files:**
- Modify: `static/index.html`

- [ ] **Step 1: Add the overlay markup**

Just before the closing `</body>` (or alongside other top-level overlays; run `grep -n "</body>" static/index.html` to place it), insert:

```html
<div id="voice-call-overlay" class="voice-call-overlay" data-state="idle" aria-hidden="true" role="dialog" aria-label="Voice call">
  <div class="vc-status">
    <span class="vc-dot"></span>
    <span>call mode · local whisper · on-device</span>
  </div>
  <div class="vc-orb"><span class="vc-orb-inner" id="vc-orb-icon"></span></div>
  <div class="vc-caption">
    <p class="vc-state-label" id="vc-state-label">Connecting…</p>
    <p class="vc-transcript" id="vc-transcript"></p>
  </div>
  <div class="vc-controls">
    <button type="button" id="vc-mute-btn" class="vc-btn">Mute</button>
    <button type="button" id="vc-end-btn" class="vc-btn vc-btn-danger">End call</button>
  </div>
</div>
```

- [ ] **Step 2: Add the styles**

In the main `<style>` block (or `static/style.css`; keep consistent with the file's existing pattern — run `grep -n "</style>" static/index.html` if inline), add:

```css
.voice-call-overlay {
  position: fixed; inset: 0; z-index: 1000;
  display: none; flex-direction: column; align-items: center; justify-content: center;
  gap: 1.5rem; background: rgba(0,0,0,0.6); backdrop-filter: blur(2px);
}
.voice-call-overlay[data-state]:not([data-state="idle"]) { display: flex; }
.vc-status { display: flex; align-items: center; gap: 8px; color: #9aa0a6; font-size: 13px; }
.vc-dot { width: 8px; height: 8px; border-radius: 50%; background: #34a853; }
.vc-orb {
  width: 150px; height: 150px; border-radius: 50%;
  display: flex; align-items: center; justify-content: center;
  background: rgba(55,138,221,0.15); border: 2px solid rgba(55,138,221,0.6);
  transition: transform .12s ease, background .2s ease, border-color .2s ease;
}
.vc-orb-inner {
  width: 96px; height: 96px; border-radius: 50%;
  display: flex; align-items: center; justify-content: center; color: #fff;
  background: #378add;
}
.voice-call-overlay[data-state="listening"] .vc-orb { animation: vc-pulse 1.4s ease-in-out infinite; }
.voice-call-overlay[data-state="thinking"] .vc-orb-inner { background: #7f77dd; }
.voice-call-overlay[data-state="speaking"] .vc-orb-inner { background: #1d9e75; }
.voice-call-overlay[data-state="capturing"] .vc-orb { transform: scale(1.08); border-color: #34a853; }
@keyframes vc-pulse { 0%,100% { transform: scale(1); } 50% { transform: scale(1.06); } }
.vc-caption { text-align: center; }
.vc-state-label { margin: 0; font-size: 16px; font-weight: 500; color: #f1f3f4; }
.vc-transcript { margin: 4px 0 0; font-size: 14px; color: #bdc1c6; min-height: 20px; }
.vc-controls { display: flex; gap: 12px; }
.vc-btn { padding: 8px 16px; border-radius: 8px; border: 1px solid #5f6368; background: transparent; color: #e8eaed; cursor: pointer; }
.vc-btn:hover { background: rgba(255,255,255,0.06); }
.vc-btn.vc-active { border-color: #378add; color: #8ab4f8; }
.vc-btn-danger { border-color: #a3312d; color: #f28b82; }
```

- [ ] **Step 3: Add the module script tag**

Near the existing `<script type="module" src="/static/js/voiceRecorder.js"></script>` line (run `grep -n "voiceRecorder.js\"" static/index.html`), add after it:

```html
<script type="module" src="/static/js/voiceCall.js"></script>
```

- [ ] **Step 4: Verify markup renders (overlay stays hidden at idle)**

Reload the app. The overlay must NOT be visible (it is `data-state="idle"`). In console: `document.getElementById('voice-call-overlay').dataset.state = 'listening'` → overlay appears with pulsing orb; set back to `'idle'` → hides.

- [ ] **Step 5: Commit**

```bash
git add static/index.html
git commit -m "feat(voice): call overlay markup and styles"
```

---

## Task 7: Browser wiring — startCall/endCall (voiceCall.js)

Wire the machine to real collaborators: shared mic stream, `createMicVad`, per-utterance `MediaRecorder`, STT POST, `window.apolloSendMessage`, `window.aiTTSManager`, and the overlay. Browser-only; verified in-app.

**Files:**
- Modify: `static/js/voiceCall.js`

- [ ] **Step 1: Append the wiring + default export**

Add to the end of `static/js/voiceCall.js`:

```js
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
window.voiceCallModule = voiceCallModule;
export default voiceCallModule;
```

- [ ] **Step 2: Confirm Node tests still pass (import stays Node-safe at machine level)**

Run: `npm run test:js`
Expected: PASS. Note: `voiceCall.js` now `import`s `./vad.js`; both are ESM with no top-level browser refs, so the `createCallMachine` import in the test still resolves. If Node errors on `window`/`document` at import, move any offending reference inside a function.

- [ ] **Step 3: Commit**

```bash
git add static/js/voiceCall.js
git commit -m "feat(voice): call-mode browser wiring (mic, STT, TTS, overlay)"
```

---

## Task 8: Call entry button (index.html + app.js)

Add a call button to the input toolbar, shown only when STT is enabled, that starts the call.

**Files:**
- Modify: `static/index.html` (input toolbar), `static/app.js`

- [ ] **Step 1: Locate the input toolbar**

Run: `grep -n "chat-input-bottom\|chat-input-bar\|class=\"tools" static/index.html | head`
Expected: the bottom toolbar container of the chat input (near lines 1010-1128).

- [ ] **Step 2: Add the call button**

Inside the input bottom toolbar (mirror an existing icon button's classes; place next to the other tool buttons), add:

```html
<button type="button" id="call-mode-btn" class="input-tool-btn" title="Start voice call" style="display:none">
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72c.13.81.36 1.6.7 2.34a2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.74-1.27a2 2 0 0 1 2.11-.45c.74.34 1.53.57 2.34.7A2 2 0 0 1 22 16.92z"/></svg>
</button>
```

(If `input-tool-btn` is not the toolbar's button class, use the class of an existing sibling button found in Step 1.)

- [ ] **Step 3: Wire the button in app.js**

Near the send/mic wiring (after the `window.apolloSendMessage` helper from Task 5), add:

```js
  // Call-mode entry button — visible only when STT is enabled (same gate as
  // the send/mic button). Opens the call overlay and starts the loop.
  const callBtn = el('call-mode-btn');
  function _syncCallBtn() {
    if (!callBtn) return;
    callBtn.style.display = _isSttEnabled() ? '' : 'none';
  }
  if (callBtn) {
    callBtn.addEventListener('click', () => window.voiceCallModule?.startCall());
  }
  window._syncCallModeBtn = _syncCallBtn;
  _syncCallBtn();
```

Then, so the button appears/disappears when the STT provider changes, add a call to `_syncCallBtn()` inside the existing `_updateSendBtnIcon` function (which already reflects STT state). Run `grep -n "function _updateSendBtnIcon" static/app.js`, and at the end of that function (just before `sendBtn.dataset.mode = newMode;`) add:

```js
    try { if (window._syncCallModeBtn) window._syncCallModeBtn(); } catch {}
```

- [ ] **Step 4: Verify in-app**

Reload. With STT disabled in settings, the call button is hidden. Enable STT (Settings → set `stt_enabled` + a provider). The call button appears. Click it → overlay opens in `listening` state.

- [ ] **Step 5: Commit**

```bash
git add static/index.html static/app.js
git commit -m "feat(voice): call-mode entry button gated on STT enabled"
```

---

## Task 9: End-to-end verification (in-app)

The mic/audio/overlay loop cannot be unit-tested; verify the whole thing live.

**Files:** none (verification only)

- [ ] **Step 1: Pre-req — ensure providers are configured**

In Settings, enable STT (`stt_enabled` + a working `faster-whisper`/endpoint provider) and TTS (`tts_enabled` + Piper or endpoint). Confirm `/api/stt/stats` and `/api/tts/stats` report `available: true`.

- [ ] **Step 2: Full turn**

Click the call button. Speak a short question ("what day is it"). Expected sequence in the overlay: `listening` (orb pulses) → on silence, `transcribing` → `thinking` → `speaking` (reply is spoken) → back to `listening`. The turn appears as a normal message pair in the chat thread behind the overlay.

- [ ] **Step 3: Barge-in**

Ask something that yields a long reply; while Apollo is speaking, start talking. Expected: TTS stops immediately and the orb returns to capturing/listening.

- [ ] **Step 4: Mute + End**

Click Mute → orb stops reacting to your voice; Unmute restores it. Click End call → overlay closes, mic released (browser mic indicator turns off), `aiTTSManager.autoPlay` restored to its pre-call value.

- [ ] **Step 5: Guard paths**

Disable STT in settings → call button disappears. Deny mic permission when prompted → error toast, no overlay left open. (Optional) run the `/verify` skill to drive the app and capture the loop.

- [ ] **Step 6: Final commit (docs/CHANGELOG if applicable)**

```bash
git add -A
git commit -m "docs(voice): note call mode in README/feature list" --allow-empty
```

---

## Self-Review

**Spec coverage:**
- Local Whisper STT → Tasks 7 (`_transcribe` → `/api/stt/transcribe`). ✓
- Energy-based VAD + manual stop → Tasks 1, 3; manual stop = End button (Task 6/7). ✓
- Barge-in with echoCancellation → Task 2 (machine), Task 7 (`getUserMedia` constraints + `stopSpeak`). ✓
- Dedicated overlay with state orb → Task 6; driven by `_setState` (Task 7). ✓
- Each turn a real chat message → Task 5 (`apolloSendMessage`) + Task 7 (`submitMessage`). ✓
- Auto-speak reply + resume listening → Task 4 (event) + Task 7 (`speak`/`speakEnd`). ✓
- Error handling / guards (secure context, mic denied, STT disabled, empty transcript, single call, TTS disabled) → Task 7 (`startCall` guards, `_transcribe` catch), Task 8 (STT gate), Task 2 (empty transcript → listening). ✓
- Testing: `vad.js` + machine unit-tested (Tasks 1-2); glue verified in-app (Task 9). ✓

**Placeholder scan:** No TBD/TODO; every code step shows full code. The one conditional ("if `input-tool-btn` is not the class, use the sibling's class") is a genuine adaptation instruction with a concrete lookup, not a placeholder.

**Type/name consistency:** Effect names (`startCapture`, `stopCapture`, `submitMessage`, `speak`, `stopSpeak`, `teardown`, `onState`) match between the machine (Task 2), its tests, and the wiring (Task 7). Events (`start`, `speechStart`, `speechEnd`, `transcribed`, `assistantComplete`, `speakEnd`, `end`, `error`) match between machine, tests, wiring, and the `apollo:assistant-complete` bridge. `window.apolloSendMessage`, `window.voiceCallModule`, `window._syncCallModeBtn`, `window.aiTTSManager` used consistently.

**Deviation from spec (noted):** TTS is completion-based (speak the full reply on `assistant-complete`) rather than sentence-streaming during generation. This is a deliberate v1 simplification — it gives a clean `speakEnd` signal to resume listening and avoids double-speech with the existing `autoPlay` pipeline (which is disabled during the call). Sentence-streaming TTS is a future enhancement.
