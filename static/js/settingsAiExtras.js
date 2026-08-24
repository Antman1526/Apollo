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
