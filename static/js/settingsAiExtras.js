// static/js/settingsAiExtras.js
//
// Settings → AI additions kept out of settings.js for the module-size
// ratchet (scripts/check_module_sizes.py): the llama-server binary field on
// the Local Models card, and the Fast Lane (mixture routing) model role.
// settings.js-private helpers are injected via a deps object.

function _renderLlamaBinary(el, data) {
  var input = el('set-localModelBinInput');
  var msg = el('set-localModelBinMsg');
  if (input) input.value = data.path || '';
  if (!msg) return;
  if (data.resolved) {
    msg.textContent = 'Using: ' + data.resolved;
    msg.style.color = '';
  } else if (data.path) {
    msg.textContent = 'Configured path not found: ' + data.path;
    msg.style.color = '#c0392b';
  } else {
    msg.textContent = 'llama-server not found — install llama.cpp or set the path above.';
    msg.style.color = '#c0392b';
  }
}

export function refreshLlamaBinary(el) {
  fetch('/api/local-models/binary', { credentials: 'same-origin' })
    .then(function(r) { return r.json(); })
    .then(function(d) { _renderLlamaBinary(el, d); })
    .catch(function() { /* section already surfaces load errors */ });
}

export function wireLlamaBinaryField(el) {
  var binSave = el('set-localModelBinSave');
  var binInput = el('set-localModelBinInput');
  if (!binSave || !binInput) return;
  binSave.addEventListener('click', function() {
    fetch('/api/local-models/binary', {
      method: 'PUT',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: binInput.value.trim() })
    }).then(function(r) { return r.json(); })
      .then(function(d) { _renderLlamaBinary(el, d); })
      .catch(function(e) {
        var msg = el('set-localModelBinMsg');
        if (msg) { msg.textContent = 'Failed to save: ' + e.message; msg.style.color = '#c0392b'; }
      });
  });
  binInput.addEventListener('keydown', function(e) {
    if (e.key === 'Enter') { e.preventDefault(); binSave.click(); }
  });
}

/* ── Fast Lane (mixture routing) ── */
// Small model that answers short conversational chat messages; the session
// model stays first fallback. Backend: services/model_router.py.
export async function initLightModel(deps) {
  var el = deps.el;
  var toggle = el('set-mixtureRoutingToggle');
  var epSel = el('set-lightEpSelect');
  var modelSel = el('set-lightModelSelect');
  var msg = el('set-lightMsg');
  if (!epSel || !modelSel) return;
  var _endpoints = [];

  try {
    _endpoints = await deps.fetchModelEndpoints();
    deps.fillEndpointSelect(epSel, _endpoints, epSel.value, true);
  } catch (e) { console.warn('Failed to load endpoints for fast lane', e); }

  function refreshModels(selectedModel) {
    var epId = epSel.value;
    var ep = _endpoints.find(function(e) { return e.id === epId; });
    deps.fillModelSelect(modelSel, ep ? ep.models : [], selectedModel, true,
      { chatOnly: true, modelMeta: ep && ep.model_meta });
  }

  try {
    var res = await fetch('/api/auth/settings', { credentials: 'same-origin' });
    var settings = await res.json();
    if (toggle) toggle.checked = !!settings.mixture_routing_enabled;
    if (settings.light_endpoint_id) epSel.value = settings.light_endpoint_id;
    refreshModels(settings.light_model || '');
  } catch (e) { console.warn('Failed to load fast lane settings', e); }

  async function saveLight() {
    try {
      await fetch('/api/auth/settings', { method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          mixture_routing_enabled: toggle ? toggle.checked : false,
          light_endpoint_id: epSel.value || '',
          light_model: modelSel.value || ''
        })
      });
      msg.textContent = 'Saved'; msg.style.color = 'var(--fg)';
      setTimeout(function() { msg.textContent = ''; }, 1500);
    } catch (e) { msg.textContent = 'Failed to save'; msg.style.color = 'var(--red)'; }
  }

  if (toggle) toggle.addEventListener('change', saveLight);
  epSel.addEventListener('change', function() { refreshModels(''); saveLight(); });
  modelSel.addEventListener('change', saveLight);

  deps.registerAiEndpointRefresh(function(endpoints) {
    _endpoints = endpoints;
    deps.fillEndpointSelect(epSel, _endpoints, epSel.value, true);
    refreshModels(modelSel.value);
  });
}

/* ── Model Hub: free cloud models, Codex Router, HF GGUF pulls ── */
export function initModelHub(el) {
  var provSel = el('set-hubProvider');
  var keyInput = el('set-hubApiKey');
  var addBtn = el('set-hubAddFree');
  var freeMsg = el('set-hubFreeMsg');
  if (addBtn && provSel && keyInput) {
    addBtn.addEventListener('click', function() {
      var key = keyInput.value.trim();
      if (!key) { if (freeMsg) freeMsg.textContent = 'Enter the provider API key (free models still need one).'; return; }
      addBtn.disabled = true;
      if (freeMsg) freeMsg.textContent = 'Fetching free model list…';
      fetch('/api/hub/free-endpoint', {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ provider: provSel.value, api_key: key })
      }).then(function(r) { return r.json().then(function(d) { if (!r.ok) throw new Error(d.detail || d.error || ('HTTP ' + r.status)); return d; }); })
        .then(function(d) {
          keyInput.value = '';
          if (freeMsg) freeMsg.textContent = '"' + d.name + '" added with ' + d.free_models + ' free models — they are in the model picker now.';
        })
        .catch(function(e) { if (freeMsg) freeMsg.textContent = 'Failed: ' + e.message; })
        .finally(function() { addBtn.disabled = false; });
    });
  }

  var codexStatus = el('set-hubCodexStatus');
  var codexInstall = el('set-hubCodexInstall');
  var codexNote = el('set-hubCodexNote');
  fetch('/api/hub/codex-router', { credentials: 'same-origin' })
    .then(function(r) { return r.json(); })
    .then(function(d) {
      if (codexStatus) {
        codexStatus.textContent = d.running
          ? 'Running on 127.0.0.1:' + d.port + ' — its models appear inside the Codex CLI.'
          : 'Not detected. Install it with:';
        codexStatus.style.color = d.running ? 'var(--fg)' : '';
      }
      if (!d.running && codexInstall) {
        codexInstall.textContent = d.install_commands;
        codexInstall.style.display = 'block';
      }
      if (codexNote) codexNote.textContent = d.note || '';
    })
    .catch(function() { if (codexStatus) codexStatus.textContent = 'Status unavailable.'; });

  _initGgufPull(el);
}

function _esc(s) {
  return String(s == null ? '' : s).replaceAll('&', '&amp;').replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;').replaceAll('"', '&quot;');
}

function _gb(bytes) { return bytes ? (bytes / 1e9).toFixed(1) + ' GB' : '?'; }

function _initGgufPull(el) {
  var query = el('set-hubGgufQuery');
  var searchBtn = el('set-hubGgufSearch');
  var results = el('set-hubGgufResults');
  var dls = el('set-hubGgufDownloads');
  var msg = el('set-hubGgufMsg');
  if (!query || !searchBtn || !results) return;
  var pollTimer = null;

  function doSearch() {
    var q = query.value.trim();
    if (!q) return;
    searchBtn.disabled = true;
    if (msg) msg.textContent = 'Searching Hugging Face…';
    fetch('/api/hub/gguf-search?q=' + encodeURIComponent(q), { credentials: 'same-origin' })
      .then(function(r) { return r.json(); })
      .then(function(d) {
        var repos = d.repos || [];
        if (msg) msg.textContent = repos.length ? '' : 'No GGUF repos found.';
        results.innerHTML = repos.map(function(rp) {
          return '<div style="display:flex;gap:6px;align-items:center;font-size:11px;">' +
            '<span style="flex:1;overflow:hidden;text-overflow:ellipsis;">' + _esc(rp.repo_id) + '</span>' +
            '<span style="opacity:0.5;font-size:10px;">' + (rp.downloads || 0) + ' dl</span>' +
            '<button class="admin-btn-sm" data-hub-repo="' + _esc(rp.repo_id) + '">Files</button></div>';
        }).join('');
        results.querySelectorAll('[data-hub-repo]').forEach(function(btn) {
          btn.addEventListener('click', function() { showFiles(btn.dataset.hubRepo); });
        });
      })
      .catch(function() { if (msg) msg.textContent = 'Search failed.'; })
      .finally(function() { searchBtn.disabled = false; });
  }

  function showFiles(repo) {
    if (msg) msg.textContent = 'Listing files in ' + repo + '…';
    fetch('/api/hub/gguf-files?repo=' + encodeURIComponent(repo), { credentials: 'same-origin' })
      .then(function(r) { return r.json(); })
      .then(function(d) {
        var files = d.files || [];
        if (msg) msg.textContent = files.length ? 'Pick a quantization to download:' : 'No .gguf files in that repo.';
        results.innerHTML = files.map(function(f) {
          return '<div style="display:flex;gap:6px;align-items:center;font-size:11px;">' +
            '<span style="flex:1;overflow:hidden;text-overflow:ellipsis;">' + _esc(f.path) + '</span>' +
            '<span style="opacity:0.5;font-size:10px;">' + _gb(f.size_bytes) + '</span>' +
            '<button class="admin-btn-sm" data-hub-file="' + _esc(f.path) + '" data-hub-frepo="' + _esc(repo) + '">Download</button></div>';
        }).join('');
        results.querySelectorAll('[data-hub-file]').forEach(function(btn) {
          btn.addEventListener('click', function() { startDownload(btn.dataset.hubFrepo, btn.dataset.hubFile); });
        });
      })
      .catch(function() { if (msg) msg.textContent = 'Could not list files.'; });
  }

  function startDownload(repo, file) {
    if (msg) msg.textContent = 'Starting download…';
    fetch('/api/hub/gguf-download', {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ repo_id: repo, file: file })
    }).then(function(r) { return r.json().then(function(d) { if (!r.ok) throw new Error(d.detail || 'refused'); return d; }); })
      .then(function() { if (msg) msg.textContent = ''; pollDownloads(); })
      .catch(function(e) { if (msg) msg.textContent = 'Download failed to start: ' + e.message; });
  }

  function pollDownloads() {
    fetch('/api/hub/gguf-downloads', { credentials: 'same-origin' })
      .then(function(r) { return r.json(); })
      .then(function(d) {
        var items = d.downloads || [];
        if (dls) {
          dls.innerHTML = items.map(function(it) {
            var pct = it.total_bytes ? Math.round(100 * it.done_bytes / it.total_bytes) : null;
            var state = it.status === 'downloading'
              ? (pct != null ? pct + '%' : _gb(it.done_bytes))
              : (it.status === 'done' ? 'done — model appears after rescan' : 'error: ' + _esc(it.error || ''));
            return '<div style="font-size:10px;opacity:0.8;">' + _esc(it.file) + ' — ' + state + '</div>';
          }).join('');
        }
        var active = items.some(function(it) { return it.status === 'downloading'; });
        clearTimeout(pollTimer);
        if (active) pollTimer = setTimeout(pollDownloads, 2000);
      })
      .catch(function() {});
  }

  searchBtn.addEventListener('click', doSearch);
  query.addEventListener('keydown', function(e) { if (e.key === 'Enter') { e.preventDefault(); doSearch(); } });
  pollDownloads();
}
