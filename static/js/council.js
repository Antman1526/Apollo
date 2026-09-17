// static/js/council.js
// The Council — ask up to three models the same question concurrently, then
// let the reviewer-role model synthesize consensus, disagreements, and a
// recommended answer.
//
// pickCouncilMembers / renderCouncilHTML are pure (no DOM) so they're unit
// tested directly under Node (tests/test_council_render.mjs). askCouncil /
// initCouncil touch the DOM and are wired up from app.js at runtime.

const MAX_MEMBERS = 3;
const MIN_MEMBERS = 2;

function escapeHtml(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
}

/**
 * Pick up to MAX_MEMBERS chat-capable models for the Council: the current
 * session's model first (if any), then other chat-capable models —
 * preferring a different endpoint from ones already picked — skipping image
 * endpoints and (endpoint, model) duplicates.
 *
 * @param {Array} items - /api/models cached items (see models.js getCachedItems)
 * @param {{model?: string, url?: string}} current - the active session's model + endpoint
 * @param {(item, modelId) => boolean} isChatCapable
 * @returns {Array<{model:string, endpoint_url:string, label:string, endpoint:string}>}
 */
export function pickCouncilMembers(items, current, isChatCapable) {
  const list = Array.isArray(items) ? items : [];
  const chatCapable = typeof isChatCapable === 'function' ? isChatCapable : () => true;
  const picked = [];
  const usedKeys = new Set();
  const usedEndpoints = new Set();

  function take(item, modelId, label) {
    if (picked.length >= MAX_MEMBERS) return;
    const url = item && item.url;
    if (!url || !modelId) return;
    const key = `${url}::${modelId}`;
    if (usedKeys.has(key)) return;
    usedKeys.add(key);
    usedEndpoints.add(url);
    picked.push({
      model: modelId,
      endpoint_url: url,
      label: label || modelId,
      endpoint: (item && item.endpoint_name) || url,
    });
  }

  // 1. Current session model first.
  if (current && current.model && current.url) {
    const curItem = list.find((it) => it && it.url === current.url) || { url: current.url };
    if (curItem.model_type !== 'image' && chatCapable(curItem, current.model)) {
      take(curItem, current.model, current.model);
    }
  }

  // Flat pool of every other chat-capable model, skipping image endpoints.
  const pool = [];
  for (const item of list) {
    if (!item || item.model_type === 'image' || !item.url) continue;
    const ids = (item.models || []).concat(item.models_extra || []);
    const labels = (item.models_display || []).concat(item.models_extra_display || item.models_extra || []);
    ids.forEach((id, i) => {
      if (!chatCapable(item, id)) return;
      pool.push({ item, modelId: id, label: labels[i] || id });
    });
  }

  // 2. Prefer a model from an endpoint not already represented.
  for (const cand of pool) {
    if (picked.length >= MAX_MEMBERS) break;
    if (usedEndpoints.has(cand.item.url)) continue;
    take(cand.item, cand.modelId, cand.label);
  }

  // 3. Fill any remaining slots from whatever's left.
  for (const cand of pool) {
    if (picked.length >= MAX_MEMBERS) break;
    take(cand.item, cand.modelId, cand.label);
  }

  return picked;
}

function renderSectionText(text, mdToHtml) {
  const t = text || '';
  if (typeof mdToHtml === 'function') return mdToHtml(t) || `<pre>${escapeHtml(t)}</pre>`;
  return `<pre>${escapeHtml(t)}</pre>`;
}

function councilHeaderHtml(label) {
  return `<div class="council-header">${label} <span class="council-muted">· not saved to history</span></div>`;
}

function councilQuestionHtml(question) {
  return question ? `<div class="council-question">${escapeHtml(question)}</div>` : '';
}

/**
 * Render a Council result as the HTML for a `.msg.msg-ai.council-block`
 * transcript bubble. Pure — takes the /api/council/ask response shape and
 * an optional markdown renderer.
 *
 * The exchange is never persisted to chat history, so the question is
 * rendered inside the block itself (`.council-question`) rather than as a
 * normal user bubble, and the header says so.
 */
export function renderCouncilHTML(result, mdToHtml) {
  const answers = (result && result.answers) || [];
  const synthesis = result && result.synthesis;
  const question = (result && result.question) || '';
  const n = answers.length;

  const answerBlocks = answers.map((a, i) => {
    const isError = !!(a && a.error);
    const modelLabel = escapeHtml((a && a.model) || '?');
    const bodyHtml = isError
      ? `<div class="council-answer-error-msg">${escapeHtml(a.error)}</div>`
      : `<div class="council-answer-body">${renderSectionText(a && a.text, mdToHtml)}</div>`;
    return (
      `<details class="council-answer${isError ? ' council-answer--error' : ''}"${i === 0 ? ' open' : ''}>` +
        `<summary>${modelLabel}</summary>${bodyHtml}` +
      `</details>`
    );
  }).join('');

  let synthesisHtml = '';
  if (synthesis && synthesis.error) {
    // Reviewer itself failed: empty sections would just look broken —
    // show a clear error state instead of three blank labels.
    synthesisHtml =
      `<div class="council-synthesis council-synthesis--error">` +
        `<div class="council-synthesis-header">Reviewer synthesis · ${escapeHtml(synthesis.model || '')}</div>` +
        `<div class="council-synthesis-error-msg">${escapeHtml(synthesis.error)}</div>` +
      `</div>`;
  } else if (synthesis) {
    const sections = synthesis.sections || {};
    synthesisHtml =
      `<div class="council-synthesis">` +
        `<div class="council-synthesis-header">Reviewer synthesis · ${escapeHtml(synthesis.model || '')}</div>` +
        `<div class="council-synthesis-section"><span class="council-section-label">Consensus</span>${renderSectionText(sections.consensus, mdToHtml)}</div>` +
        `<div class="council-synthesis-section"><span class="council-section-label">Disagreements</span>${renderSectionText(sections.disagreements, mdToHtml)}</div>` +
        `<div class="council-synthesis-section"><span class="council-section-label">Recommended answer</span>${renderSectionText(sections.recommended, mdToHtml)}</div>` +
      `</div>`;
  }

  return (
    `<div class="msg msg-ai council-block">` +
      councilHeaderHtml(`The Council · ${n} model${n === 1 ? '' : 's'}`) +
      councilQuestionHtml(question) +
      `<div class="council-answers">${answerBlocks}</div>` +
      synthesisHtml +
    `</div>`
  );
}

// ── DOM-touching runtime (not exercised by the pure-function tests) ──

let deps = {};
// Re-entry guard: only one Council round may be in flight at a time.
let inFlight = false;

function htmlToNode(html) {
  const tmp = document.createElement('div');
  tmp.innerHTML = html;
  return tmp.firstElementChild;
}

function placeholderNode(question, count) {
  const wrap = document.createElement('div');
  wrap.className = 'msg msg-ai council-block council-block--loading';
  wrap.innerHTML =
    councilHeaderHtml('The Council') +
    councilQuestionHtml(question) +
    `<div class="thinking-indicator"><span>Convening ${count} models</span><span class="thinking-dots"></span></div>` +
    `<button type="button" class="council-cancel-btn">Cancel</button>`;
  return wrap;
}

/** Ask the Council `question`, rendering the exchange into #chat-history. */
export async function askCouncil(question) {
  const q = (question || '').trim();
  if (!q) return;

  const showToast = deps.showToast || (() => {});
  if (inFlight) {
    showToast('The Council is already in session');
    return;
  }

  const getCachedItems = deps.getCachedItems || (() => []);
  const isChatCapable = deps.isChatCapable || (() => true);
  const getCurrentModel = deps.getCurrentModel || (() => null);
  const getCurrentEndpointUrl = deps.getCurrentEndpointUrl || (() => null);
  const mdToHtml = deps.mdToHtml;

  const current = { model: getCurrentModel(), url: getCurrentEndpointUrl() };
  const members = pickCouncilMembers(getCachedItems(), current, isChatCapable);
  if (members.length < MIN_MEMBERS) {
    showToast('The Council needs at least two chat models');
    return;
  }

  inFlight = true;
  const controller = new AbortController();
  const box = document.getElementById('chat-history');
  const placeholder = placeholderNode(q, members.length);
  const cancelBtn = placeholder.querySelector('.council-cancel-btn');
  if (cancelBtn) {
    cancelBtn.addEventListener('click', () => {
      controller.abort();
      placeholder.remove();
      inFlight = false;
    });
  }
  if (box) {
    box.appendChild(placeholder);
    placeholder.scrollIntoView({ behavior: 'smooth', block: 'end' });
  }

  try {
    const res = await fetch(`${deps.API_BASE || ''}/api/council/ask`, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        question: q,
        members: members.map((m) => ({ model: m.model, endpoint_url: m.endpoint_url })),
      }),
      signal: controller.signal,
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const result = await res.json();
    const node = htmlToNode(renderCouncilHTML(result, mdToHtml));
    if (placeholder.parentNode) placeholder.replaceWith(node);
    else if (box) box.appendChild(node);
    node.scrollIntoView({ behavior: 'smooth', block: 'end' });
  } catch (error) {
    if (error && error.name === 'AbortError') return; // user hit Cancel
    showToast('The Council could not be reached');
    if (placeholder.parentNode) {
      placeholder.classList.remove('council-block--loading');
      placeholder.classList.add('council-block--error');
      placeholder.innerHTML =
        councilHeaderHtml('The Council') +
        `<div class="council-error">Something went wrong asking the Council: ${escapeHtml(error && error.message)}</div>`;
    }
  } finally {
    inFlight = false;
  }
}

/** Wire the palette action + slash-command entry point. `initDeps`: see app.js. */
export function initCouncil(initDeps) {
  deps = initDeps || {};
  window.addEventListener('apollo:palette-action', (event) => {
    if (!event || !event.detail || event.detail.id !== 'council') return;
    event.preventDefault();
    const input = document.getElementById('message');
    const draft = input && input.value ? input.value.trim() : '';
    if (draft) {
      input.value = '';
      input.dispatchEvent(new Event('input', { bubbles: true }));
      askCouncil(draft);
      return;
    }
    const styledPrompt = deps.styledPrompt;
    if (typeof styledPrompt !== 'function') return;
    styledPrompt('What would you like to ask?', {
      title: 'Ask the Council',
      placeholder: 'Your question',
      confirmText: 'Ask',
      maxLength: 500,
    }).then((value) => {
      if (value && value.trim()) askCouncil(value.trim());
    });
  });
  window.councilModule = { ask: askCouncil };
}

const councilModule = { pickCouncilMembers, renderCouncilHTML, askCouncil, initCouncil };
export default councilModule;
