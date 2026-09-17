// static/js/chat/floorHook.js
//
// Glue between chat.js's SSE loop and the isometric agent floor strip
// (static/js/chatFloor.js). Lives here so chat.js only needs one import and a
// handful of folded call sites.

import { createFloorStrip } from '../chatFloor.js';

// Same key/shape app.js uses for the Customize UI visibility map. applyUIVis()
// only sweeps the DOM that exists when it runs, so a strip created mid-stream
// has to read the stored preference itself.
const UI_VIS_KEY = 'apollo-ui-visibility';

function floorEnabled() {
  try {
    const raw = localStorage.getItem(UI_VIS_KEY);
    if (!raw) return true;
    const state = JSON.parse(raw);
    return !state || state['agent-floor'] !== false;
  } catch (e) {
    return true;
  }
}

/**
 * chat.js starts a fresh .agent-thread whenever a narration bubble lands
 * between two tool rounds, so one assistant turn can own several of them.
 * Walk back to the user message that opened the turn: if an earlier thread in
 * the same turn already has a strip, adopt it instead of stacking a second
 * floor into the transcript.
 */
function adoptTurnFloor(threadEl) {
  let el = threadEl.previousElementSibling;
  while (el) {
    if (el.classList.contains('msg-user')) return null;
    if (el.classList.contains('agent-thread') && el._floor) return el._floor;
    el = el.previousElementSibling;
  }
  return null;
}

/** Create (once) the floor strip that belongs to this agent-thread container. */
export function ensureFloor(threadEl) {
  if (!threadEl) return null;
  if (threadEl._floor) return threadEl._floor;
  if (!threadEl.parentNode) return null;
  const adopted = adoptTurnFloor(threadEl);
  if (adopted) {
    threadEl._floor = adopted;
    return adopted;
  }
  const wrap = document.createElement('div');
  wrap.className = 'chat-floor';
  wrap.setAttribute('data-ui-vis', 'agent-floor');
  if (!floorEnabled()) wrap.style.display = 'none';
  threadEl.parentNode.insertBefore(wrap, threadEl);
  // The floor now occupies the gap the thread's ::before rail would reach into.
  threadEl.classList.add('has-floor');
  const strip = createFloorStrip();
  strip.mount(wrap);
  threadEl._floor = strip;
  return strip;
}

export function floorToolStart(threadEl, tool) {
  const strip = ensureFloor(threadEl);
  if (strip) strip.onToolStart(tool);
}

export function floorToolEnd(threadEl, tool, ok) {
  const strip = threadEl && threadEl._floor;
  if (strip) strip.onToolEnd(tool, ok);
}

/**
 * Park the figure back at its desk. Called with no argument from the stream's
 * finalize/abort paths, where the specific thread element isn't in scope.
 */
export function floorTurnEnd(threadEl) {
  if (threadEl) {
    if (threadEl._floor) threadEl._floor.onTurnEnd();
    return;
  }
  document.querySelectorAll('.agent-thread').forEach((el) => {
    if (el._floor) el._floor.onTurnEnd();
  });
}

export default { ensureFloor, floorToolStart, floorToolEnd, floorTurnEnd };
