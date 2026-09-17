import test from 'node:test';
import assert from 'node:assert/strict';
import { renderPulseHTML } from '../static/js/systemPulse.js';

test('all ready collapses to one dot', () => {
  const html = renderPulseHTML({ ok: true, ready_count: 3, total: 3, components: {
    storage: { label: 'Storage', ready: true, state: 'ready', summary: '' },
    memory: { label: 'Memory', ready: true, state: 'ready', summary: '' },
    search: { label: 'Search', ready: true, state: 'ready', summary: '' } } });
  assert.match(html, /pulse-dot--ready/);
  assert.doesNotMatch(html, /pulse-chip/);
});

test('degraded components render as labelled chips', () => {
  const html = renderPulseHTML({ ok: false, ready_count: 1, total: 2, components: {
    storage: { label: 'Storage', ready: true, state: 'ready', summary: '' },
    search: { label: 'Search', ready: false, state: 'degraded', summary: 'SearXNG down' } } });
  assert.match(html, /pulse-chip--degraded/);
  assert.match(html, /Search/);
  assert.match(html, /SearXNG down/);
});

test('null status renders nothing', () => { assert.equal(renderPulseHTML(null), ''); });

test('idle and stopped states are informational, not alarming', () => {
  const html = renderPulseHTML({ ok: true, ready_count: 1, total: 2, components: {
    storage: { label: 'Storage', ready: true, state: 'ready', summary: '' },
    background: { label: 'Background', ready: false, state: 'idle', summary: 'No tasks scheduled' } } });
  assert.match(html, /pulse-chip--idle/);
  assert.match(html, /pulse-chip--info/);
});
