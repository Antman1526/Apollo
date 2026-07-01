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

  // NOTE: deliberate lowercase 'speechstart'/'speechend' (DOM-event style). The
  // Task 7 call-mode wiring bridges these to the machine's camelCase
  // 'speechStart'/'speechEnd' events — do not "fix" the casing on one side only.
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

  // An AudioContext created outside a user gesture can start 'suspended', which
  // makes the RMS loop read silence forever. Resume before the tick loop begins.
  if (ctx.state === 'suspended') { try { ctx.resume(); } catch {} }

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
