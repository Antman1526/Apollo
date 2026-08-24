// Activity ledger UI — the agent's "computer history".
// Modal timeline over /api/activity: what the agent ran, wrote, and fetched,
// with per-write undo. Admin-only (the ledger holds commands and file paths).
let _open = false;
let _search = '';
let _toolFilter = '';
let _sessionFilter = '';

function _esc(s) {
  return String(s == null ? '' : s)
    .replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;');
}

function _fmtTime(iso) {
  if (!iso) return '';
  try {
    const d = new Date(iso + (iso.endsWith('Z') ? '' : 'Z'));
    return d.toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
  } catch (e) { return iso; }
}

export function isActivityOpen() { return _open; }

export function closeActivity() {
  const modal = document.getElementById('activity-modal');
  if (modal) modal.remove();
  _open = false;
}

export function openActivity() {
  if (_open) { closeActivity(); return; }
  _open = true;

  const modal = document.createElement('div');
  modal.className = 'modal';
  modal.id = 'activity-modal';
  modal.innerHTML = `
    <div class="modal-content tasks-modal-content">
      <div class="modal-header">
        <h4 style="position:relative;top:-2px;"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px;margin-right:6px"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>Agent History</h4>
        <span style="flex:1"></span>
        <button class="close-btn" id="activity-close">✖</button>
      </div>
      <div class="modal-body" style="display:flex;flex-direction:column;gap:8px;overflow:hidden;">
        <div class="admin-card" style="flex:1;display:flex;flex-direction:column;overflow:hidden;">
          <p class="memory-desc">Everything the agent did on this machine — commands, file writes, web fetches — newest first. File writes can be undone.</p>
          <div style="display:flex;gap:8px;align-items:center;margin-bottom:8px;">
            <span style="font-size:11px;font-weight:600;opacity:0.7;">Autonomy</span>
            <select class="memory-sort-select" id="activity-autonomy" title="How freely the agent may act" style="width:100px;font-size:11px;height:24px;">
              <option value="auto">Auto</option>
              <option value="observe">Observe</option>
            </select>
            <span id="activity-autonomy-msg" style="font-size:10px;opacity:0.6;"></span>
          </div>
          <div id="activity-session-bar" style="display:none;gap:6px;align-items:center;margin-bottom:6px;font-size:11px;">
            <span>Session: <code id="activity-session-label"></code></span>
            <button class="memory-toolbar-btn danger" id="activity-undo-session" title="Undo every file write from this session, newest first">Roll back all writes</button>
            <button class="memory-toolbar-btn" id="activity-session-clear">Clear filter</button>
          </div>
          <div class="memory-toolbar">
            <select class="memory-sort-select" id="activity-tool-filter" title="Filter by tool" style="width:110px;font-size:11px;height:24px;">
              <option value="">All tools</option>
              <option value="bash">bash</option>
              <option value="python">python</option>
              <option value="write_file">write_file</option>
              <option value="web_search">web_search</option>
              <option value="web_fetch">web_fetch</option>
              <option value="browser">browser</option>
              <option value="undo">undo</option>
            </select>
            <input type="text" id="activity-search" placeholder="Search commands, output, paths…" class="memory-search-input" />
          </div>
          <div id="activity-list" class="memory-list" style="flex:1;gap:4px;"></div>
          <div id="activity-err" style="font-size:11px;color:#c0392b;"></div>
        </div>
      </div>
    </div>
  `;
  document.body.appendChild(modal);

  document.getElementById('activity-close').addEventListener('click', closeActivity);
  modal.addEventListener('click', (e) => { if (e.target === modal) closeActivity(); });

  const searchEl = document.getElementById('activity-search');
  let _debounce = null;
  searchEl.addEventListener('input', () => {
    _search = searchEl.value.trim();
    clearTimeout(_debounce);
    _debounce = setTimeout(_refresh, 250);
  });
  const filterEl = document.getElementById('activity-tool-filter');
  filterEl.addEventListener('change', () => { _toolFilter = filterEl.value; _refresh(); });

  const autonomyEl = document.getElementById('activity-autonomy');
  const autonomyMsg = document.getElementById('activity-autonomy-msg');
  fetch('/api/activity/autonomy', { credentials: 'same-origin' })
    .then(r => r.json())
    .then(d => { if (autonomyEl && d.mode) autonomyEl.value = d.mode; _describeAutonomy(); })
    .catch(() => {});
  function _describeAutonomy() {
    if (!autonomyMsg || !autonomyEl) return;
    autonomyMsg.textContent = autonomyEl.value === 'observe'
      ? 'Agent can read and propose only — nothing changes on this machine.'
      : 'Agent acts freely; every action lands here and file writes are undoable.';
  }
  if (autonomyEl) {
    autonomyEl.addEventListener('change', () => {
      fetch('/api/activity/autonomy', {
        method: 'PUT', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode: autonomyEl.value })
      }).then(r => r.json()).then(_describeAutonomy)
        .catch(() => { if (autonomyMsg) autonomyMsg.textContent = 'Failed to save mode.'; });
    });
  }

  const clearBtn = document.getElementById('activity-session-clear');
  if (clearBtn) clearBtn.addEventListener('click', () => _setSessionFilter(''));
  const undoSessBtn = document.getElementById('activity-undo-session');
  if (undoSessBtn) {
    undoSessBtn.addEventListener('click', () => {
      if (!_sessionFilter) return;
      if (!confirm('Undo EVERY file write from this session? Files step back to their pre-session contents.')) return;
      undoSessBtn.disabled = true;
      fetch('/api/activity/undo-session/' + encodeURIComponent(_sessionFilter), {
        method: 'POST', credentials: 'same-origin'
      }).then(r => r.json()).then(res => {
        const err = document.getElementById('activity-err');
        if (err) {
          err.style.color = res.ok ? '' : '#c0392b';
          err.textContent = res.ok
            ? `Rolled back ${res.undone} write(s)` + (res.failed && res.failed.length ? `, ${res.failed.length} failed` : '')
            : (res.error || 'rollback failed');
        }
        _refresh();
      }).catch(() => {}).finally(() => { undoSessBtn.disabled = false; });
    });
  }

  _refresh();
}

function _setSessionFilter(sid) {
  _sessionFilter = sid || '';
  const bar = document.getElementById('activity-session-bar');
  const label = document.getElementById('activity-session-label');
  if (bar) bar.style.display = _sessionFilter ? 'flex' : 'none';
  if (label) label.textContent = _sessionFilter.slice(0, 8) + (_sessionFilter.length > 8 ? '…' : '');
  _refresh();
}

function _refresh() {
  const list = document.getElementById('activity-list');
  const err = document.getElementById('activity-err');
  if (!list) return;
  if (err) err.textContent = '';
  const params = new URLSearchParams({ limit: '200' });
  if (_search) params.set('q', _search);
  if (_toolFilter) params.set('tool', _toolFilter);
  if (_sessionFilter) params.set('session_id', _sessionFilter);
  fetch('/api/activity?' + params.toString(), { credentials: 'same-origin' })
    .then(r => {
      if (!r.ok) throw new Error(r.status === 403 ? 'Admin only' : 'HTTP ' + r.status);
      return r.json();
    })
    .then(data => _renderList(data.events || []))
    .catch(e => { if (err) err.textContent = 'Failed to load history: ' + e.message; });
}

function _renderList(events) {
  const list = document.getElementById('activity-list');
  if (!list) return;
  if (!events.length) {
    list.innerHTML = '<div style="opacity:0.55;font-size:12px;padding:12px;">No activity recorded yet. Events appear here as the agent uses tools.</div>';
    return;
  }
  list.innerHTML = events.map(ev => {
    const undoBtn = ev.undoable
      ? `<button class="memory-toolbar-btn" data-undo="${_esc(ev.id)}" title="Restore this file to its pre-write state">Undo</button>`
      : (ev.undone ? '<span style="font-size:10px;opacity:0.6;">undone</span>' : '');
    const status = ev.exit_code === 0 || ev.exit_code == null
      ? '' : `<span style="color:#c0392b;font-size:10px;">exit ${_esc(ev.exit_code)}</span>`;
    const dur = ev.duration_ms != null ? `<span style="font-size:10px;opacity:0.5;">${ev.duration_ms}ms</span>` : '';
    return `
      <div class="memory-item" style="flex-direction:column;align-items:stretch;gap:3px;">
        <div style="display:flex;gap:8px;align-items:center;">
          <span style="font-weight:600;font-size:12px;">${_esc(ev.tool)}</span>
          ${ev.session_id ? `<span data-session="${_esc(ev.session_id)}" title="Filter to this session (enables session rollback)" style="font-size:10px;opacity:0.55;cursor:pointer;text-decoration:underline dotted;">${_esc(ev.session_id.slice(0, 8))}</span>` : ''}
          ${ev.path ? `<span style="font-size:11px;opacity:0.7;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${_esc(ev.path)}</span>` : ''}
          <span style="flex:1"></span>
          ${status} ${dur}
          <span style="font-size:10px;opacity:0.55;">${_esc(_fmtTime(ev.created_at))}</span>
          ${undoBtn}
        </div>
        <div style="font-size:11px;opacity:0.8;white-space:pre-wrap;word-break:break-word;max-height:60px;overflow:hidden;">${_esc((ev.input_preview || '').slice(0, 300))}</div>
        ${ev.output_preview ? `<div style="font-size:10px;opacity:0.55;white-space:pre-wrap;word-break:break-word;max-height:40px;overflow:hidden;">${_esc(ev.output_preview.slice(0, 200))}</div>` : ''}
      </div>`;
  }).join('');

  list.querySelectorAll('[data-session]').forEach(chip => {
    chip.addEventListener('click', () => _setSessionFilter(chip.dataset.session));
  });

  list.querySelectorAll('[data-undo]').forEach(btn => {
    btn.addEventListener('click', () => {
      if (!confirm('Restore this file to its state before the agent wrote it?')) return;
      btn.disabled = true;
      fetch('/api/activity/' + encodeURIComponent(btn.dataset.undo) + '/undo', {
        method: 'POST', credentials: 'same-origin'
      }).then(r => r.json()).then(res => {
        if (!res.ok) throw new Error(res.error || 'undo failed');
        _refresh();
      }).catch(e => {
        const err = document.getElementById('activity-err');
        if (err) err.textContent = 'Undo failed: ' + e.message;
        btn.disabled = false;
      });
    });
  });
}

export default { openActivity, closeActivity, isActivityOpen };
