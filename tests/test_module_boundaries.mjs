import assert from 'node:assert/strict';
import test from 'node:test';

const stateModule = await import('../static/js/emailLibrary/state.js');
const recipients = await import('../static/js/emailLibrary/replyRecipients.js');

test('email library state module exposes one mutable state owner', () => {
  assert.ok(stateModule.state);
  assert.equal(stateModule.state._libOpen, false);
  assert.ok(stateModule.state._selectedUids instanceof Set);
});

test('reply recipient helpers import without browser globals', () => {
  assert.equal(recipients.extractEmail('Ada <ada@example.com>'), 'ada@example.com');
  assert.equal(
    recipients.buildReplyAllCc(
      { to: 'Ada <ada@example.com>, Ben <ben@example.com>', cc: 'Cam <cam@example.com>' },
      'ada@example.com',
    ),
    'Ben <ben@example.com>, Cam <cam@example.com>',
  );
});
