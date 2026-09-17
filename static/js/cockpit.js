// static/js/cockpit.js
//
// Pure reducer + renderer + mount for the composer's live "cockpit" gauge:
// tokens/sec, model name, and context-window fill. Fed by the chat stream's
// existing `metrics` and `model_info` SSE events (see chat.js's SSE loop and
// routes/chat_routes.py / src/agent_loop.py for the event shapes). No DOM
// access happens at import time — only inside createCockpit(), so this file
// stays testable under plain node:test.

const INITIAL_STATE = Object.freeze({ model: null, tps: null, used: null, window: null });

// Context-fill color bands. Empirically: 75%+ used is "getting close",
// 85%+ is "about to compact/truncate" — matches tests/test_cockpit.mjs.
const WARM_RATIO = 0.75;
const HOT_RATIO = 0.85;

function round1(n) {
  return Math.round(n * 10) / 10;
}

/**
 * Pure fold: (state, SSE event) -> next state. Unknown event types return the
 * SAME state reference (no-op), so callers can cheaply skip re-rendering.
 */
export function reduceCockpit(state, event) {
  const s = state || INITIAL_STATE;
  if (!event || typeof event !== 'object') return s;

  if (event.type === 'model_info') {
    const next = {
      ...s,
      model: event.model != null ? event.model : s.model,
      window: event.context_length != null ? event.context_length : s.window,
    };
    if (event.local != null) next.local = event.local;
    return next;
  }

  if (event.type === 'metrics') {
    const data = event.data || {};
    let used = s.used;
    if (typeof data.total_tokens === 'number') {
      used = data.total_tokens;
    } else if (typeof data.input_tokens === 'number' || typeof data.output_tokens === 'number') {
      used = (data.input_tokens || 0) + (data.output_tokens || 0);
    }
    return {
      ...s,
      tps: typeof data.tokens_per_second === 'number' ? round1(data.tokens_per_second) : s.tps,
      used,
      window: typeof data.context_length === 'number' ? data.context_length : s.window,
    };
  }

  if (event.type === 'cockpit_reset') {
    return { ...INITIAL_STATE };
  }

  return s;
}

function escapeHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function formatInt(n) {
  return Math.round(n).toLocaleString('en-US');
}

/**
 * Pure render: state -> HTML string (or '' when nothing is known yet).
 */
export function renderCockpitHTML(state) {
  const s = state || INITIAL_STATE;
  const parts = [];

  if (s.model) {
    parts.push(
      `<span class="cockpit-model" title="${escapeHtml(s.model)}">${escapeHtml(s.model)}</span>`
    );
  }

  if (typeof s.tps === 'number') {
    parts.push(`<span class="cockpit-tps">${s.tps} tok/s</span>`);
  }

  if (typeof s.used === 'number' && typeof s.window === 'number' && s.window > 0) {
    const ratio = s.used / s.window;
    const pct = Math.min(100, Math.max(0, ratio * 100));
    const band = ratio >= HOT_RATIO ? 'hot' : ratio >= WARM_RATIO ? 'warm' : 'ok';
    const title = `${formatInt(s.used)} / ${formatInt(s.window)} tokens`;
    parts.push(
      `<span class="cockpit-ctx" title="${escapeHtml(title)}">` +
        `<span class="cockpit-fill cockpit-fill--${band}" style="width:${Math.round(pct)}%"></span>` +
        `</span>`
    );
  }

  if (!parts.length) return '';
  return parts.join('');
}

/**
 * DOM mount: creates a small controller bound to an existing element.
 * Skips re-rendering when the resulting HTML hasn't changed.
 */
export function createCockpit(mountEl) {
  let state = INITIAL_STATE;
  let lastHTML = null;

  function render() {
    const html = renderCockpitHTML(state);
    if (html === lastHTML) return;
    lastHTML = html;
    if (mountEl) mountEl.innerHTML = html;
  }

  // Render once so an empty mount starts in the correct (hidden) state.
  render();

  return {
    push(event) {
      state = reduceCockpit(state, event);
      render();
    },
    reset() {
      state = reduceCockpit(state, { type: 'cockpit_reset' });
      render();
    },
    state() {
      return state;
    },
  };
}

export default { reduceCockpit, renderCockpitHTML, createCockpit };
