import assert from 'node:assert/strict';
import test from 'node:test';

const models = await import('../static/js/settings/models.js');

test('settings model policy labels endpoint availability and filters chat models', () => {
  assert.equal(models.endpointLabel({ name: 'Local', online: false }), 'Local (offline)');
  assert.deepEqual(models.selectableModels(['embed', 'chat', 'unsupported'], {
    embed: { kind: 'embedding' }, unsupported: { kind: 'unsupported' },
  }, { chatOnly: true }), [
    { id: 'chat', label: 'chat', disabled: false, kind: '' },
    { id: 'unsupported', label: 'unsupported', disabled: true, kind: 'unsupported' },
  ]);
});
