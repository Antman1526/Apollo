// static/js/chat/cockpitHook.js
//
// Glue between chat.js's SSE loop and the composer cockpit gauge
// (static/js/cockpit.js). Lives here so chat.js only needs one import and a
// couple of folded call sites, mirroring floorHook.js's pattern.

import { createCockpit } from '../cockpit.js';

let cockpit = null;
let lastModel = null;

function ensureCockpit() {
  if (cockpit) return cockpit;
  const el = document.getElementById('cockpit');
  if (!el) return null;
  cockpit = createCockpit(el);
  return cockpit;
}

/**
 * Called from chat.js's SSE loop for `metrics` and `model_info` events.
 * `isBg` is true for background (non-active-session) streams, which never
 * touch the visible gauge.
 */
export function cockpitEvent(json, isBg) {
  if (isBg) return;
  const c = ensureCockpit();
  if (!c) return;
  if (json && json.type === 'model_info' && json.model && json.model !== lastModel) {
    lastModel = json.model;
    c.reset();
  }
  c.push(json);
}

export function cockpitReset() {
  lastModel = null;
  const c = ensureCockpit();
  if (c) c.reset();
}

// A new chat / welcome screen means the next stream is a fresh session —
// clear the gauge. chat.js and sessions.js are both at their line-count
// ratchet baselines, so we subscribe to this existing signal instead of
// adding a call site for a session-switch path.
window.addEventListener('apollo:welcome', cockpitReset);

export default { cockpitEvent, cockpitReset };
