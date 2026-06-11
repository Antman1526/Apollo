export function wireSystemStatusActions(root, options = {}) {
  const fetchImpl = options.fetchImpl || globalThis.fetch;
  const confirmImpl = options.confirmImpl || globalThis.styledConfirm || globalThis.confirm;
  const alertImpl = options.alertImpl || globalThis.alert;
  const showToast = options.showToast || globalThis.uiModule?.showToast;
  const showError = options.showError || globalThis.uiModule?.showError;
  const rerender = options.rerender || (async () => {});

  root.querySelectorAll('.system-status-action').forEach(btn => {
    btn.addEventListener('click', async (e) => {
      e.stopPropagation();
      const endpoint = btn.dataset.systemActionEndpoint;
      const method = btn.dataset.systemActionMethod || 'POST';
      const confirmText = btn.dataset.systemActionConfirm || '';
      if (!endpoint) return;
      if (confirmText) {
        const ok = await confirmImpl?.(confirmText, { confirmText: btn.textContent || 'Run' });
        if (!ok) return;
      }
      const oldText = btn.textContent;
      btn.disabled = true;
      btn.textContent = 'Working...';
      try {
        const res = await fetchImpl(endpoint, { method, credentials: 'same-origin' });
        if (!res.ok) {
          let msg = `Action failed (${res.status})`;
          try {
            const data = await res.json();
            msg = data?.detail || data?.error || msg;
          } catch (_) {}
          throw new Error(msg);
        }
        showToast?.('System action complete');
        await rerender();
      } catch (err) {
        btn.disabled = false;
        btn.textContent = oldText;
        if (showError) showError(err?.message || 'System action failed');
        else alertImpl?.(err?.message || 'System action failed');
      }
    });
  });
}
