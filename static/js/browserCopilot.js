// Apollo — browser co-pilot helpers (pure; no DOM). browserPanel.js turns
// server {type:"agent_action"} events into a ghost cursor + caption with these.

const READ_ACTIONS = new Set(['get_visible_text', 'get_page_html']);

function hostOf(url) {
  const raw = String(url || '').trim();
  if (!raw) return '';
  try {
    const parsed = new URL(raw);
    return parsed.host || raw;
  } catch (_) {
    return raw;
  }
}

/**
 * Human caption for one agent_action event, or '' when the phase carries no
 * new information (a "done" just lets the previous caption fade).
 */
export function captionFor(event) {
  const ev = event || {};
  const action = String(ev.action || '');
  const phase = String(ev.phase || 'start');
  const detail = String(ev.detail || '');
  if (phase === 'error') return `Agent: failed — ${detail || action || 'unknown error'}`;
  if (phase !== 'start') return '';
  if (action === 'navigate') return `Agent: opening ${hostOf(detail) || 'a page'}`;
  if (action === 'click') return `Agent: clicking \`${detail}\``;
  if (action === 'type') return `Agent: typing ${detail}`;
  if (action === 'wait_for_selector') return `Agent: waiting for \`${detail}\``;
  if (action === 'screenshot') return 'Agent: taking a screenshot';
  if (action === 'execute_script') return 'Agent: running a script';
  if (action === 'go_back') return 'Agent: going back';
  if (action === 'go_forward') return 'Agent: going forward';
  if (action === 'reload_page') return 'Agent: reloading the page';
  if (READ_ACTIONS.has(action)) return 'Agent: reading the page';
  return action ? `Agent: ${action.replace(/_/g, ' ')}` : '';
}

/**
 * Inverse of browserPanel's canvasCoords: screencast device px → client px.
 * `rect` is the canvas getBoundingClientRect(); returns null when any input
 * cannot be mapped (no frame yet, collapsed canvas, non-numeric point).
 */
export function deviceToClient(x, y, rect, deviceW, deviceH) {
  if (!rect || !(rect.width > 0) || !(rect.height > 0)) return null;
  if (!(deviceW > 0) || !(deviceH > 0)) return null;
  if (typeof x !== 'number' || typeof y !== 'number' || Number.isNaN(x) || Number.isNaN(y)) return null;
  return {
    x: rect.left + x * (rect.width / deviceW),
    y: rect.top + y * (rect.height / deviceH),
  };
}
