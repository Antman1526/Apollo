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
  _refreshLocalSelects(el);
}

// Launch settings for local llama.cpp models: the context window (Apollo
// budgets prompts against it, capped at each model's own limit) and the KV
// cache precision (8-bit halves the memory a window takes). Each is a select
// backed by GET/PUT /api/local-models/<path>; saving restarts the running
// chat model so it applies to the next message.
var _LOCAL_SELECTS = [
  { id: 'set-localModelContext', msg: 'set-localModelContextMsg', path: '/api/local-models/context', key: 'context', number: true },
  { id: 'set-localModelKvCache', msg: 'set-localModelKvCacheMsg', path: '/api/local-models/kv-cache', key: 'kv_cache', number: false },
  { id: 'set-localModelReasoning', msg: 'set-localModelReasoningMsg', path: '/api/local-models/reasoning', key: 'value', number: true },
  { id: 'set-localModelIdle', msg: 'set-localModelIdleMsg', path: '/api/local-models/idle', key: 'value', number: true },
  // Helper: the server also returns `options` (local chat models) and what
  // automatic currently picks, shown on the "Automatic" entry.
  { id: 'set-localModelHelper', msg: 'set-localModelHelperMsg', path: '/api/local-models/helper', key: 'helper_model', number: false }
];

function _refreshLocalSelects(el) {
  _LOCAL_SELECTS.forEach(function(spec) {
    var sel = el(spec.id);
    if (!sel) return;
    fetch(spec.path, { credentials: 'same-origin' })
      .then(function(r) { return r.json(); })
      .then(function(d) {
        var v = String(d[spec.key]);
        if (d.options) {
          while (sel.options.length > 1) sel.remove(1);
          d.options.forEach(function(name) {
            var o = document.createElement('option');
            o.value = name; o.textContent = name;
            sel.appendChild(o);
          });
          if (sel.options[0]) sel.options[0].textContent = 'Automatic' + (d.auto_pick ? ' — ' + d.auto_pick : ' — none suitable');
        }
        var known = Array.prototype.some.call(sel.options, function(o) { return o.value === v; });
        if (!known) {
          var opt = document.createElement('option');
          opt.value = v;
          opt.textContent = (spec.number ? Number(v).toLocaleString() + ' tokens' : v) + ' (custom)';
          sel.appendChild(opt);
        }
        sel.value = v;
      })
      .catch(function() { /* section already surfaces load errors */ });
  });
}

function _wireLocalSelects(el) {
  _LOCAL_SELECTS.forEach(function(spec) {
    var sel = el(spec.id);
    var msg = el(spec.msg);
    if (!sel) return;
    sel.addEventListener('change', function() {
      var body = {};
      body[spec.key] = spec.number ? parseInt(sel.value, 10) : sel.value;
      fetch(spec.path, {
        method: 'PUT',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
      }).then(function(r) { return r.json(); })
        .then(function(d) {
          if (!msg) return;
          if (d.ok === false) { msg.textContent = d.error || 'Could not save.'; msg.style.color = '#c0392b'; return; }
          msg.style.color = '';
          msg.textContent = 'Saved. ' + ((d.restarted && d.restarted.length)
            ? d.restarted.join(', ') + ' will reload with the new setting on your next message.'
            : 'Applies the next time a model loads.');
        })
        .catch(function(e) {
          if (msg) { msg.textContent = 'Failed to save: ' + e.message; msg.style.color = '#c0392b'; }
        });
    });
  });
}

export function wireLlamaBinaryField(el) {
  _wireLocalSelects(el);
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

// Module-scoped so settings.close() can stop the download poller — otherwise
// a started GGUF download kept polling /api/hub/gguf-downloads every 2s
// forever, even after the Settings modal was closed.
let _ggufPollTimer = null;
export function stopGgufPolling() {
  if (_ggufPollTimer) { clearTimeout(_ggufPollTimer); _ggufPollTimer = null; }
}

function _initGgufPull(el) {
  var query = el('set-hubGgufQuery');
  var searchBtn = el('set-hubGgufSearch');
  var results = el('set-hubGgufResults');
  var dls = el('set-hubGgufDownloads');
  var msg = el('set-hubGgufMsg');
  if (!query || !searchBtn || !results) return;

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
        stopGgufPolling();
        if (active) _ggufPollTimer = setTimeout(pollDownloads, 2000);
      })
      .catch(function() {});
  }

  searchBtn.addEventListener('click', doSearch);
  query.addEventListener('keydown', function(e) { if (e.key === 'Enter') { e.preventDefault(); doSearch(); } });
  pollDownloads();
}

function _dirStatusLabel(status) {
  if (!status) return null;
  if (status.state === 'unmounted') return { text: 'not mounted', color: 'var(--warning, #d29922)' };
  if (status.state === 'missing') return { text: 'does not exist', color: 'var(--danger, #f85149)' };
  return { text: status.models + ' model' + (status.models === 1 ? '' : 's'), color: '' };
}

/** Scan-directory rows for Settings → AI → Local Models, each with its
 * state from GET /api/local-models `dir_status` (N models / not mounted /
 * does not exist). `onRemove(newDirs)` persists a removal. */
export function renderLocalModelDirs(el, dirs, statuses, onRemove) {
  var container = el('set-localModelDirs');
  if (!container) return;
  container.innerHTML = '';
  if (!dirs || dirs.length === 0) {
    var empty = document.createElement('div');
    empty.style.cssText = 'font-size:11px;opacity:0.45;';
    empty.textContent = 'No directories configured.';
    container.appendChild(empty);
    return;
  }
  dirs.forEach(function(dir) {
    var row = document.createElement('div');
    row.style.cssText = 'display:flex;align-items:center;gap:6px;';
    var span = document.createElement('span');
    span.style.cssText = 'flex:1;font-size:12px;font-family:monospace;word-break:break-all;opacity:0.85;';
    span.textContent = dir;
    var label = _dirStatusLabel((statuses || []).filter(function(st) { return st.path === dir; })[0]);
    var tag = document.createElement('span');
    tag.style.cssText = 'flex-shrink:0;font-size:11px;opacity:0.7;' + (label && label.color ? 'color:' + label.color + ';' : '');
    tag.textContent = label ? label.text : '';
    var btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'admin-btn-sm';
    btn.textContent = 'Remove';
    btn.style.cssText = 'flex-shrink:0;';
    btn.addEventListener('click', function() {
      var newDirs = dirs.filter(function(d) { return d !== dir; });
      onRemove(newDirs);
    });
    row.appendChild(span);
    row.appendChild(tag);
    row.appendChild(btn);
    container.appendChild(row);
  });
}

/** Local model rows (Settings → AI → Local Models). A model the runtime
 * can't serve (video/audio/diffusion, or a type this mlx_lm doesn't know)
 * is greyed out with the reason instead of a Start button that would fail. */
export function renderLocalModelsList(el, models, refresh) {
  var container = el('set-localModelsList');
  if (!container) return;
  container.innerHTML = '';
  if (!models || models.length === 0) {
    var empty = document.createElement('div');
    empty.style.cssText = 'font-size:11px;opacity:0.45;';
    empty.textContent = 'No models found. Add a directory and click Rescan.';
    container.appendChild(empty);
    return;
  }
  models.forEach(function(m) {
    var row = document.createElement('div');
    row.style.cssText = 'display:flex;align-items:center;gap:8px;padding:5px 0;border-bottom:1px solid color-mix(in srgb,var(--fg) 8%,transparent);flex-wrap:wrap;';

    var info = document.createElement('div');
    info.style.cssText = 'flex:1;min-width:0;';

    var nameSpan = document.createElement('span');
    nameSpan.style.cssText = 'font-size:12px;font-weight:600;word-break:break-all;';
    nameSpan.textContent = m.name || m.id;

    var meta = document.createElement('span');
    meta.style.cssText = 'font-size:11px;opacity:0.55;margin-left:6px;';
    var parts = [];
    if (m.quant) parts.push(m.quant);
    if (m.kind && m.kind !== 'unsupported') parts.push(m.kind);
    if (m.size_bytes) parts.push(_gb(m.size_bytes));
    if (m.kind === 'unsupported') parts.push("can't be served" + (m.arch ? ' (' + m.arch + ')' : ''));
    meta.textContent = parts.join(' · ');

    info.appendChild(nameSpan);
    info.appendChild(meta);
    if (m.kind === 'unsupported') {
      row.style.opacity = '0.5';
      row.title = 'Not a chat or embedding model this runtime can load';
      row.appendChild(info);
      container.appendChild(row);
      return;
    }

    if (m.running) {
      var badge = document.createElement('span');
      badge.style.cssText = 'font-size:10px;padding:2px 6px;border-radius:10px;background:color-mix(in srgb,#27ae60 20%,transparent);color:#27ae60;font-weight:700;flex-shrink:0;';
      badge.textContent = 'Running';
      row.appendChild(info);
      row.appendChild(badge);
    } else {
      row.appendChild(info);
    }

    var actionBtn = document.createElement('button');
    actionBtn.type = 'button';
    actionBtn.className = 'admin-btn-sm';
    actionBtn.style.cssText = 'flex-shrink:0;';
    if (m.running) {
      actionBtn.textContent = 'Stop';
      actionBtn.addEventListener('click', function() {
        actionBtn.disabled = true;
        actionBtn.textContent = 'Stopping…';
        fetch('/api/local-models/' + encodeURIComponent(m.id) + '/stop', {
          method: 'POST',
          credentials: 'same-origin'
        }).then(function(r) { return r.json(); }).then(function() {
          refresh();
        }).catch(function(e) {
          var errEl = el('set-localModelsErr');
          if (errEl) errEl.textContent = 'Stop failed: ' + e.message;
          actionBtn.disabled = false;
          actionBtn.textContent = 'Stop';
        });
      });
    } else {
      actionBtn.textContent = 'Start';
      actionBtn.addEventListener('click', function() {
        actionBtn.disabled = true;
        actionBtn.textContent = 'Starting…';
        fetch('/api/local-models/' + encodeURIComponent(m.id) + '/start', {
          method: 'POST',
          credentials: 'same-origin'
        }).then(function(r) { return r.json(); }).then(function(data) {
          if (data && data.ok === false) {
            var errEl = el('set-localModelsErr');
            if (errEl) errEl.textContent = 'Start failed: ' + (data.error || 'unknown error');
            actionBtn.disabled = false;
            actionBtn.textContent = 'Start';
          } else {
            refresh();
          }
        }).catch(function(e) {
          var errEl = el('set-localModelsErr');
          if (errEl) errEl.textContent = 'Start failed: ' + e.message;
          actionBtn.disabled = false;
          actionBtn.textContent = 'Start';
        });
      });
    }
    row.appendChild(actionBtn);
    container.appendChild(row);
  });
}
