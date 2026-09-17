// static/js/cockpit.js
//
// Pure reducer + renderer + mount for the composer's live "cockpit" gauge:
// tokens/sec, model name, and context-window fill. Fed by the chat stream's
// existing `metrics` and `model_info` SSE events (see chat.js's SSE loop and
// routes/chat_routes.py / src/agent_loop.py for the event shapes). No DOM
// access happens at import time — only inside createCockpit(), so this file
// stays testable under plain node:test.

const INITIAL_STATE = Object.freeze({
  model: null,
  tps: null,
  window: null,
  percent: null,
  // 'real'  -> percent came straight from the backend's context_percent
  // 'fallback' -> percent was computed here from input+output/window
  fillSource: null,
  // Only meaningful (non-null) when fillSource === 'fallback' — it's the
  // number the fallback tooltip needs. Real percent doesn't carry a trustworthy
  // token count (see the context_percent note below), so it stays null then.
  used: null,
});

// Bands match the footer's context-usage ring (static/js/chatRenderer.js
// ~1513-1517: warm at 70%, hot at 85%) so the two indicators never disagree
// at a glance. An earlier draft of this gauge used 75/85 (the plan said 75/90) — replaced to line
// up with the existing ring instead of inventing a second scale.
const WARM_PCT = 70;
const HOT_PCT = 85;

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
    // The backend's model_info event carries no context window (see
    // routes/chat_routes.py); the caller (cockpitHook.js) seeds
    // `context_length` here from window._realContextLengths when it knows
    // one for this model, so the bar doesn't have to wait for a metrics
    // event to learn the window size.
    return {
      ...s,
      model: event.model != null ? event.model : s.model,
      window: typeof event.context_length === 'number' ? event.context_length : s.window,
    };
  }

  if (event.type === 'metrics') {
    const data = event.data || {};
    let percent = s.percent;
    let fillSource = s.fillSource;
    let used = s.used;
    const win = typeof data.context_length === 'number' ? data.context_length : s.window;

    if (typeof data.context_percent === 'number' && Number.isFinite(data.context_percent)) {
      // Real, backend-computed figure (src/agent_loop.py _compute_final_metrics)
      // based on the LAST round's input tokens. Always preferred over summing
      // input_tokens + output_tokens ourselves: input_tokens accumulates
      // across agent rounds (src/agent_loop.py ~1758, real_input_tokens +=
      // round_input), so on a multi-round tool turn that sum overcounts what
      // is actually sitting in the model's context window.
      percent = data.context_percent;
      fillSource = 'real';
      used = null;
    } else {
      let sum = null;
      if (typeof data.total_tokens === 'number') {
        sum = data.total_tokens;
      } else if (typeof data.input_tokens === 'number' || typeof data.output_tokens === 'number') {
        sum = (data.input_tokens || 0) + (data.output_tokens || 0);
      }
      if (sum != null && win) {
        percent = Math.min(100, Math.max(0, (sum / win) * 100));
        fillSource = 'fallback';
        used = sum;
      }
    }

    return {
      ...s,
      tps: typeof data.tokens_per_second === 'number' ? round1(data.tokens_per_second) : s.tps,
      percent,
      fillSource,
      used,
      window: win,
    };
  }

  if (event.type === 'compacted') {
    // routes/chat_routes.py ~905 emits {type:'compacted', context_length}
    // after an auto-compaction. chat.js is at its line-count ratchet, so
    // there's no room to add a call site for this event in its SSE loop —
    // the reducer stays able to handle it (and is covered by a test) in case
    // a future caller wires it in, but today the gauge simply reflects the
    // drop in usage at the very next `metrics` event, which always follows
    // a compaction.
    return {
      ...s,
      percent: null,
      fillSource: null,
      used: null,
      window: typeof event.context_length === 'number' ? event.context_length : s.window,
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
    // "Last response" — this is the tps of the most recently completed turn,
    // not a live-updating estimate; be honest about that in the tooltip.
    parts.push(`<span class="cockpit-tps" title="Last response">${s.tps} tok/s</span>`);
  }

  if (typeof s.percent === 'number') {
    const pct = Math.min(100, Math.max(0, s.percent));
    const band = pct >= HOT_PCT ? 'hot' : pct >= WARM_PCT ? 'warm' : 'ok';
    const isFallback = s.fillSource === 'fallback' && typeof s.used === 'number' && typeof s.window === 'number';
    const title = isFallback
      ? `${formatInt(s.used)} / ${formatInt(s.window)} tokens`
      : `${Math.round(pct)}% of window`;
    const ariaLabel = isFallback
      ? `${formatInt(s.used)} of ${formatInt(s.window)} tokens`
      : `Context ${Math.round(pct)}% of window`;
    parts.push(
      `<span class="cockpit-ctx" role="img" title="${escapeHtml(title)}" aria-label="${escapeHtml(ariaLabel)}">` +
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
