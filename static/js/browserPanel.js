// Apollo Browser panel — live canvas screencast of the agent's server-side
// Chromium over a WebSocket, with full mouse/keyboard input forwarding.

const BLOCKED_SCHEMES = new Set([
  'about:', 'apollo:', 'chrome:', 'chrome-extension:', 'data:', 'devtools:',
  'electron:', 'file:', 'javascript:', 'node:', 'vscode:',
]);
const LOCALHOST_RE = /((?:https?:\/\/)?(?:localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\])(?::\d{1,5})(?:\/[^\s"'<>]*)?)/ig;
const HOST_PORT_RE = /^(?:[A-Za-z0-9.-]+|\[[0-9A-Fa-f:.]+\]):\d{1,5}(?:[/?#].*)?$/;

// Local history stack is the FALLBACK path only (used when the WS is not
// connected and we drive the agent browser over POST). When the stream is
// live, the SERVER owns history and we just reflect {type:"url"} pushes.
let historyStack = [];
let historyIndex = -1;
let eventsTimer = null;
let lastEventId = 0;
let initialized = false;

// ── WebSocket / screencast state ──
let ws = null;
let wsConnected = false;
let deviceW = 0; // latest screencast device width (for input scaling)
let deviceH = 0;
let frameImg = null; // reused Image for decode (latest-wins)
let frameBusy = false; // true while frameImg is decoding
let pendingFrame = null; // most-recent frame data dropped while busy
let lastMoveSent = 0; // timestamp gate for mousemove throttle
const MOVE_THROTTLE_MS = 33; // ~30/s

const LOCALHOST_OUTPUT_SELECTOR = [
  '.code-runner-output',
  '.doc-run-output',
  '.cookbook-output-pre',
  '.cookbook-output-wrap',
  '.agent-tool-output pre',
  '.task-log-row-body pre',
  '.skills-audit-log',
  '.skill-test-log',
  '[data-terminal-output]',
  '[data-dev-server-output]',
].join(',');
const LOCALHOST_SCAN_MAX_CHARS = 8000;

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
  // When the stream is live the server owns history (buttons stay enabled);
  // otherwise the local fallback stack governs availability.
  const back = el('browser-back');
  const forward = el('browser-forward');
  if (wsConnected) {
    if (back) back.disabled = false;
    if (forward) forward.disabled = false;
    return;
  }
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

function setAddress(url, { record = true } = {}) {
  const address = el('browser-address');
  if (address && document.activeElement !== address) address.value = url;
  if (record) pushHistory(url);
}

// ── WebSocket lifecycle ───────────────────────────────────────────────

function wsUrl() {
  const scheme = location.protocol === 'https:' ? 'wss://' : 'ws://';
  return `${scheme}${location.host}/api/browser/ws`;
}

function wsSend(obj) {
  if (!ws || ws.readyState !== WebSocket.OPEN) return false;
  try {
    ws.send(JSON.stringify(obj));
    return true;
  } catch (_) {
    return false;
  }
}

function connectWs() {
  if (ws && (ws.readyState === WebSocket.CONNECTING || ws.readyState === WebSocket.OPEN)) return;
  let socket;
  try {
    socket = new WebSocket(wsUrl());
  } catch (err) {
    setStatus('Stream unavailable', err.message || String(err));
    return;
  }
  ws = socket;
  setStatus('Connecting to browser stream...');

  socket.onopen = () => {
    if (socket !== ws) return;
    wsConnected = true;
    setStatus('Browser stream connected');
    syncButtons();
  };

  socket.onmessage = (event) => {
    if (socket !== ws) return;
    let msg;
    try {
      msg = JSON.parse(event.data);
    } catch (_) {
      return;
    }
    handleWsMessage(msg);
  };

  socket.onerror = () => {
    if (socket !== ws) return;
    // onclose fires next; surface the disconnect there.
  };

  socket.onclose = () => {
    if (socket !== ws) return;
    wsConnected = false;
    ws = null;
    syncButtons();
    setStatus('Stream disconnected — press reload to reconnect');
  };
}

function closeWs() {
  const socket = ws;
  ws = null;
  wsConnected = false;
  if (socket) {
    try { socket.onclose = null; socket.onerror = null; socket.onmessage = null; } catch (_) {}
    try { socket.close(); } catch (_) {}
  }
}

function handleWsMessage(msg) {
  const kind = msg && msg.type;
  if (kind === 'frame') {
    drawFrame(msg);
  } else if (kind === 'url') {
    const url = msg.url || '';
    if (url) setAddress(url, { record: false });
    setStatus(msg.title || url || 'Loaded');
  } else if (kind === 'error') {
    // Non-fatal stream error; show it without tearing down.
    setStatus('Browser stream', msg.message || 'error');
  }
}

// ── Frame rendering (latest-wins, single reused Image) ────────────────

function drawFrame(msg) {
  const canvas = el('browser-canvas');
  if (!canvas) return;
  const data = msg.data;
  if (!data) return;
  // Track device size for input scaling; guard nulls.
  if (typeof msg.w === 'number' && msg.w > 0) deviceW = msg.w;
  if (typeof msg.h === 'number' && msg.h > 0) deviceH = msg.h;

  if (frameBusy) {
    // Drop all but the most recent frame while the previous decode runs.
    pendingFrame = msg;
    return;
  }
  paint(canvas, data, msg.w, msg.h);
}

function paint(canvas, data, w, h) {
  if (!frameImg) frameImg = new Image();
  const img = frameImg;
  frameBusy = true;
  img.onload = () => {
    frameBusy = false;
    const ctx = canvas.getContext('2d');
    const cw = (typeof w === 'number' && w > 0) ? w : img.naturalWidth;
    const ch = (typeof h === 'number' && h > 0) ? h : img.naturalHeight;
    if (cw && ch) {
      if (canvas.width !== cw) canvas.width = cw;
      if (canvas.height !== ch) canvas.height = ch;
    }
    if (ctx) ctx.drawImage(img, 0, 0);
    // Drain a frame that arrived mid-decode (latest-wins).
    const next = pendingFrame;
    pendingFrame = null;
    if (next) paint(canvas, next.data, next.w, next.h);
  };
  img.onerror = () => {
    frameBusy = false;
    const next = pendingFrame;
    pendingFrame = null;
    if (next) paint(canvas, next.data, next.w, next.h);
  };
  img.src = `data:image/jpeg;base64,${data}`;
}

// ── Input forwarding ──────────────────────────────────────────────────

function buttonName(code) {
  return code === 2 ? 'right' : code === 1 ? 'middle' : 'left';
}

function canvasCoords(e) {
  const canvas = el('browser-canvas');
  if (!canvas) return null;
  const rect = canvas.getBoundingClientRect();
  if (!rect.width || !rect.height) return null;
  const dw = deviceW || canvas.width || rect.width;
  const dh = deviceH || canvas.height || rect.height;
  const x = (e.clientX - rect.left) * (dw / rect.width);
  const y = (e.clientY - rect.top) * (dh / rect.height);
  return { x: Math.round(x), y: Math.round(y) };
}

// Keys we capture so they act on the page instead of scrolling/acting on Apollo.
const CAPTURE_KEYS = new Set([
  ' ', 'Spacebar', 'ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight',
  'Tab', 'Backspace', 'Enter', '/', "'", '"',
]);

function bindCanvasInput() {
  const canvas = el('browser-canvas');
  if (!canvas || canvas._apolloInputBound) return;
  canvas._apolloInputBound = true;

  canvas.addEventListener('mousemove', (e) => {
    const now = (typeof performance !== 'undefined' ? performance.now() : Date.now());
    if (now - lastMoveSent < MOVE_THROTTLE_MS) return;
    lastMoveSent = now;
    const c = canvasCoords(e);
    if (!c) return;
    wsSend({ type: 'mouse', kind: 'move', x: c.x, y: c.y });
  });

  canvas.addEventListener('mousedown', (e) => {
    canvas.focus();
    const c = canvasCoords(e);
    if (!c) return;
    wsSend({ type: 'mouse', kind: 'down', x: c.x, y: c.y, button: buttonName(e.button), clicks: e.detail || 1 });
  });

  canvas.addEventListener('mouseup', (e) => {
    const c = canvasCoords(e);
    if (!c) return;
    wsSend({ type: 'mouse', kind: 'up', x: c.x, y: c.y, button: buttonName(e.button), clicks: e.detail || 1 });
  });

  canvas.addEventListener('contextmenu', (e) => {
    // Let the right-click reach the page, not Apollo's context menu.
    e.preventDefault();
  });

  canvas.addEventListener('wheel', (e) => {
    e.preventDefault();
    const c = canvasCoords(e);
    if (!c) return;
    wsSend({ type: 'mouse', kind: 'wheel', x: c.x, y: c.y, dx: e.deltaX, dy: e.deltaY });
  }, { passive: false });

  canvas.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      // Esc releases keyboard focus rather than forwarding.
      e.preventDefault();
      canvas.blur();
      return;
    }
    // Let host-browser combos through un-forwarded (e.g. Cmd+L/Cmd+R on Mac).
    if (e.metaKey) return;
    if (CAPTURE_KEYS.has(e.key) || e.ctrlKey) e.preventDefault();
    wsSend({ type: 'key', kind: 'down', key: e.key });
  });

  canvas.addEventListener('keyup', (e) => {
    if (e.key === 'Escape') return;
    if (e.metaKey) return;
    if (CAPTURE_KEYS.has(e.key) || e.ctrlKey) e.preventDefault();
    wsSend({ type: 'key', kind: 'up', key: e.key });
  });
}

// ── Agent-browser POST fallback (no live stream) ──────────────────────

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
      setStatus('Agent browser unavailable', text.slice(0, 180));
      return;
    }
    const data = await res.json();
    setStatus(data.title || 'Loaded', data.warning === 'non_secure_http' ? 'Non-secure HTTP' : '');
  } catch (err) {
    setStatus('Agent browser unavailable', err.message || String(err));
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
  if (wsConnected && wsSend({ type: 'navigate', url })) {
    setAddress(url, { record: false });
    return;
  }
  // Fallback: drive the agent browser over POST and track local history.
  setAddress(url, options);
  await syncAgentBrowser(url);
}

function goBack() {
  if (wsConnected) {
    wsSend({ type: 'back' });
    return;
  }
  if (historyIndex <= 0) return;
  historyIndex -= 1;
  const url = historyStack[historyIndex];
  setAddress(url, { record: false });
  syncAgentBrowser(url);
  syncButtons();
}

function goForward() {
  if (wsConnected) {
    wsSend({ type: 'forward' });
    return;
  }
  if (historyIndex < 0 || historyIndex >= historyStack.length - 1) return;
  historyIndex += 1;
  const url = historyStack[historyIndex];
  setAddress(url, { record: false });
  syncAgentBrowser(url);
  syncButtons();
}

function reload() {
  if (wsConnected) {
    setStatus('Reloading...');
    wsSend({ type: 'reload' });
    return;
  }
  // Not connected: a reload click re-establishes the stream (single retry).
  connectWs();
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
  bindCanvasInput();
  connectWs();
  startEventPolling();
  if (url) navigate(url);
  else el('browser-address')?.focus();
}

function close() {
  const modal = el('browser-modal');
  if (modal) modal.classList.add('hidden');
  closeWs();
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
  const observedOutputs = new WeakSet();
  let pendingScan = new Set();
  let scanTimer = null;

  const scanOutput = (node) => {
    if (!node || !node.textContent) return;
    const text = String(node.textContent).slice(-LOCALHOST_SCAN_MAX_CHARS);
    if (!/(localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\])/i.test(text)) return;
    detectLocalhost(text).forEach((url) => {
      if (seen.has(url)) return;
      seen.add(url);
      if (window.uiModule?.showToast) {
        window.uiModule.showToast(`Open ${url} from the Browser panel`);
      }
    });
  };

  const scheduleScan = (node) => {
    if (!node) return;
    pendingScan.add(node);
    if (scanTimer) return;
    scanTimer = window.setTimeout(() => {
      const allPending = Array.from(pendingScan);
      const batch = allPending.slice(0, 20);
      const remaining = allPending.slice(20);
      pendingScan = new Set();
      scanTimer = null;
      batch.forEach(scanOutput);
      remaining.forEach(scheduleScan);
    }, 250);
  };

  const watchOutput = (node) => {
    if (!node || observedOutputs.has(node)) return;
    observedOutputs.add(node);
    scheduleScan(node);
    new MutationObserver(() => scheduleScan(node)).observe(node, {
      childList: true,
      characterData: true,
      subtree: true,
    });
  };

  const collectOutputs = (node) => {
    if (!node || node.nodeType !== Node.ELEMENT_NODE) return [];
    const outputs = [];
    if (node.matches?.(LOCALHOST_OUTPUT_SELECTOR)) outputs.push(node);
    node.querySelectorAll?.(LOCALHOST_OUTPUT_SELECTOR).forEach((child) => outputs.push(child));
    return outputs;
  };

  document.querySelectorAll(LOCALHOST_OUTPUT_SELECTOR).forEach(watchOutput);
  const observer = new MutationObserver((mutations) => {
    for (const mutation of mutations) {
      for (const node of mutation.addedNodes || []) {
        collectOutputs(node).forEach(watchOutput);
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
    const url = el('browser-address')?.value;
    if (url) window.open(url, '_blank', 'noopener,noreferrer');
  });
  el('close-browser-modal')?.addEventListener('click', close);
  bindCanvasInput();
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
