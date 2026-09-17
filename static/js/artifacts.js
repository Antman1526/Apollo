// static/js/artifacts.js
//
// Live artifact pane — renders HTML / SVG output from the model in a sandboxed
// iframe beside the chat (a body-level flex sibling of #chat-container, the
// same mechanism the Documents pane uses so the chat column shrinks instead
// of being covered).
//
// Security model
// --------------
//   * The preview iframe is `sandbox="allow-scripts allow-modals allow-popups"`
//     — and never the same-origin flag. The artifact therefore runs in an opaque
//     origin: no cookies, no localStorage, no same-origin fetches to /api.
//   * Every srcdoc gets a restrictive CSP <meta> as the first head element
//     (`default-src 'none'`), so the sandboxed page can only inline-style,
//     inline-script and load data:/https: images + fonts.
//   * about:srcdoc frames inherit the host page's nonce-based CSP. Inline
//     <script> tags in the artifact are therefore stamped with the page's
//     nonce so they may run *inside the sandbox* — the nonce cannot be used
//     to escape it (opaque origin) and never leaves this process.
//   * "Open in new tab" ships a Blob URL wrapper document whose only content
//     is another sandboxed srcdoc iframe — the model HTML never runs with the
//     app origin even in the new tab.
//
// The pure helpers (`extractArtifacts`, `wrapForPreview`, `artifactFromCode`)
// are DOM-free so they can be unit-tested under plain node; everything that
// touches the document is guarded behind `typeof document !== 'undefined'`
// and built lazily on first open.

const MAX_ARTIFACTS = 8;
const MIN_ARTIFACT_CHARS = 40;
const PANE_ID = 'artifact-pane';
const AUTO_OPEN_PREF_KEY = 'artifacts_auto_open';
const UI_VIS_STORAGE_KEY = 'apollo-ui-visibility';

// Sandbox flags for the preview frame. Kept as a single constant so a grep
// for the same-origin sandbox flag over this file stays empty (pinned by tests).
const FRAME_SANDBOX = 'allow-scripts allow-modals allow-popups';

const CSP_META = '<meta http-equiv="Content-Security-Policy" content="'
  + "default-src 'none'; img-src data: https:; style-src 'unsafe-inline'; "
  + "script-src 'unsafe-inline'; font-src data: https:"
  + '">';

const DEFAULT_STYLE = '<style>'
  + 'html{color-scheme:light dark}'
  + 'body{margin:0;padding:16px;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;line-height:1.5}'
  + 'img,svg,video,canvas{max-width:100%}'
  + '</style>';

const SVG_STYLE = '<style>'
  + 'html{color-scheme:light dark}'
  + 'html,body{height:100%}'
  + 'body{margin:0;padding:16px;box-sizing:border-box;display:flex;align-items:center;justify-content:center;'
  + 'font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}'
  + 'svg{max-width:100%;max-height:100%;height:auto}'
  + '</style>';

// ── Pure helpers ────────────────────────────────────────────────────────

function _stripTags(s) {
  return String(s || '').replace(/<[^>]*>/g, '').replace(/\s+/g, ' ').trim();
}

function _decodeEntities(s) {
  return String(s || '')
    .replace(/&lt;/g, '<').replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&amp;/g, '&');
}

/** Resolve the language of a fence to 'html' | 'svg' | null. */
function _artifactLang(lang, code) {
  const l = String(lang || '').toLowerCase();
  const head = String(code || '').trimStart().slice(0, 16).toLowerCase();
  if (l === 'html') return 'html';
  if (l === 'svg') return 'svg';
  if (l === 'xml' && head.startsWith('<svg')) return 'svg';
  return null;
}

/** Title: <title> text, else first <h1> text, else a per-kind default. */
export function detectTitle(code, lang) {
  const src = String(code || '');
  const t = src.match(/<title[^>]*>([\s\S]*?)<\/title>/i);
  if (t) {
    const txt = _decodeEntities(_stripTags(t[1]));
    if (txt) return txt.slice(0, 120);
  }
  const h = src.match(/<h1[^>]*>([\s\S]*?)<\/h1>/i);
  if (h) {
    const txt = _decodeEntities(_stripTags(h[1]));
    if (txt) return txt.slice(0, 120);
  }
  return lang === 'svg' ? 'SVG artifact' : 'HTML artifact';
}

/**
 * Build one artifact record from raw code + fence language. Returns null when
 * the language is not previewable or the block is too short to matter.
 */
export function artifactFromCode(code, lang, index) {
  const cleaned = String(code || '')
    .replace(/\r\n/g, '\n')
    .replace(/^\s*\n+/, '')
    .replace(/\s+$/, '');
  const kind = _artifactLang(lang, cleaned);
  if (!kind) return null;
  if (cleaned.trim().length < MIN_ARTIFACT_CHARS) return null;
  const i = Number.isFinite(index) ? index : 0;
  return {
    id: `${kind}-${i}`,
    lang: kind,
    title: detectTitle(cleaned, kind),
    code: cleaned,
  };
}

/**
 * Pure: scan markdown text for fenced blocks tagged `html`, `svg`, or `xml`
 * whose content starts with `<svg`. Returns [{id, lang, title, code}].
 */
export function extractArtifacts(markdownText) {
  const out = [];
  const src = String(markdownText || '');
  // Same fence grammar as static/js/markdown.js so the preview button and the
  // auto-open scan agree on which blocks exist.
  const re = /```(\w+)?\n([\s\S]*?)```/g;
  let m;
  let n = 0;
  while ((m = re.exec(src)) !== null) {
    const art = artifactFromCode(m[2], m[1], n);
    if (!art) continue;
    art.id = `${art.lang}-${out.length}`;
    out.push(art);
    n++;
  }
  return out;
}

function _hasNonce() {
  if (typeof document === 'undefined') return '';
  try {
    const s = document.querySelector('script[nonce]');
    return (s && s.nonce) ? String(s.nonce) : '';
  } catch (_) { return ''; }
}

/**
 * Stamp the host page's CSP nonce onto <script> tags so inline scripts may
 * run inside the sandboxed frame (about:srcdoc inherits the parent CSP).
 * No-op outside a browser or when the page has no nonce.
 */
function _applyScriptNonce(html) {
  const nonce = _hasNonce();
  if (!nonce || !/^[A-Za-z0-9+/=_-]+$/.test(nonce)) return html;
  return html.replace(/<script\b(?![^>]*\bnonce=)/gi, `<script nonce="${nonce}"`);
}

function _injectCspMeta(doc) {
  // CSP <meta> must be the first element in <head> so it governs everything
  // that follows (a script placed before it would not be covered).
  const headOpen = doc.match(/<head\b[^>]*>/i);
  if (headOpen) {
    const at = headOpen.index + headOpen[0].length;
    return doc.slice(0, at) + CSP_META + doc.slice(at);
  }
  const htmlOpen = doc.match(/<html\b[^>]*>/i);
  if (htmlOpen) {
    const at = htmlOpen.index + htmlOpen[0].length;
    return doc.slice(0, at) + '<head>' + CSP_META + '</head>' + doc.slice(at);
  }
  const doctype = doc.match(/^\s*<!doctype\b[^>]*>/i);
  if (doctype) {
    const at = doctype[0].length;
    return doc.slice(0, at) + '<head>' + CSP_META + '</head>' + doc.slice(at);
  }
  return '<head>' + CSP_META + '</head>' + doc;
}

/**
 * Pure: turn an artifact into an srcdoc string. HTML is used as-is when it is
 * already a full document, otherwise wrapped in a minimal one; SVG is wrapped
 * in a centering document. The CSP <meta> is always the first head element.
 */
export function wrapForPreview(artifact) {
  const lang = artifact && artifact.lang === 'svg' ? 'svg' : 'html';
  const code = String((artifact && artifact.code) || '');
  let doc;
  if (lang === 'svg') {
    doc = '<!DOCTYPE html><html><head>'
      + '<meta charset="utf-8">'
      + '<meta name="viewport" content="width=device-width, initial-scale=1">'
      + '<base target="_blank">'
      + SVG_STYLE
      + '</head><body>' + code + '</body></html>';
  } else if (/<html\b/i.test(code) || /<!doctype\b/i.test(code)) {
    doc = code;
  } else {
    doc = '<!DOCTYPE html><html><head>'
      + '<meta charset="utf-8">'
      + '<meta name="viewport" content="width=device-width, initial-scale=1">'
      + '<base target="_blank">'
      + DEFAULT_STYLE
      + '</head><body>' + code + '</body></html>';
  }
  return _applyScriptNonce(_injectCspMeta(doc));
}

/**
 * Pure: the document opened by "Open in new tab". A trusted wrapper whose
 * only content is a sandboxed srcdoc iframe carrying the artifact, so the
 * model HTML stays in an opaque origin even though the Blob URL itself is
 * same-origin with the app.
 */
export function wrapForNewTab(artifact) {
  const inner = wrapForPreview(artifact);
  const title = _escapeHtml((artifact && artifact.title) || 'Artifact');
  return '<!DOCTYPE html><html><head><meta charset="utf-8">'
    + '<meta name="viewport" content="width=device-width, initial-scale=1">'
    + '<title>' + title + '</title>'
    + '<style>html,body{margin:0;height:100%;background:#fff}iframe{border:0;width:100%;height:100%;display:block}</style>'
    + '</head><body>'
    + '<iframe sandbox="' + FRAME_SANDBOX + '" referrerpolicy="no-referrer" title="' + title + '" srcdoc="'
    + _escapeHtml(inner) + '"></iframe>'
    + '</body></html>';
}

function _escapeHtml(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

/** Pref: auto-open the pane when a finished reply contains an artifact. */
export function isAutoOpenEnabled() {
  if (typeof localStorage === 'undefined') return true;
  try {
    const raw = localStorage.getItem(UI_VIS_STORAGE_KEY);
    if (!raw) return true;
    const st = JSON.parse(raw);
    return !(st && st[AUTO_OPEN_PREF_KEY] === false);
  } catch (_) { return true; }
}

// ── Pane state (browser only) ───────────────────────────────────────────

const _hasDom = typeof document !== 'undefined' && typeof window !== 'undefined';

let _items = [];          // [{id, lang, title, code, sessionId}] — oldest first
let _activeId = null;
let _pane = null;
let _seq = 0;
let _inited = false;

function _currentSessionId() {
  try {
    const sm = window.sessionModule;
    if (sm && typeof sm.getCurrentSessionId === 'function') return sm.getCurrentSessionId() || null;
  } catch (_) {}
  return null;
}

async function _toast(msg, opts) {
  try {
    const ui = await import('./ui.js');
    const fn = ui.showToast || (ui.default && ui.default.showToast);
    if (fn) fn(msg, opts);
  } catch (_) {}
}

function _icon(name) {
  const A = 'width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"';
  switch (name) {
    case 'reload': return `<svg ${A}><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>`;
    case 'copy': return `<svg ${A}><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>`;
    case 'doc': return `<svg ${A}><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg>`;
    case 'newtab': return `<svg ${A}><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>`;
    case 'close': return `<svg ${A}><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>`;
    default: return '';
  }
}

function _buildPane() {
  const pane = document.createElement('aside');
  pane.id = PANE_ID;
  pane.className = 'artifact-pane';
  pane.setAttribute('role', 'complementary');
  pane.setAttribute('aria-label', 'Artifact preview');
  pane.tabIndex = -1;
  pane.innerHTML = `
    <header class="artifact-header">
      <span class="artifact-kind" aria-hidden="true"></span>
      <span class="artifact-title" title=""></span>
      <div class="artifact-actions" role="toolbar" aria-label="Artifact actions">
        <button type="button" class="artifact-btn" data-act="reload" title="Reload preview" aria-label="Reload">${_icon('reload')}<span class="artifact-btn-label">Reload</span></button>
        <button type="button" class="artifact-btn" data-act="copy" title="Copy code" aria-label="Copy code">${_icon('copy')}<span class="artifact-btn-label">Copy code</span></button>
        <button type="button" class="artifact-btn" data-act="doc" title="Open in Documents" aria-label="Open in Documents">${_icon('doc')}<span class="artifact-btn-label">Open in Documents</span></button>
        <button type="button" class="artifact-btn" data-act="newtab" title="Open in new tab" aria-label="Open in new tab">${_icon('newtab')}<span class="artifact-btn-label">New tab</span></button>
        <button type="button" class="artifact-btn artifact-close" data-act="close" title="Close (Esc)" aria-label="Close artifact pane">${_icon('close')}</button>
      </div>
    </header>
    <div class="artifact-tabs" role="tablist" aria-label="Artifacts" hidden></div>
    <div class="artifact-body">
      <iframe class="artifact-frame" sandbox="${FRAME_SANDBOX}" referrerpolicy="no-referrer" title="Artifact preview"></iframe>
    </div>`;

  pane.addEventListener('click', (e) => {
    const btn = e.target && e.target.closest && e.target.closest('button[data-act]');
    if (!btn || !pane.contains(btn)) return;
    e.preventDefault();
    e.stopPropagation();
    const act = btn.dataset.act;
    const art = _active();
    if (act === 'close') { close(); return; }
    if (!art) return;
    if (act === 'reload') _render(art, { force: true });
    else if (act === 'copy') _copyCode(art);
    else if (act === 'doc') openInDocuments(art);
    else if (act === 'newtab') openInNewTab(art);
  });

  pane.addEventListener('click', (e) => {
    const tab = e.target && e.target.closest && e.target.closest('.artifact-tab[data-id]');
    if (!tab) return;
    e.preventDefault();
    e.stopPropagation();
    _activate(tab.dataset.id);
  });

  // Esc closes the pane while focus is inside it. preventDefault so the
  // global Escape arbiter in ui.js (which checks e.defaultPrevented) leaves
  // modals / thinking blocks alone for this keypress.
  pane.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    e.preventDefault();
    e.stopPropagation();
    close();
  });

  return pane;
}

function _mount() {
  if (_pane && _pane.isConnected) return _pane;
  const stale = document.getElementById(PANE_ID);
  if (stale) stale.remove();
  _pane = _buildPane();
  const container = document.getElementById('chat-container');
  if (container && container.parentNode) container.after(_pane);
  else document.body.appendChild(_pane);
  document.body.classList.add('artifact-view');
  return _pane;
}

function _active() {
  return _items.find(a => a.id === _activeId) || null;
}

function _renderTabs() {
  if (!_pane) return;
  const strip = _pane.querySelector('.artifact-tabs');
  if (!strip) return;
  if (_items.length <= 1) {
    strip.hidden = true;
    strip.innerHTML = '';
    return;
  }
  strip.hidden = false;
  strip.innerHTML = '';
  _items.forEach((a, i) => {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'artifact-tab' + (a.id === _activeId ? ' active' : '');
    b.dataset.id = a.id;
    b.setAttribute('role', 'tab');
    b.setAttribute('aria-selected', a.id === _activeId ? 'true' : 'false');
    b.title = a.title;
    b.textContent = `${i + 1} · ${a.title}`;
    strip.appendChild(b);
  });
}

function _render(art, { force = false } = {}) {
  if (!_pane || !art) return;
  const titleEl = _pane.querySelector('.artifact-title');
  const kindEl = _pane.querySelector('.artifact-kind');
  if (titleEl) { titleEl.textContent = art.title; titleEl.title = art.title; }
  if (kindEl) kindEl.textContent = art.lang.toUpperCase();
  const frame = _pane.querySelector('iframe.artifact-frame');
  if (!frame) return;
  // Re-derive on every render (never cache the nonce-stamped markup on the
  // record, so "Open in Documents" / "Copy code" always see the raw code).
  const srcdoc = wrapForPreview(art);
  if (force) frame.removeAttribute('srcdoc');
  frame.srcdoc = srcdoc;
  _renderTabs();
}

function _activate(id) {
  const art = _items.find(a => a.id === id);
  if (!art) return;
  _activeId = id;
  _render(art);
}

function _remember(artifact) {
  const sid = _currentSessionId();
  // Tabs are per chat session — drop entries that belong to another one.
  if (sid) _items = _items.filter(a => !a.sessionId || a.sessionId === sid);
  const dup = _items.find(a => a.lang === artifact.lang && a.code === artifact.code);
  if (dup) {
    dup.title = artifact.title || dup.title;
    return dup;
  }
  const rec = {
    id: `art-${Date.now().toString(36)}-${++_seq}`,
    lang: artifact.lang,
    title: artifact.title || (artifact.lang === 'svg' ? 'SVG artifact' : 'HTML artifact'),
    code: artifact.code,
    sessionId: sid,
  };
  _items.push(rec);
  while (_items.length > MAX_ARTIFACTS) _items.shift(); // evict oldest
  return rec;
}

async function _copyCode(art) {
  try {
    const ui = await import('./ui.js');
    const fn = ui.copyToClipboard || (ui.default && ui.default.copyToClipboard);
    if (fn) { await fn(art.code); return; }
  } catch (_) {}
  try {
    await navigator.clipboard.writeText(art.code);
    _toast('Copied');
  } catch (_) {
    _toast('Copy failed');
  }
}

/** POST /api/document then hand the fresh doc to the Documents pane. */
export async function openInDocuments(artifact) {
  const art = artifact || _active();
  if (!art) return null;
  try {
    const sessionId = _currentSessionId();
    const body = { title: art.title, content: art.code, language: art.lang };
    if (sessionId) body.session_id = sessionId;
    const res = await fetch('/api/document', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      let detail = '';
      try { const j = await res.json(); detail = j.detail || j.error || ''; } catch (_) {}
      throw new Error(detail || `HTTP ${res.status}`);
    }
    const doc = await res.json();
    const mod = await import('./document.js');
    const inject = mod.injectFreshDoc || (mod.default && mod.default.injectFreshDoc);
    if (typeof inject !== 'function') throw new Error('Documents module unavailable');
    inject(doc);
    _toast('Opened in Documents');
    return doc;
  } catch (e) {
    _toast('Could not open in Documents: ' + (e && e.message ? e.message : 'unknown error'), { duration: 5000 });
    return null;
  }
}

/** Blob URL + noopener; the blob is a trusted wrapper around a sandboxed frame. */
export function openInNewTab(artifact) {
  const art = artifact || _active();
  if (!art || !_hasDom) return;
  let url = null;
  try {
    const blob = new Blob([wrapForNewTab(art)], { type: 'text/html' });
    url = URL.createObjectURL(blob);
    // With `noopener` window.open() returns null by design, so a blocked
    // popup is indistinguishable here — the browser surfaces its own UI.
    window.open(url, '_blank', 'noopener,noreferrer');
  } catch (e) {
    _toast('Could not open new tab');
  } finally {
    // Revoke once the new tab has had a chance to fetch it.
    if (url) setTimeout(() => { try { URL.revokeObjectURL(url); } catch (_) {} }, 60000);
  }
}

/**
 * Open (or focus) the pane on `artifact` ({lang, title, code}).
 * `autoOpened` marks an open triggered by stream finalization rather than a
 * click — in that case we do not steal keyboard focus from the chat input.
 */
export function open(artifact, { autoOpened = false } = {}) {
  if (!_hasDom || !artifact || !artifact.code) return null;
  init();
  const rec = _remember(artifact);
  _activeId = rec.id;
  const pane = _mount();
  _render(rec);
  if (!autoOpened) {
    try { pane.focus({ preventScroll: true }); } catch (_) { try { pane.focus(); } catch (__) {} }
  }
  return rec;
}

/** Convenience for the code-block buttons: build the record, then open. */
export function openFromCode(code, lang, opts) {
  const art = artifactFromCode(code, lang, 0);
  if (!art) {
    // A block that is too short for the auto-scan can still be previewed on
    // explicit request — fall back to a bare record.
    const kind = _artifactLang(lang, code);
    if (!kind) return null;
    return open({ lang: kind, title: detectTitle(code, kind), code: String(code || '') }, opts);
  }
  return open(art, opts);
}

/** Stream-finalization hook: auto-open the LAST artifact of a reply. */
export function autoOpenFromMessage(markdownText) {
  if (!_hasDom) return null;
  if (!isAutoOpenEnabled()) return null;
  const arts = extractArtifacts(markdownText);
  if (!arts.length) return null;
  return open(arts[arts.length - 1], { autoOpened: true });
}

export function close() {
  if (!_hasDom) return;
  const pane = _pane || document.getElementById(PANE_ID);
  _pane = null;
  document.body.classList.remove('artifact-view');
  if (!pane) return;
  const frame = pane.querySelector('iframe.artifact-frame');
  if (frame) { try { frame.removeAttribute('srcdoc'); } catch (_) {} }
  const reduce = (() => {
    try { return window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches; }
    catch (_) { return false; }
  })();
  if (reduce) { pane.remove(); return; }
  pane.classList.add('artifact-pane-leaving');
  let done = false;
  const finish = () => { if (done) return; done = true; if (pane.isConnected) pane.remove(); };
  pane.addEventListener('animationend', finish, { once: true });
  setTimeout(finish, 240);
}

export function isOpen() {
  if (!_hasDom) return false;
  return !!(_pane && _pane.isConnected && !_pane.classList.contains('artifact-pane-leaving'));
}

/** Idempotent; the pane itself is built lazily on first open(). */
export function init() {
  if (_inited || !_hasDom) return;
  _inited = true;
}

/** Test/debug helper. */
export function _artifactList() {
  return _items.map(a => ({ id: a.id, lang: a.lang, title: a.title }));
}

const artifactsModule = {
  extractArtifacts,
  wrapForPreview,
  wrapForNewTab,
  artifactFromCode,
  detectTitle,
  isAutoOpenEnabled,
  open,
  openFromCode,
  autoOpenFromMessage,
  openInDocuments,
  openInNewTab,
  close,
  isOpen,
  init,
};

if (_hasDom) {
  init();
  window.artifactsModule = artifactsModule;
}

export default artifactsModule;

// ▣ Preview button on html / svg code blocks (emitted by markdown.js).
// Lives here rather than in chatRenderer.js so the renderer stays free of an
// artifact dependency. If the block was edited in place (✎) the <code> text
// is the source of truth — the edit handler only re-syncs run/copy buttons.
if (_hasDom) {
  document.addEventListener('click', function(e) {
    const btn = e.target && e.target.closest && e.target.closest('.preview-artifact');
    if (!btn) return;
    e.preventDefault();
    e.stopPropagation();
    const lang = (btn.getAttribute('data-lang') || 'html').toLowerCase();
    let code = btn.getAttribute('data-code') || '';
    const codeEl = btn.closest('pre')?.querySelector('code');
    if (codeEl) {
      const live = codeEl.textContent || '';
      if (live.trim() && live.trim() !== code.trim()) code = live;
    }
    if (code.trim()) openFromCode(code, lang, { autoOpened: false });
  });
}
