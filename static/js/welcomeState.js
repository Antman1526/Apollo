// ============================================
// Welcome screen — recent sessions + memory-derived suggested prompts
// ES6 module. Pure logic (pickRecentSessions/buildSuggestedPrompts/
// renderWelcomeStateHTML) must import cleanly under Node: no DOM access
// happens outside function bodies.
// ============================================

const CATEGORY_TEMPLATES = {
  fact: (t) => `What do you remember about ${t}?`,
  preference: (t) => `Something I'd like, given ${t}`,
  project: (t) => `Where are we on ${t}?`,
  goal: (t) => `Next step toward ${t}?`,
};

const FALLBACK_PROMPTS = [
  'Pick up where we left off',
  'Plan my day',
  'Teach me something new',
];

const TEXT_MAX = 40;

function _truncate(text) {
  const s = String(text == null ? '' : text).trim();
  if (s.length <= TEXT_MAX) return s;
  return s.slice(0, TEXT_MAX - 1).trimEnd() + '…';
}

function _escapeHtml(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

// Mirrors sessions.js renderSessionList()'s sidebar filter (~786) and its
// "active" sort key (~819-825). _isIncognitoSession there is a private
// module-level closure (not exported), so the incognito-id half of that
// filter can't be reused here — the name-based Nobody/Incognito check
// (also part of the same sidebar rule) approximates it.
function _isListableSession(s) {
  if (!s || s.archived) return false;
  if (s.folder === 'Assistant') return false;
  const name = (s.name || '').trim();
  if (name === 'Nobody' || name === 'Incognito') return false;
  if (s.message_count === 0) return false;
  return true;
}

function _sessionSortKey(s) {
  return s.last_message_at || s.updated_at || s.created_at || '';
}

/** Listable (sidebar-consistent), most-recently-active sessions first. */
export function pickRecentSessions(sessions, n = 4) {
  return (sessions || [])
    .filter(_isListableSession)
    .slice()
    .sort((a, b) => {
      const ak = _sessionSortKey(a);
      const bk = _sessionSortKey(b);
      if (ak === bk) return 0;
      return ak < bk ? 1 : -1;
    })
    .slice(0, n);
}

// When two memories share a category, prefer the pinned one, then the
// more recent one (routes/memory_routes.py sorts memories by this same
// pinned-then-timestamp precedence).
function _betterCandidate(a, b) {
  const ap = !!a.pinned;
  const bp = !!b.pinned;
  if (ap !== bp) return bp ? b : a;
  return (b.timestamp || 0) > (a.timestamp || 0) ? b : a;
}

/** Exactly 3 suggested prompts: one per distinct memory category (pinned,
 * then newest, wins a category), then generic fallbacks filling any
 * remaining slots. */
export function buildSuggestedPrompts(memories, n = 3) {
  const byCategory = new Map();
  for (const m of memories || []) {
    if (!m || !m.text || !CATEGORY_TEMPLATES[m.category]) continue;
    const existing = byCategory.get(m.category);
    byCategory.set(m.category, existing ? _betterCandidate(existing, m) : m);
  }
  const prompts = [];
  for (const m of byCategory.values()) {
    prompts.push(CATEGORY_TEMPLATES[m.category](_truncate(m.text)));
    if (prompts.length >= n) break;
  }
  let i = 0;
  while (prompts.length < n && i < FALLBACK_PROMPTS.length) {
    prompts.push(FALLBACK_PROMPTS[i]);
    i += 1;
  }
  return prompts.slice(0, n);
}

/** Pure HTML string builder for the two welcome-screen chip groups. Empty
 * groups render as ''. */
export function renderWelcomeStateHTML({ recent = [], prompts = [] } = {}) {
  let recentHtml = '';
  if (recent.length) {
    const chips = recent
      .map((s) => `<button type="button" class="welcome-chip" data-session-id="${_escapeHtml(s.id)}">${_escapeHtml(s.name || 'Untitled')}</button>`)
      .join('');
    recentHtml = `<div class="welcome-recent" role="group" aria-labelledby="welcome-recent-label"><span class="welcome-group-label" id="welcome-recent-label">Recent</span><div class="welcome-chip-row">${chips}</div></div>`;
  }
  let promptsHtml = '';
  if (prompts.length) {
    const chips = prompts
      .map((p) => `<button type="button" class="welcome-chip welcome-chip--prompt" data-prompt="${_escapeHtml(p)}">${_escapeHtml(p)}</button>`)
      .join('');
    promptsHtml = `<div class="welcome-prompts" role="group" aria-labelledby="welcome-prompts-label"><span class="welcome-group-label" id="welcome-prompts-label">Try</span><div class="welcome-chip-row">${chips}</div></div>`;
  }
  return { recentHtml, promptsHtml };
}

// Memories change rarely enough (and every welcome re-render re-fetches on
// each empty-composer/incognito-toggle event) that a short TTL cache avoids
// hammering the endpoint without ever showing meaningfully stale data.
const MEMORY_CACHE_TTL_MS = 60000;
let _memoryCache = null; // { data, at }

async function _defaultFetchMemories() {
  const now = Date.now();
  if (_memoryCache && now - _memoryCache.at < MEMORY_CACHE_TTL_MS) {
    return _memoryCache.data;
  }
  try {
    const res = await fetch(`${window.location.origin}/api/memory`);
    if (!res.ok) return _memoryCache ? _memoryCache.data : [];
    const data = await res.json();
    const list = Array.isArray(data) ? data : (Array.isArray(data && data.memory) ? data.memory : []);
    _memoryCache = { data: list, at: now };
    return list;
  } catch (_) {
    return _memoryCache ? _memoryCache.data : [];
  }
}

// One listener per container, ever — re-mounting only replaces the chips
// inside, so tracking by container reference keeps delegation idempotent.
const _wiredRecent = new WeakSet();
const _wiredPrompts = new WeakSet();

function _wireOnce(container, set, selector, datasetKey, handler) {
  if (!container || typeof handler !== 'function' || set.has(container)) return;
  set.add(container);
  container.addEventListener('click', (e) => {
    const btn = e.target.closest(selector);
    if (!btn || !container.contains(btn)) return;
    handler(btn.dataset[datasetKey]);
  });
}

function _isIncognitoActive() {
  const chk = document.getElementById('incognito-toggle');
  if (chk) return !!chk.checked;
  const btn = document.getElementById('incognito-btn');
  return !!(btn && btn.classList.contains('active'));
}

/** Render recent sessions + suggested prompts into #welcome-recent and
 * #welcome-prompts and wire click delegation. Nobody/incognito mode shows
 * neither group — no session history or memory should surface there. */
export async function mountWelcomeState({
  getSessions,
  fetchMemories = _defaultFetchMemories,
  onOpenSession,
  onUsePrompt,
} = {}) {
  const recentEl = document.getElementById('welcome-recent');
  const promptsEl = document.getElementById('welcome-prompts');
  if (!recentEl && !promptsEl) return;

  if (_isIncognitoActive()) {
    if (recentEl) recentEl.innerHTML = '';
    if (promptsEl) promptsEl.innerHTML = '';
    return;
  }

  const sessions = typeof getSessions === 'function' ? getSessions() || [] : [];
  const recent = pickRecentSessions(sessions);

  let memories = [];
  try {
    memories = await fetchMemories();
  } catch (_) {
    memories = [];
  }
  const prompts = buildSuggestedPrompts(memories);

  const { recentHtml, promptsHtml } = renderWelcomeStateHTML({ recent, prompts });

  if (recentEl) {
    recentEl.innerHTML = recentHtml;
    _wireOnce(recentEl, _wiredRecent, '[data-session-id]', 'sessionId', onOpenSession);
  }
  if (promptsEl) {
    promptsEl.innerHTML = promptsHtml;
    _wireOnce(promptsEl, _wiredPrompts, '[data-prompt]', 'prompt', onUsePrompt);
  }
}

// kb-hidden is a transient opacity fade (mobile keyboard open) — the welcome
// screen is still logically "showing", so it must not skip the mount; the
// CSS pointer-events:none it carries already keeps faded chips unclickable.
function _isWelcomeVisible() {
  const ws = document.getElementById('welcome-screen');
  if (!ws) return false;
  if (ws.classList.contains('hidden')) return false;
  const style = window.getComputedStyle ? window.getComputedStyle(ws) : null;
  if (style && style.display === 'none') return false;
  return true;
}

/** Mount once, then re-mount (debounced) whenever the app announces the
 * welcome screen is showing again. Skips remounts while it's hidden. */
export function initWelcomeState(deps = {}) {
  mountWelcomeState(deps);
  let debounceTimer = null;
  window.addEventListener('apollo:welcome', () => {
    if (debounceTimer) clearTimeout(debounceTimer);
    debounceTimer = setTimeout(() => {
      debounceTimer = null;
      if (!_isWelcomeVisible()) return;
      mountWelcomeState(deps);
    }, 150);
  });
}
