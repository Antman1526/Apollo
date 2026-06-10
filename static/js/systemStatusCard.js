export function escapeStatusHTML(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

export function renderStatePillHTML(state) {
  const label = String(state || 'unknown');
  const color = label === 'ready'
    ? 'var(--green,#50fa7b)'
    : label === 'needs_setup' || label === 'blocked' || label === 'error'
      ? 'var(--accent,var(--red))'
      : 'var(--orange,#ffb86c)';
  return `<span style="font-size:9px;text-transform:uppercase;letter-spacing:0.5px;padding:1px 5px;border:1px solid color-mix(in srgb, ${color} 45%, transparent);border-radius:3px;color:${color};background:color-mix(in srgb, ${color} 10%, transparent);">${escapeStatusHTML(label.replace('_', ' '))}</span>`;
}

export function renderSystemStatusCardHTML(systemStatus, options = {}) {
  if (!systemStatus || !systemStatus.components) return '';
  const esc = options.escapeHTML || escapeStatusHTML;
  const renderStatePill = options.renderStatePill || renderStatePillHTML;
  const components = systemStatus.components || {};
  const order = [
    ['storage', 'Storage'],
    ['auth', 'Auth'],
    ['memory', 'Memory'],
    ['email', 'Email'],
    ['documents', 'Documents'],
    ['models', 'Models'],
    ['search', 'Search'],
    ['tool_servers', 'Tool Servers'],
    ['terminal', 'Terminal'],
    ['background', 'Background'],
  ];
  const nextSteps = order
    .map(([key, label]) => {
      const item = components[key] || {};
      if (item.ready || !item.next_step) return '';
      return `<div style="font-size:11px;opacity:0.65;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(label)}: ${esc(item.next_step)}</div>`;
    })
    .filter(Boolean)
    .join('');
  return `
      <div class="intg-card system-status-card" data-intg-type="system-status" style="padding:10px;border:1px solid var(--border);border-radius:6px;margin-bottom:10px;">
        <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px;">
          <span style="opacity:0.7"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 7h-9"/><path d="M14 17H5"/><circle cx="17" cy="17" r="3"/><circle cx="7" cy="7" r="3"/></svg></span>
          <div style="flex:1;min-width:0">
            <div style="font-size:12px;font-weight:700;display:flex;align-items:center;gap:6px">System Status ${renderStatePill(systemStatus.ok ? 'ready' : 'degraded')}</div>
            <div style="font-size:11px;opacity:0.55">${Number(systemStatus.ready_count || 0)}/${Number(systemStatus.total || 0)} systems ready</div>
          </div>
        </div>
        <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:6px;margin-bottom:${nextSteps ? '8px' : '0'};">
          ${order.map(([key, label]) => {
            const item = components[key] || {};
            return `<div style="border:1px solid color-mix(in srgb, var(--border) 70%, transparent);border-radius:5px;padding:6px;min-width:0;" title="${esc(item.summary || '')}">
              <div style="font-size:11px;font-weight:650;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${esc(label)}</div>
              <div style="margin-top:4px">${renderStatePill(item.state || 'unknown')}</div>
            </div>`;
          }).join('')}
        </div>
        ${nextSteps ? `<div style="display:grid;gap:3px">${nextSteps}</div>` : ''}
      </div>
    `;
}
