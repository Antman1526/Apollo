import assert from 'node:assert/strict';
import test from 'node:test';

const attachments = await import('../static/js/emailLibrary/attachments.js');
const readers = await import('../static/js/emailLibrary/readerWindows.js');

test('attachment helpers hide signature artwork but retain user documents', () => {
  assert.equal(attachments.isLikelySignatureImage({ filename: 'logo.png', size: 80_000 }), true);
  assert.equal(attachments.isLikelySignatureImage({ filename: 'report.pdf', size: 100 }), false);
  const markup = attachments.buildAttachmentsHtml('uid-1', {
    attachments: [
      { filename: 'logo.png', size: 200 },
      { filename: 'brief.pdf', size: 2048, index: 3 },
    ],
  });
  assert.match(markup, /brief\.pdf/);
  assert.doesNotMatch(markup, /logo\.png/);
  assert.match(markup, /data-open-index="3"/);
});

test('reader slots are stable and reuse the first freed position', () => {
  readers.resetReaderSlotsForTest();
  assert.equal(readers.allocReaderSlot('one'), 1);
  assert.equal(readers.allocReaderSlot('two'), 2);
  assert.equal(readers.allocReaderSlot('one'), 1);
  readers.freeReaderSlot('one');
  assert.equal(readers.allocReaderSlot('three'), 1);
});
