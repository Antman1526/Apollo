import assert from 'node:assert/strict';
import test from 'node:test';

import { renderSystemStatusCardHTML } from '../static/js/systemStatusCard.js';

test('renders expanded system status components with next steps', () => {
  const html = renderSystemStatusCardHTML({
    ok: false,
    ready_count: 8,
    total: 10,
    components: {
      storage: { ready: true, state: 'ready', summary: 'Storage ok' },
      auth: { ready: true, state: 'ready', summary: 'Auth ok' },
      memory: { ready: true, state: 'ready', summary: 'Memory ok' },
      email: { ready: true, state: 'idle', summary: 'No email' },
      documents: { ready: true, state: 'ready', summary: 'Docs ok' },
      models: { ready: false, state: 'degraded', summary: 'No cache', next_step: 'Refresh model endpoint caches' },
      search: { ready: true, state: 'ready', summary: 'Search ok' },
      tool_servers: { ready: true, state: 'ready', summary: 'Tools ok' },
      terminal: { ready: true, state: 'ready', summary: 'Terminal ok' },
      background: { ready: false, state: 'degraded', summary: 'Stuck run', next_step: 'Inspect stuck task runs' },
    },
  });

  assert.match(html, /system-status-card/);
  assert.match(html, /8\/10 systems ready/);
  assert.match(html, /Auth/);
  assert.match(html, /Email/);
  assert.match(html, /Documents/);
  assert.match(html, /Models/);
  assert.match(html, /Search/);
  assert.match(html, /Refresh model endpoint caches/);
  assert.match(html, /Inspect stuck task runs/);
});

test('escapes component summaries and next steps', () => {
  const html = renderSystemStatusCardHTML({
    ok: false,
    ready_count: 0,
    total: 1,
    components: {
      models: {
        ready: false,
        state: 'degraded',
        summary: '"bad" <script>alert(1)</script>',
        next_step: '<img src=x onerror=alert(1)>',
      },
    },
  });

  assert.doesNotMatch(html, /<script>/);
  assert.doesNotMatch(html, /<img/);
  assert.match(html, /&lt;script&gt;/);
  assert.match(html, /&lt;img/);
});
