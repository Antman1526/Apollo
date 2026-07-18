import assert from 'node:assert/strict';
import test from 'node:test';

const diff = await import('../static/js/document/diff.js');

test('document diff module exposes a stable line review model', () => {
  const entries = diff.computeLineDiff('one\ntwo', 'one\nthree');
  assert.deepEqual(entries, [
    { type: 'equal', line: 'one' },
    { type: 'delete', line: 'two' },
    { type: 'insert', line: 'three' },
  ]);
  const [chunk] = diff.buildDiffChunks(entries);
  assert.deepEqual(chunk.oldLines, ['two']);
  assert.deepEqual(chunk.newLines, ['three']);
  assert.equal(chunk.resolved, false);
});

test('document diff helpers remain DOM-free and preserve streaming semantics', () => {
  assert.deepEqual(diff.simpleDiff('start-old-end', 'start-new-end'), {
    prefixLen: 6, oldMid: 'old', newMid: 'new',
  });
  assert.deepEqual(diff.lineDiff('a\nb', 'a\nc'), [
    { type: 'same', text: 'a' },
    { type: 'add', text: 'c' },
    { type: 'del', text: 'b' },
  ]);
});
