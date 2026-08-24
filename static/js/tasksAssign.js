// static/js/tasksAssign.js
//
// "Assign to agent" quick bar for the Tasks panel, kept out of tasks.js for
// the module-size ratchet (scripts/check_module_sizes.py). Injects its own
// markup above the tasks toolbar and wires the POST /api/tasks/assign flow;
// `refresh` re-fetches and re-renders the task list after an assign.

export function wireAssignBar(refresh) {
  const toolbar = document.querySelector('#tasks-modal .memory-toolbar');
  if (!toolbar || document.getElementById('tasks-assign-input')) return;
  toolbar.insertAdjacentHTML('beforebegin', `
    <div style="display:flex;gap:6px;align-items:center;margin-bottom:8px;position:relative;top:-4px;">
      <input type="text" id="tasks-assign-input" placeholder="Assign a task to the agent — it runs in the background…" class="memory-search-input" style="flex:1;" />
      <button class="memory-toolbar-btn" id="tasks-assign-btn" title="Create an agent task from this prompt and run it now">Assign to agent</button>
    </div>
    <div id="tasks-assign-msg" style="font-size:11px;margin-bottom:4px;position:relative;top:-4px;color:color-mix(in srgb, var(--fg) 55%, transparent);"></div>
  `);

  const assignInput = document.getElementById('tasks-assign-input');
  const assignBtn = document.getElementById('tasks-assign-btn');
  const assignMsg = document.getElementById('tasks-assign-msg');
  const doAssign = () => {
    const prompt = assignInput.value.trim();
    if (!prompt) return;
    assignBtn.disabled = true;
    if (assignMsg) assignMsg.textContent = 'Assigning…';
    fetch('/api/tasks/assign', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ prompt })
    }).then(r => {
      if (!r.ok) return r.json().then(d => { throw new Error(d.detail || ('HTTP ' + r.status)); });
      return r.json();
    }).then(data => {
      assignInput.value = '';
      if (assignMsg) assignMsg.textContent = data.started
        ? `"${(data.task && data.task.name) || 'Task'}" is running in the background — watch the Activity tab.`
        : 'Task created (already running — see Activity).';
      refresh();
      setTimeout(() => { if (assignMsg) assignMsg.textContent = ''; }, 6000);
    }).catch(e => {
      if (assignMsg) assignMsg.textContent = 'Assign failed: ' + e.message;
    }).finally(() => { assignBtn.disabled = false; });
  };
  assignBtn.addEventListener('click', doAssign);
  assignInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); doAssign(); }
  });
}
