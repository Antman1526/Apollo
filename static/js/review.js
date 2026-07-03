// ============================================
// Apollo — Adversarial Reviewer (frontend)
// ES6 module. Adds an inline "Review" button to assistant messages and
// an optional persisted "Review mode" that auto-reviews each answer.
//
// Mirrors addAITTSButton (tts-ai.js:459): finds the message's .msg-actions
// container, dedup-guards, and appends a small icon button. On click it POSTs
// /api/review {question, answer} and renders {verdict, issues, suggestion} in a
// collapsible box under the message.
// ============================================

import { loadToggleState } from './storage.js';

const ICON_REVIEW = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 11l3 3L22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/></svg>';
const ICON_LOADING = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><circle cx="12" cy="12" r="9" stroke-dasharray="42" stroke-dashoffset="12" stroke-linecap="round"><animateTransform attributeName="transform" type="rotate" from="0 12 12" to="360 12 12" dur="0.8s" repeatCount="indefinite"/></circle></svg>';

// verdict → badge color. accurate=green, incomplete/needs-context=amber, incorrect=red.
function verdictColor(verdict) {
  const v = (verdict || '').toLowerCase();
  if (v.includes('incorrect')) return '#ef4444';        // red
  if (v.includes('accurate')) return '#22c55e';         // green
  if (v.includes('incomplete') || v.includes('needs')) return '#f59e0b'; // amber
  return '#6b7280';                                     // neutral / unknown
}

function escapeHtml(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

// Build (or reuse) the collapsible review box under the message element.
function ensureReviewBox(messageElement) {
  let box = messageElement.querySelector('.review-box');
  if (box) return box;
  box = document.createElement('details');
  box.className = 'review-box';
  box.open = true;
  box.style.cssText = 'margin:8px 0 4px;border:1px solid #2a2a2a;border-radius:8px;padding:6px 10px;font-size:13px;background:rgba(255,255,255,0.02);';
  messageElement.appendChild(box);
  return box;
}

function renderReview(box, result) {
  const verdict = result.verdict || 'unknown';
  const color = verdictColor(verdict);
  const issues = Array.isArray(result.issues) ? result.issues : [];
  const suggestion = result.suggestion || '';

  const badge = '<span class="review-verdict-badge" style="display:inline-flex;align-items:center;gap:4px;padding:2px 8px;border-radius:10px;font-weight:600;font-size:12px;color:#fff;background:' + color + ';text-transform:capitalize;">' + escapeHtml(verdict) + '</span>';

  let issuesHtml = '';
  if (issues.length) {
    issuesHtml = '<ul style="margin:8px 0 0;padding-left:18px;color:#c9c9c9;">' +
      issues.map((i) => '<li style="margin:2px 0;">' + escapeHtml(i) + '</li>').join('') +
      '</ul>';
  } else {
    issuesHtml = '<div style="margin-top:8px;color:#9ca3af;">No issues flagged.</div>';
  }

  let suggestionHtml = '';
  if (suggestion) {
    suggestionHtml = '<div style="margin-top:8px;color:#c9c9c9;"><strong style="color:#9ca3af;">Suggestion:</strong> ' + escapeHtml(suggestion) + '</div>';
  }

  box.innerHTML =
    '<summary style="cursor:pointer;color:#9ca3af;list-style:none;display:flex;align-items:center;gap:8px;">' +
      '<span style="font-weight:600;">Review</span>' + badge +
    '</summary>' +
    '<div style="margin-top:6px;">' + issuesHtml + suggestionHtml + '</div>';
}

function renderError(box, message) {
  box.innerHTML =
    '<summary style="cursor:pointer;color:#9ca3af;list-style:none;">Review</summary>' +
    '<div style="margin-top:6px;color:#ef4444;">' + escapeHtml(message || 'Review failed') + '</div>';
}

// Perform the /api/review call and render the result into the message's box.
async function runReview(messageElement, question, answer, buttonEl) {
  const box = ensureReviewBox(messageElement);
  box.innerHTML = '<summary style="cursor:pointer;color:#9ca3af;list-style:none;">Review</summary><div style="margin-top:6px;color:#9ca3af;">Reviewing…</div>';
  if (buttonEl) { buttonEl.innerHTML = ICON_LOADING; buttonEl.classList.add('loading'); }

  try {
    const response = await fetch('/api/review', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question: question || '', answer: answer || '' }),
    });
    if (!response.ok) {
      let detail = 'Review failed (' + response.status + ')';
      try { const err = await response.json(); if (err && err.detail) detail = err.detail; } catch (e) { /* ignore */ }
      renderError(box, detail);
      return;
    }
    const result = await response.json();
    renderReview(box, result);
  } catch (e) {
    renderError(box, (e && e.message) || 'Review request failed');
  } finally {
    if (buttonEl) { buttonEl.innerHTML = ICON_REVIEW; buttonEl.classList.remove('loading'); buttonEl.style.color = '#6b7280'; }
  }
}

// Find the user question for a given assistant message element by walking back
// to the preceding user bubble in the DOM.
function findPrecedingQuestion(messageElement) {
  if (!messageElement) return '';
  // Prefer a walk-back from the assistant bubble; fall back to the last user
  // bubble in the history (streaming assistant is appended after it).
  let node = messageElement;
  while (node) {
    node = node.previousElementSibling;
    if (node && node.classList && node.classList.contains('msg-user')) {
      return (node.dataset && node.dataset.raw) || (node.querySelector('.body')?.textContent) || '';
    }
  }
  const last = document.querySelector('#chat-history .msg-user:last-of-type');
  if (last) return (last.dataset && last.dataset.raw) || (last.querySelector('.body')?.textContent) || '';
  return '';
}

// Public API — mirrors addAITTSButton(messageElement, text).
export function addReviewButton(messageElement, question, answer) {
  if (!messageElement) return;
  if (messageElement.querySelector('.review-button')) return;

  const actions = messageElement.querySelector('.msg-actions');
  if (!actions) return;

  const btn = document.createElement('button');
  btn.className = 'review-button';
  btn.type = 'button';
  btn.title = 'Review this answer';
  btn.innerHTML = ICON_REVIEW;
  btn.style.cssText = 'background:none;border:none;color:#6b7280;cursor:pointer;padding:2px 6px;border-radius:4px;transition:color .15s;line-height:1;display:inline-flex;align-items:center;';

  btn.addEventListener('mouseenter', () => { if (!btn.classList.contains('loading')) btn.style.color = '#ccc'; });
  btn.addEventListener('mouseleave', () => { if (!btn.classList.contains('loading')) btn.style.color = '#6b7280'; });

  btn.addEventListener('click', (e) => {
    e.stopPropagation();
    const q = question || findPrecedingQuestion(messageElement);
    const a = answer || messageElement.dataset.raw || messageElement.querySelector('.body')?.textContent || '';
    runReview(messageElement, q, a, btn);
  });

  actions.appendChild(btn);
}

// ── Review gate: mark a completed answer "under review" (dim + badge) until the
//    /api/review verdict returns, then swap the pending badge for the verdict.
//    This is a purely VISUAL gate — the answer is already saved and the chat
//    stream is never blocked. Independent of Review Mode. ──

// Add (or reuse) a small "⏳ under review" badge in the message's action bar and
// dim the message body while the review is in flight.
function addPendingBadge(messageElement) {
  const actions = messageElement.querySelector('.msg-actions');
  if (!actions) return null;
  let badge = actions.querySelector('.review-gate-badge');
  if (!badge) {
    badge = document.createElement('span');
    badge.className = 'review-gate-badge';
    actions.appendChild(badge);
  }
  badge.style.cssText = 'display:inline-flex;align-items:center;gap:4px;padding:2px 8px;border-radius:10px;font-weight:600;font-size:12px;color:#fff;background:#6b7280;';
  badge.textContent = '⏳ under review';
  messageElement.classList.add('review-pending');
  messageElement.style.opacity = '0.6';
  messageElement.style.transition = 'opacity .2s';
  return badge;
}

// Replace the pending badge with the final verdict badge and un-dim the message.
function resolvePendingBadge(messageElement, verdict) {
  messageElement.classList.remove('review-pending');
  messageElement.style.opacity = '';
  const badge = messageElement.querySelector('.review-gate-badge');
  if (!badge) return;
  const color = verdictColor(verdict);
  badge.style.background = color;
  badge.style.textTransform = 'capitalize';
  badge.textContent = verdict || 'unknown';
}

// Run the review for the gate: show pending state, POST /api/review, render the
// full review box AND swap the inline pending badge for the verdict badge.
async function runGatedReview(messageElement, question, answer) {
  const badge = addPendingBadge(messageElement);
  const box = ensureReviewBox(messageElement);
  box.innerHTML = '<summary style="cursor:pointer;color:#9ca3af;list-style:none;">Review</summary><div style="margin-top:6px;color:#9ca3af;">Reviewing…</div>';
  try {
    const response = await fetch('/api/review', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question: question || '', answer: answer || '' }),
    });
    if (!response.ok) {
      let detail = 'Review failed (' + response.status + ')';
      try { const err = await response.json(); if (err && err.detail) detail = err.detail; } catch (e) { /* ignore */ }
      renderError(box, detail);
      resolvePendingBadge(messageElement, 'error');
      if (badge) badge.style.background = '#ef4444';
      return;
    }
    const result = await response.json();
    renderReview(box, result);
    resolvePendingBadge(messageElement, result.verdict || 'unknown');
  } catch (e) {
    renderError(box, (e && e.message) || 'Review request failed');
    resolvePendingBadge(messageElement, 'error');
    if (badge) badge.style.background = '#ef4444';
  }
}

// ── Auto-review: listen for the assistant-complete event and, when Review mode
//    OR Review gate is on, run a review for the just-finished message. The gate
//    variant adds the pending badge / dim state; Review mode alone does not. ──
window.addEventListener('apollo:assistant-complete', (ev) => {
  let toggles = {};
  try { toggles = loadToggleState() || {}; } catch (e) { /* ignore */ }
  const gateOn = !!toggles.reviewGate;
  const modeOn = !!toggles.reviewMode;
  if (!gateOn && !modeOn) return;

  const answer = (ev && ev.detail && ev.detail.text) || '';
  if (!answer) return;

  // The just-finished assistant bubble is the last .msg-ai in history.
  const messageElement = document.querySelector('#chat-history .msg-ai:last-of-type');
  if (!messageElement) return;
  // Skip if it has no action bar yet (nothing to attach to / not a real answer).
  if (!messageElement.querySelector('.msg-actions')) return;

  const question = findPrecedingQuestion(messageElement);
  if (gateOn) {
    runGatedReview(messageElement, question, answer);
  } else {
    const btn = messageElement.querySelector('.review-button');
    runReview(messageElement, question, answer, btn);
  }
});

const reviewModule = { addReviewButton };
export default reviewModule;
