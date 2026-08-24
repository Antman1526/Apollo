// static/js/ecosystemHub.js
//
// Settings → AI → Ecosystem: one-click community skill packs and MCP
// presets, a persona importer, and a lean config security scan. Kept out of
// settings.js / settingsAiExtras.js for the module-size ratchet
// (scripts/check_module_sizes.py) — this is a fresh module with its own
// 1500-line budget, same pattern as tasksAssign.js.

function _esc(s) {
  return String(s == null ? '' : s).replaceAll('&', '&amp;').replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;').replaceAll('"', '&quot;');
}

const SEV_COLOR = { high: '#c0453c', medium: '#c8912f', info: 'var(--fg)' };

export function initEcosystemHub(el) {
  _initCatalog(el);
  _initPersonas(el);
  _initScan(el);
  _initReference(el);
}

/* ── Catalog: skill packs + MCP presets ── */
function _initCatalog(el) {
  const packsEl = el('eco-catalog-packs');
  const mcpEl = el('eco-catalog-mcp');
  const msg = el('eco-catalog-msg');
  if (!packsEl || !mcpEl) return;

  fetch('/api/hub/catalog', { credentials: 'same-origin' })
    .then(r => r.json())
    .then(data => {
      packsEl.innerHTML = (data.skill_packs || []).map(p => `
        <div style="display:flex;gap:8px;align-items:center;font-size:12px;">
          <div style="flex:1;">
            <div style="font-weight:600;">${_esc(p.name)}</div>
            <div style="opacity:0.65;font-size:11px;">${_esc(p.description)}</div>
          </div>
          <button class="admin-btn-sm" data-pack-source="${_esc(p.source)}" data-pack-name="${_esc(p.name)}">Install</button>
        </div>`).join('');
      mcpEl.innerHTML = (data.mcp_servers || []).map(s => `
        <div style="display:flex;gap:8px;align-items:center;font-size:12px;">
          <div style="flex:1;">
            <div style="font-weight:600;">${_esc(s.name)}</div>
            <div style="opacity:0.65;font-size:11px;">${_esc(s.description)}</div>
          </div>
          <button class="admin-btn-sm" data-mcp-id="${_esc(s.id)}">Add</button>
        </div>`).join('');

      packsEl.querySelectorAll('[data-pack-source]').forEach(btn => {
        btn.addEventListener('click', () => _installPack(btn, msg));
      });
      mcpEl.querySelectorAll('[data-mcp-id]').forEach(btn => {
        const preset = (data.mcp_servers || []).find(s => s.id === btn.dataset.mcpId);
        btn.addEventListener('click', () => _addMcpPreset(preset, btn, msg));
      });
    })
    .catch(() => { if (msg) msg.textContent = 'Failed to load catalog.'; });
}

function _installPack(btn, msg) {
  const source = btn.dataset.packSource;
  btn.disabled = true;
  if (msg) msg.textContent = `Fetching ${btn.dataset.packName}…`;
  fetch('/api/skills/packs/preview', {
    method: 'POST', credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ source })
  }).then(r => r.json().then(d => { if (!r.ok) throw new Error(d.detail || 'preview failed'); return d; }))
    .then(preview => {
      const names = (preview.skills || preview.found || []).map(s => s.name).filter(Boolean);
      if (!names.length) throw new Error('no skills found in that repo');
      return fetch('/api/skills/packs/install', {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ source, names, category: 'imported' })
      }).then(r => r.json().then(d => { if (!r.ok) throw new Error(d.detail || 'install failed'); return d; }));
    })
    .then(() => { if (msg) msg.textContent = `Installed ${btn.dataset.packName}. Review drafts in Skills.`; })
    .catch(e => { if (msg) msg.textContent = `Failed: ${e.message}`; })
    .finally(() => { btn.disabled = false; });
}

function _addMcpPreset(preset, btn, msg) {
  if (!preset) return;
  let envValues = {};
  for (const key of Object.keys(preset.env || {})) {
    const v = window.prompt(`${preset.name} needs ${key}:`, '');
    if (v) envValues[key] = v;
  }
  btn.disabled = true;
  if (msg) msg.textContent = `Adding ${preset.name}…`;
  const form = new FormData();
  form.set('name', preset.name);
  form.set('transport', 'stdio');
  form.set('command', preset.command);
  form.set('args', JSON.stringify(preset.args || []));
  form.set('env', JSON.stringify(envValues));
  fetch('/api/mcp/servers', { method: 'POST', credentials: 'same-origin', body: form })
    .then(r => r.json().then(d => { if (!r.ok) throw new Error(d.detail || 'add failed'); return d; }))
    .then(() => { if (msg) msg.textContent = `${preset.name} added — see MCP Servers.`; })
    .catch(e => { if (msg) msg.textContent = `Failed: ${e.message}`; })
    .finally(() => { btn.disabled = false; });
}

/* ── Persona importer ── */
function _initPersonas(el) {
  const sourceEl = el('eco-persona-source');
  const previewBtn = el('eco-persona-preview-btn');
  const listEl = el('eco-persona-list');
  const installBtn = el('eco-persona-install-btn');
  const msg = el('eco-persona-msg');
  if (!sourceEl || !previewBtn || !listEl || !installBtn) return;

  previewBtn.addEventListener('click', () => {
    const source = sourceEl.value.trim();
    if (!source) return;
    previewBtn.disabled = true;
    if (msg) msg.textContent = 'Fetching personas…';
    fetch('/api/hub/personas/preview', {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ source })
    }).then(r => r.json().then(d => { if (!r.ok) throw new Error(d.detail || 'preview failed'); return d; }))
      .then(data => {
        const personas = data.personas || [];
        if (msg) msg.textContent = personas.length ? `${personas.length} persona(s) found.` : 'No personas found.';
        listEl.innerHTML = personas.map(p => `
          <label style="display:flex;gap:6px;align-items:flex-start;font-size:12px;">
            <input type="checkbox" value="${_esc(p.name)}" checked style="margin-top:3px;">
            <span><b>${_esc(p.name)}</b>${p.description ? ' — ' + _esc(p.description) : ''}</span>
          </label>`).join('');
        installBtn.style.display = personas.length ? '' : 'none';
      })
      .catch(e => { if (msg) msg.textContent = `Failed: ${e.message}`; })
      .finally(() => { previewBtn.disabled = false; });
  });

  installBtn.addEventListener('click', () => {
    const names = Array.from(listEl.querySelectorAll('input[type=checkbox]:checked')).map(c => c.value);
    if (!names.length) return;
    installBtn.disabled = true;
    if (msg) msg.textContent = 'Installing…';
    fetch('/api/hub/personas/install', {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ source: sourceEl.value.trim(), names })
    }).then(r => r.json().then(d => { if (!r.ok) throw new Error(d.detail || 'install failed'); return d; }))
      .then(d => { if (msg) msg.textContent = `Added ${d.added}, skipped ${d.skipped} (already installed).`; })
      .catch(e => { if (msg) msg.textContent = `Failed: ${e.message}`; })
      .finally(() => { installBtn.disabled = false; });
  });
}

/* ── Security scan ── */
function _initScan(el) {
  const btn = el('eco-scan-btn');
  const summary = el('eco-scan-summary');
  const findingsEl = el('eco-scan-findings');
  if (!btn || !findingsEl) return;

  btn.addEventListener('click', () => {
    btn.disabled = true;
    if (summary) summary.textContent = 'Scanning…';
    fetch('/api/hub/security-scan', { credentials: 'same-origin' })
      .then(r => r.json())
      .then(data => {
        const s = data.summary || {};
        if (summary) {
          summary.textContent = (s.high || 0) + (s.medium || 0) + (s.info || 0) === 0
            ? 'No findings.'
            : `${s.high || 0} high, ${s.medium || 0} medium, ${s.info || 0} info`;
        }
        findingsEl.innerHTML = (data.findings || []).map(f => `
          <div style="display:flex;gap:8px;align-items:flex-start;font-size:12px;padding:8px 10px;border-radius:8px;background:color-mix(in srgb, var(--fg) 5%, transparent);">
            <span style="font-weight:700;text-transform:uppercase;font-size:10px;color:${SEV_COLOR[f.severity] || 'var(--fg)'};min-width:44px;">${_esc(f.severity)}</span>
            <span><b>${_esc(f.target)}</b> — ${_esc(f.message)}</span>
          </div>`).join('');
      })
      .catch(() => { if (summary) summary.textContent = 'Scan failed.'; })
      .finally(() => { btn.disabled = false; });
  });
}

/* ── Reference Library: installable catalogs + a live search box ── */
function _initReference(el) {
  const listEl = el('eco-ref-sources');
  const msg = el('eco-ref-msg');
  if (!listEl) return;

  function render(sources) {
    listEl.innerHTML = sources.map(s => {
      const installed = s.installed > 0;
      const badge = installed
        ? `<span style="font-size:10px;opacity:0.65;">${s.installed} entries</span>`
        : '';
      const agentTag = s.agent_actionable
        ? '<span style="font-size:9px;padding:1px 5px;border-radius:999px;background:color-mix(in srgb,var(--red) 18%,transparent);color:var(--red);">agent-usable</span>'
        : '';
      return `<div style="display:flex;gap:8px;align-items:flex-start;font-size:11px;">
        <div style="flex:1;">
          <div style="display:flex;gap:6px;align-items:center;">
            <b>${_esc(s.name)}</b> ${agentTag} ${badge}
          </div>
          <div style="opacity:0.65;">${_esc(s.description)}</div>
          <div style="opacity:0.45;font-size:10px;">${_esc(s.repo)} · ${_esc(s.license)}</div>
        </div>
        <button class="admin-btn-sm" data-ref-install="${_esc(s.id)}">${installed ? 'Update' : 'Install'}</button>
        ${installed ? `<button class="admin-btn-sm" data-ref-remove="${_esc(s.id)}">Remove</button>` : ''}
      </div>`;
    }).join('');

    listEl.querySelectorAll('[data-ref-install]').forEach(btn => {
      btn.addEventListener('click', () => _refAction('install', btn.dataset.refInstall, btn, msg, el));
    });
    listEl.querySelectorAll('[data-ref-remove]').forEach(btn => {
      btn.addEventListener('click', () => _refAction('remove', btn.dataset.refRemove, btn, msg, el));
    });
  }

  function load() {
    fetch('/api/hub/reference/sources', { credentials: 'same-origin' })
      .then(r => r.json())
      .then(d => render(d.sources || []))
      .catch(() => { if (msg) msg.textContent = 'Could not load reference sources.'; });
  }
  _initReference._reload = load;
  load();

  const searchBtn = el('eco-ref-search-btn');
  const queryEl = el('eco-ref-query');
  const resultsEl = el('eco-ref-results');
  if (searchBtn && queryEl && resultsEl) {
    const run = () => {
      const q = queryEl.value.trim();
      if (!q) return;
      searchBtn.disabled = true;
      fetch('/api/hub/reference/search?q=' + encodeURIComponent(q) + '&limit=15',
            { credentials: 'same-origin' })
        .then(r => r.json())
        .then(d => {
          const rows = d.results || [];
          if (msg) msg.textContent = rows.length ? '' : 'No matches — install a catalog above first.';
          resultsEl.innerHTML = rows.map(r => {
            const auth = r.meta && r.meta.auth ? ` · auth: ${_esc(r.meta.auth)}` : '';
            return `<div style="font-size:11px;">
              <a href="${_esc(r.url)}" target="_blank" rel="noopener noreferrer">${_esc(r.title)}</a>
              <span style="opacity:0.5;font-size:10px;"> [${_esc(r.category || r.kind)}]${auth}</span>
              ${r.description ? `<div style="opacity:0.6;font-size:10px;">${_esc(r.description.slice(0, 120))}</div>` : ''}
            </div>`;
          }).join('');
        })
        .catch(e => { if (msg) msg.textContent = 'Search failed: ' + e.message; })
        .finally(() => { searchBtn.disabled = false; });
    };
    searchBtn.addEventListener('click', run);
    queryEl.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); run(); } });
  }
}

function _refAction(action, source, btn, msg, el) {
  btn.disabled = true;
  if (msg) msg.textContent = action === 'install' ? 'Fetching catalog…' : 'Removing…';
  fetch('/api/hub/reference/' + action, {
    method: 'POST', credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ source })
  }).then(r => r.json().then(d => { if (!r.ok) throw new Error(d.detail || 'failed'); return d; }))
    .then(d => {
      if (msg) msg.textContent = action === 'install'
        ? `Indexed ${d.installed} entries from ${source}.`
        : `Removed ${d.removed} entries.`;
      if (_initReference._reload) _initReference._reload();
    })
    .catch(e => { if (msg) msg.textContent = 'Failed: ' + e.message; })
    .finally(() => { btn.disabled = false; });
}
