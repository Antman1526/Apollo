// Paperclip integration UI: reveals the sidebar tool + iframe modal only when
// the bundled Paperclip sidecar is enabled, and fills the Settings subsection.
// Self-contained, dependency-free. The iframe loads same-origin /paperclip/,
// which Apollo reverse-proxies (behind auth) to the Paperclip server.

const FRAME_SRC = '/paperclip/';

function $(id) { return document.getElementById(id); }

function openModal() {
  const modal = $('paperclip-modal');
  if (!modal) return;
  const frame = $('paperclip-frame');
  // Lazy-load the iframe on first open so a disabled/slow sidecar never blocks
  // initial page load.
  if (frame && !frame.getAttribute('src')) frame.setAttribute('src', FRAME_SRC);
  modal.classList.remove('hidden');
}

function closeModal() {
  const modal = $('paperclip-modal');
  if (modal) modal.classList.add('hidden');
}

function applyStatus(status) {
  const enabled = !!(status && status.enabled);

  // Sidebar tool button — hidden unless the sidecar is enabled.
  const btn = $('tool-paperclip-btn');
  if (btn) btn.style.display = enabled ? '' : 'none';

  // Settings subsection.
  const section = $('set-paperclip-section');
  const stateEl = $('set-paperclipState');
  const endpointEl = $('set-paperclipEndpoint');
  if (stateEl) stateEl.textContent = enabled ? 'Enabled' : 'Disabled';
  if (endpointEl && status) {
    endpointEl.textContent = status.model_endpoint
      ? `Model endpoint: ${status.model_endpoint}` : '';
  }
  const openBtn = $('set-paperclipOpen');
  if (openBtn) openBtn.disabled = !enabled;
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
