import assert from 'node:assert/strict';
import test from 'node:test';

const drafts = await import('../static/js/notes/drafts.js');

test('note draft helpers preserve note, checklist, and goal content', () => {
  const values = new Map();
  const storage = { getItem: (key) => values.get(key) || null, removeItem: (key) => values.delete(key) };
  values.set('apollo-note-draft-n1', JSON.stringify({ title: '', items: [{ text: 'Ship it' }] }));
  assert.equal(drafts.draftKey('n1'), 'apollo-note-draft-n1');
  assert.equal(drafts.isDraftEmpty(drafts.loadDraft(storage, 'n1')), false);
  drafts.clearDraft(storage, 'n1');
  assert.equal(drafts.loadDraft(storage, 'n1'), null);
  assert.equal(drafts.isDraftEmpty({ title: '', content: '', items: [] }), true);
});

test('note draft debounce cancels the prior scheduled save', () => {
  let cleared = null;
  let scheduled = null;
  const next = drafts.scheduleDraftSave({
    timer: 7, clearTimer: (id) => { cleared = id; }, setTimer: (fn, delay) => { scheduled = { fn, delay }; return 8; }, save: () => {},
  });
  assert.equal(cleared, 7);
  assert.equal(next, 8);
  assert.equal(scheduled.delay, 600);
});
