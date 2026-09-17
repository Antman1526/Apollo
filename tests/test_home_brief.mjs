import assert from 'node:assert/strict';
import test from 'node:test';

import { buildChecklist, buildBrief, buildDayPrompt } from '../static/js/homeBrief.js';

const NOW = new Date('2026-09-11T12:00:00');

test('buildChecklist maps setup state into three rows with actions', () => {
  const rows = buildChecklist({
    models: { ready: true, endpoints: 2, warm_model: 'llama-3-8b' },
    search: { ready: false, state: 'off', detail: 'SearXNG not installed' },
    email: { ready: false, accounts: 0 },
  });
  assert.deepEqual(rows.map((r) => r.key), ['models', 'search', 'email']);
  assert.equal(rows[0].ready, true);
  assert.match(rows[0].detail, /2 endpoints connected/);
  assert.match(rows[0].detail, /llama-3-8b warm/);
  assert.equal(rows[0].action, 'setup');
  assert.equal(rows[1].ready, false);
  assert.equal(rows[1].detail, 'SearXNG not installed');
  assert.equal(rows[1].action, 'settings-search');
  assert.equal(rows[2].ready, false);
  assert.equal(rows[2].detail, 'No mailbox connected');
  assert.equal(rows[2].action, 'email-add');
});

test('buildChecklist tolerates a missing/partial setup block', () => {
  const rows = buildChecklist(undefined);
  assert.equal(rows.length, 3);
  assert.ok(rows.every((r) => r.ready === false));
  assert.equal(rows[0].detail, 'No model connected yet');
  assert.equal(rows[1].detail, 'Search status unknown');
  const fallback = buildChecklist({ search: { ready: true, state: 'fallback' } });
  assert.equal(fallback[1].detail, 'Using a fallback search provider');
});

test('buildBrief builds Today / Due / Inbox cards, overdue first, capped at six', () => {
  const tasks = [];
  for (let i = 0; i < 5; i++) {
    tasks.push({ id: `t${i}`, title: `Task ${i}`, due: new Date(NOW.getTime() + (i + 1) * 3600e3).toISOString(), status: 'due' });
  }
  const built = buildBrief({
    calendar_today: [
      { id: 'a', title: 'Standup', start: '2026-09-11T09:30:00', end: '2026-09-11T09:45:00', all_day: false, calendar: 'Work' },
      { id: 'b', title: 'Holiday', start: '2026-09-11', end: '2026-09-12', all_day: true, calendar: '' },
    ],
    tasks_due: [
      { id: 'late', title: 'Overdue task', due: '2026-09-10T08:00:00', status: 'overdue' },
      ...tasks,
    ],
    notes_due: [
      { id: 'n1', title: 'Renew passport', due_date: '2026-09-11T18:00' },
    ],
    email: { unread: 4, urgent: 1 },
    warm_model: 'llama-3-8b',
  }, { now: NOW });

  assert.equal(built.empty, false);
  assert.deepEqual(built.sections.map((s) => s.key), ['today', 'due', 'inbox']);

  const today = built.sections[0];
  assert.equal(today.title, 'Today');
  assert.equal(today.items[0].text, 'Standup');
  assert.match(today.items[0].sub, /Work/);
  assert.equal(today.items[1].sub, 'All day');

  const due = built.sections[1];
  assert.equal(due.items.length, 6, 'due card is capped at six rows');
  assert.equal(due.items[0].text, 'Overdue task');
  assert.match(due.items[0].sub, /^Overdue/);
  assert.match(due.items[0].sub, /Task$/);
  assert.ok(due.items.every((i) => i.text !== 'Renew passport' || /Note$/.test(i.sub)));

  const inbox = built.sections[2];
  assert.equal(inbox.items[0].text, '4 unread messages');
  assert.equal(inbox.items[0].sub, '1 urgent');
});

test('buildBrief omits empty cards and reports empty when nothing is due', () => {
  const built = buildBrief({ calendar_today: [], tasks_due: [], notes_due: [], email: null }, { now: NOW });
  assert.equal(built.empty, true);
  assert.deepEqual(built.sections, []);

  const noInbox = buildBrief({ calendar_today: [], tasks_due: [], notes_due: [], email: { unread: 0, urgent: 0 } }, { now: NOW });
  assert.equal(noInbox.empty, true, 'inbox card is omitted when there is nothing unread');

  const noEmail = buildBrief({ tasks_due: [{ id: 't', title: 'Only task', due: '2026-09-11T15:00:00' }] }, { now: NOW });
  assert.deepEqual(noEmail.sections.map((s) => s.key), ['due']);
  assert.equal(noEmail.sections[0].items[0].text, 'Only task');
});

test('buildDayPrompt summarizes the brief and ends with a question', () => {
  const prompt = buildDayPrompt({
    calendar_today: [{ title: 'Dentist', start: '2026-09-11T14:00:00', all_day: false }],
    tasks_due: [{ title: 'Ship release', due: '2026-09-11T10:00:00', status: 'overdue' }],
    notes_due: [{ title: 'Call mom', due_date: '2026-09-11T18:00' }],
    email: { unread: 2, urgent: 0 },
  });
  assert.match(prompt, /^Here's my day so far:/);
  assert.match(prompt, /Calendar:\n- Dentist \(/);
  assert.match(prompt, /- Ship release \(overdue\)/);
  assert.match(prompt, /- Call mom \(note\)/);
  assert.match(prompt, /Inbox: 2 unread\./);
  assert.match(prompt, /What should I focus on first/);

  const quiet = buildDayPrompt({});
  assert.match(quiet, /Nothing scheduled or due\./);
  assert.match(quiet, /What should I focus on first/);
});

// ── render(): brief mode yields to the Today briefing card ─────────────

function fakeEl() {
  const el = {
    children: [], attrs: {}, className: '', textContent: '', hidden: false,
    appendChild(c) { el.children.push(c); return c; },
    replaceChildren() { el.children = []; },
    setAttribute(k, v) { el.attrs[k] = v; },
    addEventListener() {},
    classList: {
      set: new Set(),
      add(c) { this.set.add(c); },
      remove(c) { this.set.delete(c); },
      toggle(c, on) { on ? this.set.add(c) : this.set.delete(c); return on; },
      contains(c) { return this.set.has(c); },
    },
  };
  return el;
}

test('render skips the brief when the Today card is mounted, but still shows the checklist', async (t) => {
  global.document = { createElement: fakeEl, createTextNode: (s) => ({ text: s }), getElementById: () => null };
  t.after(() => { delete global.document; });
  const { render } = await import('../static/js/homeBrief.js');
  const ready = { models: { ready: true, endpoints: 1 }, search: { ready: true, state: 'sidecar' }, email: { ready: true, accounts: 1 } };
  const data = { setup: ready, brief: { calendar_today: [], tasks_due: [], notes_due: [], email: null } };

  const root = fakeEl();
  render(root, data, { incognito: false, visible: true, dismissed: false, todayCard: true });
  assert.equal(root.hidden, true);
  assert.equal(root.children.length, 0);

  const root2 = fakeEl();
  render(root2, data, { incognito: false, visible: true, dismissed: false, todayCard: false });
  assert.equal(root2.hidden, false);
  assert.ok(root2.classList.contains('is-brief'));

  const notReady = { ...ready, email: { ready: false, accounts: 0 } };
  const root3 = fakeEl();
  render(root3, { setup: notReady, brief: {} }, { incognito: false, visible: true, dismissed: false, todayCard: true });
  assert.equal(root3.hidden, false);
  assert.ok(root3.classList.contains('is-checklist'));
});
