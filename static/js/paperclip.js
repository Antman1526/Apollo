// Paperclip integration UI: reveals the sidebar tool + iframe modal only when
// the bundled Paperclip sidecar is enabled, and fills the Settings subsection.
// Self-contained, dependency-free. The iframe loads Paperclip's OWN origin
// (browser_url from /api/paperclip/status) — Paperclip's UI + /api are wired to
// root paths, so it can't be embedded under an Apollo subpath. Paperclip brings
// its own auth.

let _frameSrc = '';

function $(id) { return document.getElementById(id); }

function openModal() {
  const modal = $('paperclip-modal');
  if (!modal || !_frameSrc) return;
  const frame = $('paperclip-frame');
  // Lazy-load the iframe on first open so a disabled/slow sidecar never blocks
  // initial page load.
  if (frame && !frame.getAttribute('src')) frame.setAttribute('src', _frameSrc);
  modal.classList.remove('hidden');
}

function closeModal() {
  const modal = $('paperclip-modal');
  if (modal) modal.classList.add('hidden');
}

function applyStatus(status) {
  const enabled = !!(status && status.enabled);
  _frameSrc = (status && status.browser_url) ? status.browser_url : '';

  // Sidebar tool button — hidden unless the sidecar is enabled.
  const btn = $('tool-paperclip-btn');
  if (btn) btn.style.display = enabled ? '' : 'none';

  // Settings subsection.
  const section = $('set-paperclip-section');
  const stateEl = $('set-paperclipState');
  const endpointEl = $('set-paperclipEndpoint');
  if (stateEl) {
    let label = enabled ? 'Enabled' : 'Disabled';
    if (enabled && status.reachable === false) label = 'Enabled (not reachable)';
    stateEl.textContent = label;
  }
  if (endpointEl && status) {
    const bits = [];
    if (status.model_endpoint) bits.push(`model: ${status.model_endpoint}`);
    if (status.browser_url) bits.push(status.browser_url);
    endpointEl.textContent = bits.join(' · ');
  }
  const openBtn = $('set-paperclipOpen');
  if (openBtn) openBtn.disabled = !enabled || !_frameSrc;
  if (section) section.dataset.enabled = String(enabled);
}

async function refreshStatus() {
  try {
    const res = await fetch('/api/paperclip/status', { credentials: 'same-origin' });
    if (!res.ok) { applyStatus({ enabled: false }); return; }
    applyStatus(await res.json());
  } catch (_e) {
    applyStatus({ enabled: false });
  }
}

function init() {
  const btn = $('tool-paperclip-btn');
  if (btn) btn.addEventListener('click', openModal);

  const closeBtn = $('close-paperclip-modal');
  if (closeBtn) closeBtn.addEventListener('click', closeModal);

  const openBtn = $('set-paperclipOpen');
  if (openBtn) openBtn.addEventListener('click', openModal);

  refreshStatus();
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}

export { refreshStatus };
