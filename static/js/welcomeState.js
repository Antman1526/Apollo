// ============================================
// Welcome screen — recent sessions + memory-derived suggested prompts
// ES6 module. Pure logic (pickRecentSessions/buildSuggestedPrompts/
// renderWelcomeStateHTML) must import cleanly under Node: no DOM access
// happens outside function bodies.
// ============================================

const CATEGORY_TEMPLATES = {
  project: (t) => `Give me a status summary of ${t}`,
  goal: (t) => `What's the next step toward ${t}?`,
  preference: (t) => `Draft a short note in my usual style about ${t}`,
  identity: (t) => `Draft a short note in my usual style about ${t}`,
  fact: (t) => `Draft a short note in my usual style about ${t}`,
  task: (t) => `Help me plan: ${t}`,
};

const FALLBACK_PROMPTS = [
  'Summarize what we worked on recently',
  'Help me plan today',
  "Explain something I'm curious about",
];

const TEXT_MAX = 60;

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

function _sessionSortKey(s) {
  return s.updated_at || s.last_message_at || s.created_at || '';
}

/** Non-archived sessions, most-recently-updated first. */
export function pickRecentSessions(sessions, n = 4) {
  return (sessions || [])
    .filter((s) => s && !s.archived)
    .slice()
    .sort((a, b) => (_sessionSortKey(b) > _sessionSortKey(a) ? 1 : -1))
    .slice(0, n);
}

/** Exactly 3 suggested prompts: one per distinct memory category, then
 * generic fallbacks filling any remaining slots. */
export function buildSuggestedPrompts(memories, n = 3) {
  const seenCategories = new Set();
  const prompts = [];
  for (const m of memories || []) {
    if (!m || !m.text) continue;
    const template = CATEGORY_TEMPLATES[m.category];
    if (!template || seenCategories.has(m.category)) continue;
    seenCategories.add(m.category);
    prompts.push(template(_truncate(m.text)));
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
    recentHtml = `<div class="welcome-recent"><span class="welcome-group-label">Recent</span><div class="welcome-chip-row">${chips}</div></div>`;
  }
  let promptsHtml = '';
  if (prompts.length) {
    const chips = prompts
      .map((p) => `<button type="button" class="welcome-chip welcome-chip--prompt" data-prompt="${_escapeHtml(p)}">${_escapeHtml(p)}</button>`)
      .join('');
    promptsHtml = `<div class="welcome-prompts"><span class="welcome-group-label">Try</span><div class="welcome-chip-row">${chips}</div></div>`;
  }
  return { recentHtml, promptsHtml };
}

async function _defaultFetchMemories() {
  try {
    const res = await fetch(`${window.location.origin}/api/memory`);
    if (!res.ok) return [];
    const data = await res.json();
    if (Array.isArray(data)) return data;
    return Array.isArray(data && data.memory) ? data.memory : [];
  } catch (_) {
    return [];
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

/** Render recent sessions + suggested prompts into #welcome-recent and
 * #welcome-prompts and wire click delegation. */
export async function mountWelcomeState({
  getSessions,
  fetchMemories = _defaultFetchMemories,
  onOpenSession,
  onUsePrompt,
} = {}) {
  const recentEl = document.getElementById('welcome-recent');
  const promptsEl = document.getElementById('welcome-prompts');
  if (!recentEl && !promptsEl) return;

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

function _isWelcomeVisible() {
  const ws = document.getElementById('welcome-screen');
  if (!ws) return false;
  if (ws.classList.contains('hidden') || ws.classList.contains('kb-hidden')) return false;
  const style = window.getComputedStyle ? window.getComputedStyle(ws) : null;
  if (style && (style.display === 'none' || style.visibility === 'hidden')) return false;
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
