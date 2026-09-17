// static/js/chat/cockpitHook.js
//
// Glue between chat.js's SSE loop and the composer cockpit gauge
// (static/js/cockpit.js). Lives here so chat.js only needs one import and a
// couple of folded call sites, mirroring floorHook.js's pattern.

import { createCockpit } from '../cockpit.js';
// sessions.js is already loaded by chat.js — this import resolves to the
// same module instance (ES modules are cached per URL), so it adds no extra
// script load or side effect of its own.
import { getCurrentSessionId } from '../sessions.js';

let cockpit = null;
let lastModel = null;
let historyObserver = null;
let lastSeenSessionId; // left undefined until the observer's first callback

function ensureCockpit() {
  if (cockpit) return cockpit;
  const el = document.getElementById('cockpit');
  if (!el) return null;
  cockpit = createCockpit(el);
  return cockpit;
}

export function cockpitReset() {
  lastModel = null;
  const c = ensureCockpit();
  if (c) c.reset();
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

  if (json && json.type === 'model_info') {
    if (json.model && json.model !== lastModel) {
      lastModel = json.model;
      c.reset();
    }
    // The backend's model_info carries no context window (routes/chat_routes.py).
    // chatRenderer.js (~1506-1509) remembers each model's real window the first
    // time a metrics event reports one; reuse it here so the bar can show the
    // window before this session's own first metrics event arrives.
    let evt = json;
    if (json.context_length == null && json.model && window._realContextLengths &&
        typeof window._realContextLengths[json.model] === 'number') {
      evt = { ...json, context_length: window._realContextLengths[json.model] };
    }
    c.push(evt);
    return;
  }

  c.push(json);
}

/**
 * A session switch (new chat, or picking a different existing session in the
 * sidebar) must clear the gauge — its model/tps/fill belong to whichever
 * conversation was open before. chat.js and sessions.js are both pinned at
 * their line-count ratchet baselines, so instead of adding a call site there,
 * watch the one DOM node every switch path touches: #chat-history.
 *   - New chat / an empty session: sessions.js clears it to 0 children and
 *     nothing else lands in the same synchronous pass, so the observer still
 *     sees childElementCount === 0 when it runs.
 * - Switching to another session WITH history: sessions.js clears then
 *   synchronously re-populates it, so the element is never observably empty
 *   by the time this callback runs — the session-id change is what catches
 *   that case instead.
 */
function handleHistoryMutation() {
  const currentId = getCurrentSessionId();
  const historyEl = document.getElementById('chat-history');
  const emptied = !!historyEl && historyEl.childElementCount === 0;
  const switched = lastSeenSessionId !== undefined && currentId !== lastSeenSessionId;
  if (emptied || switched) cockpitReset();
  lastSeenSessionId = currentId;
}

function ensureHistoryObserver() {
  if (historyObserver) return;
  const historyEl = document.getElementById('chat-history');
  if (!historyEl) return;
  lastSeenSessionId = getCurrentSessionId();
  historyObserver = new MutationObserver(handleHistoryMutation);
  historyObserver.observe(historyEl, { childList: true });
}

ensureHistoryObserver();

export default { cockpitEvent, cockpitReset };
