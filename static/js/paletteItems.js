// Command-palette item model — pure helpers, no DOM.
// Consumed by search-chat.js (the Ctrl+K palette) and unit-tested directly.
// Session listability/ordering is owned by welcomeState.js so the palette,
// the welcome screen and the sidebar agree on what counts as a chat.

import { isListableSession, sessionSortKey } from './welcomeState.js';

export const PALETTE_GROUPS = ['Actions', 'Sessions', 'Models'];
export const GROUP_CAP = 8;

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

/**
 * Scattered subsequence hits ("brl" inside "borrow checker") are noise, not
 * matches. Require roughly one word-start or contiguous hit per typed
 * character before a row is allowed to show up at all.
 */
export function scoreFloor(query) {
  return String(query ?? '').length * 4;
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
    .filter(isListableSession)
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
  if (!query) return { items: list.slice(0, GROUP_CAP).map(i => ({ ...i, score: 1 })), total: list.length };
  const floor = scoreFloor(query);
  const scored = [];
  for (const item of list) {
    // Only a model's hint (its endpoint) is searchable — "2h ago" on a chat
    // row is not something anyone types, and matching it invents hits.
    const viaHint = (item.kind === 'model' && item.hint) ? fuzzyScore(query, item.hint) / 2 : 0;
    const score = Math.max(fuzzyScore(query, item.label), viaHint);
    if (score >= floor && score > 0) scored.push({ ...item, score });
  }
  scored.sort((a, b) => b.score - a.score);
  return { items: scored.slice(0, GROUP_CAP), total: scored.length };
}

/**
 * Build the palette groups for a query, in display order.
 * `total` is the number of matches before the per-group cap, so callers can
 * render a "+N more" affordance.
 * @returns {Array<{group:string, items:Array, total:number}>}
 */
export function buildPaletteGroups(query, ctx = {}) {
  const q = String(query ?? '').trim();
  const source = {
    Actions: actionItems(ctx.actions),
    Sessions: sessionItems(ctx.sessions),
    Models: modelItems(ctx.models),
  };
  return PALETTE_GROUPS.map(group => ({ group, ...rankGroup(source[group], q) }));
}

/**
 * Flat list of palette rows for a query.
 * @param {string} query
 * @param {{actions?: Array, sessions?: Array, models?: Array}} ctx
 * @returns {Array<{group:string,id:string,label:string,hint:string,score:number,kind:string}>}
 */
export function buildPaletteItems(query, ctx = {}) {
  const out = [];
  for (const g of buildPaletteGroups(query, ctx)) out.push(...g.items);
  return out;
}

export default {
  buildPaletteItems, buildPaletteGroups, fuzzyScore, scoreFloor,
  relativeTime, PALETTE_GROUPS, GROUP_CAP,
};
