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
