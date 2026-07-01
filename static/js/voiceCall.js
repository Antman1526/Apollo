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
