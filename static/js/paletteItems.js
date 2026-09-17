// Command-palette item model — pure helpers, no DOM, no imports.
// Consumed by search-chat.js (the Ctrl+K palette) and unit-tested directly.

export const PALETTE_GROUPS = ['Actions', 'Sessions', 'Models'];
export const GROUP_CAP = 8;

/** Minimal HTML escaper so callers can build rows without pulling in ui.js. */
export function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

// Characters that start a new "word" for the word-start bonus below.
const WORD_BREAK = /[\s\-_/.:@,()[\]]/;

/**
 * Case-insensitive subsequence match.
 * Returns 0 when `query` is not a subsequence of `text`, otherwise a positive
 * score: word-start hits and contiguous runs score higher, an exact prefix
 * outranks everything, and shorter haystacks break ties.
 */
export function fuzzyScore(query, text) {
  const q = String(query ?? '').toLowerCase();
  const t = String(text ?? '').toLowerCase();
  if (!q) return 1;
  if (!t) return 0;

  let score = 0;
  let prevIdx = -2;
  let from = 0;
  for (let i = 0; i < q.length; i++) {
    const idx = t.indexOf(q[i], from);
    if (idx === -1) return 0;
    let bonus = 1;
    if (idx === prevIdx + 1) bonus += 4;                              // contiguous run
    if (idx === 0 || WORD_BREAK.test(t[idx - 1])) bonus += 6;         // word start
    score += bonus;
    prevIdx = idx;
    from = idx + 1;
  }
  if (t.startsWith(q)) score += 100;                                  // exact prefix wins
  score += Math.max(0, 40 - t.length) / 100;                          // tie-break: shorter
  return score;
}

function sessionSortKey(s) {
  return s.last_message_at || s.updated_at || s.created_at || '';
}

/** "3m ago" / "5h ago" / "Yesterday" / "Mar 4" — empty when there is no date. */
export function relativeTime(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  const ms = d.getTime();
  if (!Number.isFinite(ms)) return '';
  const diff = Date.now() - ms;
  if (diff < 0) return '';
  const min = Math.floor(diff / 60000);
  if (min < 1) return 'just now';
  if (min < 60) return min + 'm ago';
  const hr = Math.floor(min / 60);
  if (hr < 24) return hr + 'h ago';
  const days = Math.floor(hr / 24);
  if (days === 1) return 'Yesterday';
  if (days < 7) return days + 'd ago';
  return d.toLocaleDateString([], { month: 'short', day: 'numeric' });
}

function actionItems(actions) {
  return (actions || [])
    .filter(a => a && a.id)
    .map(a => ({
      group: 'Actions', kind: 'action', id: a.id,
      label: a.label || a.id, hint: a.hint || '', score: 0,
    }));
}

function sessionItems(sessions) {
  return (sessions || [])
    .filter(s => s && s.id)
    .slice()
    .sort((a, b) => {
      const ak = sessionSortKey(a);
      const bk = sessionSortKey(b);
      if (ak === bk) return 0;
      return ak < bk ? 1 : -1;            // most recently active first
    })
    .map(s => ({
      group: 'Sessions', kind: 'session', id: s.id,
      label: (s.name || '').trim() || 'Untitled chat',
      hint: relativeTime(sessionSortKey(s)) || s.mode || '',
      score: 0,
    }));
}

function modelItems(models) {
  return (models || [])
    .filter(m => m && m.id)
    .map(m => ({
      group: 'Models', kind: 'model', id: m.id,
      label: m.label || m.id, hint: m.endpoint || '', score: 0,
      url: m.url || '', modelId: m.modelId || m.label || m.id,
      endpointId: m.endpointId ?? null,
    }));
}

function rankGroup(list, query) {
  if (!query) return list.slice(0, GROUP_CAP).map(item => ({ ...item, score: 1 }));
  const scored = [];
  for (const item of list) {
    const direct = fuzzyScore(query, item.label);
    // Hints (endpoint name, relative time) match at half weight so a model's
    // provider is searchable without letting it outrank a name match.
    const viaHint = item.hint ? fuzzyScore(query, item.label + ' ' + item.hint) / 2 : 0;
    const score = Math.max(direct, viaHint);
    if (score > 0) scored.push({ ...item, score });
  }
  scored.sort((a, b) => b.score - a.score);
  return scored.slice(0, GROUP_CAP);
}

/**
 * Build the palette rows for a query.
 * @param {string} query
 * @param {{actions?: Array, sessions?: Array, models?: Array}} ctx
 * @returns {Array<{group:string,id:string,label:string,hint:string,score:number,kind:string}>}
 */
export function buildPaletteItems(query, ctx = {}) {
  const q = String(query ?? '').trim();
  const byGroup = {
    Actions: actionItems(ctx.actions),
    Sessions: sessionItems(ctx.sessions),
    Models: modelItems(ctx.models),
  };
  const out = [];
  for (const group of PALETTE_GROUPS) {
    out.push(...rankGroup(byGroup[group], q));
  }
  return out;
}

export default { buildPaletteItems, fuzzyScore, escapeHtml, relativeTime, PALETTE_GROUPS, GROUP_CAP };
