import test from 'node:test';
import assert from 'node:assert/strict';
import { renderBriefingHTML, formatBriefingTime } from '../static/js/briefing.js';

const BASE = { date: '2026-09-16', emails: [], events: [], notes: [], tasks: [], counts: { emails: 0, events: 0, notes: 0, tasks: 0 } };

test('renderBriefingHTML renders the empty state when everything is empty', () => {
  const html = renderBriefingHTML(BASE);
  assert.ok(html.includes('briefing-empty'));
  assert.ok(html.includes('Nothing on your plate'));
  assert.ok(!html.includes('briefing-section-label'));
});

test('renderBriefingHTML renders a Mail section only when emails is non-empty', () => {
  const html = renderBriefingHTML({ ...BASE, emails: [{ uid: '1', from: 'a@x', subject: 'Hi' }] });
  assert.ok(html.includes('>Mail<'));
  assert.ok(!html.includes('>Today<'));
  assert.ok(!html.includes('>Notes<'));
  assert.ok(!html.includes('>Tasks<'));
});

test('renderBriefingHTML escapes a hostile email subject', () => {
  const html = renderBriefingHTML({ ...BASE, emails: [{ uid: '1', from: 'a@x', subject: '<b>x</b>' }] });
  assert.ok(!html.includes('<b>x</b>'));
  assert.ok(html.includes('&lt;b&gt;x&lt;/b&gt;'));
});

test('renderBriefingHTML renders Today section with all-day and timed events', () => {
  const html = renderBriefingHTML({
    ...BASE,
    events: [
      { uid: 'e1', summary: 'Standup', dtstart: '2026-09-16T10:00:00+00:00', all_day: false },
      { uid: 'e2', summary: 'Holiday', dtstart: '2026-09-16', dtend: '2026-09-17', all_day: true },
    ],
  });
  assert.ok(html.includes('>Today<'));
  assert.ok(html.includes('all day'));
  assert.ok(html.includes('Standup'));
  assert.ok(html.includes('Holiday'));
});

test('renderBriefingHTML renders Notes with open checklist items', () => {
  const html = renderBriefingHTML({
    ...BASE,
    notes: [{ id: 'n1', title: 'Ship v1', note_type: 'checklist', pinned: true, open_items: ['Write changelog'] }],
  });
  assert.ok(html.includes('>Notes<'));
  assert.ok(html.includes('Ship v1'));
  assert.ok(html.includes('briefing-subitem'));
  assert.ok(html.includes('Write changelog'));
});

test('renderBriefingHTML renders Tasks section', () => {
  const html = renderBriefingHTML({ ...BASE, tasks: [{ id: 't1', name: 'Nightly backup', next_run: '2026-09-16T23:00:00+00:00' }] });
  assert.ok(html.includes('>Tasks<'));
  assert.ok(html.includes('Nightly backup'));
});

test('renderBriefingHTML shows count pills for non-zero sources', () => {
  const html = renderBriefingHTML({
    ...BASE,
    emails: [{ uid: '1' }], events: [{ uid: 'e1' }],
    counts: { emails: 1, events: 1, notes: 0, tasks: 0 },
  });
  assert.ok(html.includes('briefing-pill'));
  assert.ok(html.includes('1 Mail'));
  assert.ok(html.includes('1 Events'));
  assert.ok(!html.includes('0 Notes'));
  assert.ok(!html.includes('0 Tasks'));
});

test('renderBriefingHTML renders a summary paragraph when set', () => {
  const html = renderBriefingHTML({ ...BASE, summary: 'You have one thing to do today.' });
  assert.ok(html.includes('briefing-summary'));
  assert.ok(html.includes('You have one thing to do today.'));
});

test('renderBriefingHTML has no summary paragraph when unset', () => {
  const html = renderBriefingHTML(BASE);
  assert.ok(!html.includes('briefing-summary'));
});

test('renderBriefingHTML renders warnings when a source failed', () => {
  const html = renderBriefingHTML({ ...BASE, warnings: ['email: unavailable'] });
  assert.ok(html.includes('briefing-warnings'));
  assert.ok(html.includes('email: unavailable'));
});

test('renderBriefingHTML renders the header with a formatted date', () => {
  const html = renderBriefingHTML(BASE);
  assert.ok(html.includes('Today ·'));
  assert.ok(html.includes('September'));
});

test('renderBriefingHTML emits an accessible toggle button and a hidden, controlled body', () => {
  const html = renderBriefingHTML(BASE);
  assert.match(html, /<button[^>]*class="briefing-header"[^>]*aria-expanded="false"[^>]*aria-controls="briefing-body"[^>]*>/);
  assert.match(html, /<div[^>]*class="briefing-body"[^>]*id="briefing-body"[^>]* hidden[^>]*>/);
});

test('formatBriefingTime formats a timed ISO datetime', () => {
  const out = formatBriefingTime('2026-09-16T14:30:00+00:00');
  assert.ok(out.length > 0);
  assert.ok(/\d/.test(out));
});

test('formatBriefingTime returns empty string for a date-only string', () => {
  assert.equal(formatBriefingTime('2026-09-16'), '');
});

test('formatBriefingTime returns empty string for missing/invalid input', () => {
  assert.equal(formatBriefingTime(null), '');
  assert.equal(formatBriefingTime(undefined), '');
  assert.equal(formatBriefingTime('not-a-date-but-long-enough'), '');
});
