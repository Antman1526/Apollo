// Apollo Browser panel — sandboxed UI iframe + agent browser route sync.

const BLOCKED_SCHEMES = new Set([
  'about:', 'apollo:', 'chrome:', 'chrome-extension:', 'data:', 'devtools:',
  'electron:', 'file:', 'javascript:', 'node:', 'vscode:',
]);
const LOCALHOST_RE = /((?:https?:\/\/)?(?:localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\])(?::\d{1,5})(?:\/[^\s"'<>]*)?)/ig;
const HOST_PORT_RE = /^(?:[A-Za-z0-9.-]+|\[[0-9A-Fa-f:.]+\]):\d{1,5}(?:[/?#].*)?$/;

let historyStack = [];
let historyIndex = -1;
let eventsTimer = null;
let lastEventId = 0;
let initialized = false;

function el(id) {
  return document.getElementById(id);
}

function normalizeUrl(raw) {
  const input = String(raw || '').trim();
  if (!input) throw new Error('Enter a URL');
  if (/[\n\r\t]/.test(input)) throw new Error('URL must be one line');
  let url = input;
  const schemeMatch = url.match(/^([a-z][a-z0-9+.-]*):/i);
  if (schemeMatch && !/^[a-z][a-z0-9+.-]*:\/\//i.test(url)) {
    const scheme = `${schemeMatch[1].toLowerCase()}:`;
    if (BLOCKED_SCHEMES.has(scheme) || !HOST_PORT_RE.test(url)) {
      throw new Error(`Blocked URL scheme: ${scheme.replace(':', '')}`);
    }
  }
  if (!/^[a-z][a-z0-9+.-]*:\/\//i.test(url)) url = `http://${url}`;
  const parsed = new URL(url);
  if (BLOCKED_SCHEMES.has(parsed.protocol) || !['http:', 'https:'].includes(parsed.protocol)) {
    throw new Error(`Blocked URL scheme: ${parsed.protocol.replace(':', '')}`);
  }
  if (parsed.origin === window.location.origin) {
    throw new Error('Open Apollo panels from the sidebar, not inside the browser frame');
  }
  return parsed.href;
}

function setStatus(text, warning) {
  const status = el('browser-status');
  const security = el('browser-security');
  if (status) status.textContent = text || 'Ready';
  if (security) security.textContent = warning || '';
}

function syncButtons() {
  const back = el('browser-back');
  const forward = el('browser-forward');
  if (back) back.disabled = historyIndex <= 0;
  if (forward) forward.disabled = historyIndex < 0 || historyIndex >= historyStack.length - 1;
}

function pushHistory(url) {
  if (historyStack[historyIndex] === url) return;
  historyStack = historyStack.slice(0, historyIndex + 1);
  historyStack.push(url);
  historyIndex = historyStack.length - 1;
  syncButtons();
}

function setFrameUrl(url, { record = true } = {}) {
  const frame = el('browser-frame');
  const address = el('browser-address');
  if (!frame || !address) return;
  frame.src = url;
  address.value = url;
  if (record) pushHistory(url);
}

async function syncAgentBrowser(url) {
  try {
    const res = await fetch('/api/browser/navigate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
      body: JSON.stringify({ url }),
    });
    if (!res.ok) {
      const text = await res.text();
      setStatus('Rendered in panel; agent browser unavailable', text.slice(0, 180));
      return;
    }
    const data = await res.json();
    setStatus(data.title || 'Loaded', data.warning === 'non_secure_http' ? 'Non-secure HTTP' : '');
  } catch (err) {
    setStatus('Rendered in panel; agent browser unavailable', err.message || String(err));
  }
}

async function navigate(raw, options = {}) {
  let url;
  try {
    url = normalizeUrl(raw);
  } catch (err) {
    setStatus(err.message || String(err));
    return;
  }
  setStatus('Loading...');
  setFrameUrl(url, options);
  await syncAgentBrowser(url);
}

function goBack() {
  if (historyIndex <= 0) return;
  historyIndex -= 1;
  const url = historyStack[historyIndex];
  setFrameUrl(url, { record: false });
  syncAgentBrowser(url);
  syncButtons();
}

function goForward() {
  if (historyIndex < 0 || historyIndex >= historyStack.length - 1) return;
  historyIndex += 1;
  const url = historyStack[historyIndex];
  setFrameUrl(url, { record: false });
  syncAgentBrowser(url);
  syncButtons();
}

function reload() {
  const frame = el('browser-frame');
  if (frame && frame.src) {
    setStatus('Reloading...');
    frame.src = frame.src;
    syncAgentBrowser(frame.src);
  }
}

async function pollEvents() {
  try {
    const res = await fetch('/api/browser/events', { credentials: 'same-origin' });
    if (!res.ok) return;
    const data = await res.json();
    const events = Array.isArray(data.events) ? data.events : [];
    const newestEvent = events[events.length - 1] || {};
    const newestId = Number(newestEvent.id || events.length || 0);
    if (newestId === lastEventId) return;
    lastEventId = newestId;
    const log = el('browser-console-log');
    if (!log) return;
    log.innerHTML = '';
    events.slice(-80).forEach((event) => {
      const line = document.createElement('div');
      line.className = 'browser-console-line';
      line.textContent = `[${event.kind}] ${event.message}`;
      log.appendChild(line);
    });
    log.scrollTop = log.scrollHeight;
  } catch (_) {}
}

function startEventPolling() {
  if (eventsTimer) return;
  eventsTimer = window.setInterval(pollEvents, 2000);
  pollEvents();
}

function stopEventPolling() {
  if (eventsTimer) window.clearInterval(eventsTimer);
  eventsTimer = null;
}

function open(url) {
  const modal = el('browser-modal');
  if (!modal) return;
  modal.classList.remove('hidden');
  startEventPolling();
  if (url) navigate(url);
  else el('browser-address')?.focus();
}

function close() {
  const modal = el('browser-modal');
  if (modal) modal.classList.add('hidden');
  stopEventPolling();
}

function detectLocalhost(text) {
  const urls = [];
  String(text || '').replace(LOCALHOST_RE, (match) => {
    let url = match.replace(/[.,);]+$/, '');
    if (!/^https?:\/\//i.test(url)) url = `http://${url}`;
    try {
      url = normalizeUrl(url);
    } catch (_) {
      return match;
    }
    if (!urls.includes(url)) urls.push(url);
    return match;
  });
  if (!urls.length) return [];
  const latest = urls[urls.length - 1];
  setStatus(`Detected ${latest}`);
  if (window.uiModule?.showToast) {
    window.uiModule.showToast(`Dev server detected: ${latest}`);
  }
  return urls;
}

function initLocalhostObserver() {
  if (document._apolloBrowserLocalhostObserver) return;
  document._apolloBrowserLocalhostObserver = true;
  const seen = new Set();
  const observer = new MutationObserver((mutations) => {
    for (const mutation of mutations) {
      for (const node of mutation.addedNodes || []) {
        const text = node?.textContent || '';
        if (!text || !/(localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\])/i.test(text)) continue;
        detectLocalhost(text).forEach((url) => {
          if (seen.has(url)) return;
          seen.add(url);
          if (window.uiModule?.showToast) {
            window.uiModule.showToast(`Open ${url} from the Browser panel`);
          }
        });
      }
    }
  });
  observer.observe(document.body, { childList: true, subtree: true });
}

function init() {
  if (initialized) return;
  initialized = true;

  el('browser-go')?.addEventListener('click', () => navigate(el('browser-address')?.value || ''));
  el('browser-address')?.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') navigate(event.currentTarget.value);
  });
  el('browser-back')?.addEventListener('click', goBack);
  el('browser-forward')?.addEventListener('click', goForward);
  el('browser-reload')?.addEventListener('click', reload);
  el('browser-open-external')?.addEventListener('click', () => {
    const url = el('browser-frame')?.src || el('browser-address')?.value;
    if (url) window.open(url, '_blank', 'noopener,noreferrer');
  });
  el('close-browser-modal')?.addEventListener('click', close);
  el('browser-frame')?.addEventListener('load', () => {
    const url = el('browser-frame')?.src;
    if (url) {
      el('browser-address').value = url;
      setStatus('Loaded');
    }
  });
  initLocalhostObserver();
  syncButtons();
}

export default {
  init,
  open,
  close,
  navigate,
  detectLocalhost,
};
