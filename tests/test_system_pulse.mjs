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

test('a ready:false component is always an alert chip, ready:true renders nothing', () => {
  const html = renderPulseHTML({ ok: false, ready_count: 1, total: 2, components: {
    storage: { label: 'Storage', ready: true, state: 'idle', summary: 'Idle, but ready' },
    background: { label: 'Background', ready: false, state: 'stopped', summary: 'Scheduler loop dead' } } });
  assert.doesNotMatch(html, /pulse-chip--idle/);
  assert.match(html, /pulse-chip--stopped/);
  assert.match(html, /pulse-chip--alert/);
});

test('long summaries are truncated for display but kept in full in the title', () => {
  const longSummary = 'x'.repeat(100);
  const html = renderPulseHTML({ ok: false, ready_count: 0, total: 1, components: {
    search: { label: 'Search', ready: false, state: 'degraded', summary: longSummary } } });
  const truncated = `${'x'.repeat(47)}…`;
  assert.match(html, new RegExp(`title="${longSummary}"`));
  assert.match(html, new RegExp(`<span class="pulse-chip-summary">${truncated}</span>`));
});

test('more than four non-ready components cap at four chips plus an overflow badge', () => {
  const components = {};
  ['a', 'b', 'c', 'd', 'e'].forEach((key, i) => {
    components[key] = { label: `Comp${i}`, ready: false, state: 'degraded', summary: '' };
  });
  const html = renderPulseHTML({ ok: false, ready_count: 0, total: 5, components });
  const chipCount = (html.match(/class="pulse-chip pulse-chip--/g) || []).length;
  assert.equal(chipCount, 4);
  assert.match(html, /pulse-more">\+1</);
});
