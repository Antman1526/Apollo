import assert from 'node:assert/strict';
import test from 'node:test';

const diff = await import('../static/js/document/diff.js');
const exports = await import('../static/js/document/export.js');
const suggestions = await import('../static/js/document/suggestions.js');
const state = await import('../static/js/document/state.js');
const versions = await import('../static/js/document/versionHistory.js');

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

test('document state helpers reuse drafts and derive conservative titles', () => {
  const docs = new Map([
    ['draft', { title: 'Untitled', content: '', sessionId: 'session-1' }],
    ['other', { title: 'Report', content: 'saved', sessionId: 'session-2' }],
  ]);
  assert.equal(state.findReusableDocumentId(docs, { title: 'Anything' }, 'session-1'), 'draft');
  assert.equal(state.deriveDocumentTitle('# Weekly report\nDetails'), 'Weekly report');
  assert.equal(state.deriveDocumentTitle('x'), null);
  assert.deepEqual(state.mergeDocumentUpdate(null, { doc_id: 'new', content: 'text' }, 'session-1'), {
    id: 'new', title: '', language: '', content: 'text', version: 1, sessionId: 'session-1',
  });
});

test('export metadata and HTML escaping stay deterministic', () => {
  assert.deepEqual(exports.getExportMetadata({ title: 'A/B', version: 2, language: 'json' }), {
    baseName: 'A_B_v2', extension: '.json', mime: 'application/json',
  });
  assert.equal(exports.escapeDocumentHtml('<script>'), '&lt;script&gt;');
  assert.match(exports.renderExportHtml({ title: '<unsafe>', body: '<p>safe</p>' }), /<title>&lt;unsafe&gt;<\/title>/);
});

test('suggestion persistence ignores malformed values and duplicate ids', () => {
  assert.deepEqual(suggestions.parseStoredSuggestions('{broken'), []);
  const current = [{ id: 'one', find: 'a', replace: 'b', reason: 'first' }];
  assert.deepEqual(suggestions.appendUniqueSuggestions(current, [
    { id: 'one', find: 'ignored' }, { id: 'two', find: 'c', replace: 'd', reason: 'second' },
  ]), [{ id: 'two', find: 'c', replace: 'd', reason: 'second', cardEl: null }]);
  assert.deepEqual(suggestions.serializeSuggestions(current), current);
});

test('version history summary escapes changed source lines', () => {
  const summary = versions.buildVersionDiffSummary('<old>', '<new>');
  assert.match(summary, /&lt;old&gt;/);
  assert.match(summary, /&lt;new&gt;/);
});
