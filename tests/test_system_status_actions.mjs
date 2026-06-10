import assert from 'node:assert/strict';
import test from 'node:test';

import { wireSystemStatusActions } from '../static/js/systemStatusActions.js';

function fakeButton(dataset = {}) {
  return {
    dataset,
    disabled: false,
    textContent: 'Repair',
    listeners: {},
    addEventListener(type, handler) {
      this.listeners[type] = handler;
    },
    async click() {
      let stopped = false;
      await this.listeners.click({
        stopPropagation() {
          stopped = true;
        },
      });
      return stopped;
    },
  };
}

test('runs confirmed system status action and refreshes the list', async () => {
  const btn = fakeButton({
    systemActionEndpoint: '/api/system/actions/memory.rebuild_semantic_index',
    systemActionMethod: 'POST',
    systemActionConfirm: 'Rebuild?',
  });
  const calls = [];
  let refreshed = false;
  let toasted = false;

  wireSystemStatusActions({
    querySelectorAll(selector) {
      assert.equal(selector, '.system-status-action');
      return [btn];
    },
  }, {
    confirmImpl: async (message, options) => {
      calls.push(['confirm', message, options.confirmText]);
      return true;
    },
    fetchImpl: async (url, options) => {
      calls.push(['fetch', url, options.method, options.credentials]);
      return { ok: true };
    },
    rerender: async () => {
      refreshed = true;
    },
    showToast: () => {
      toasted = true;
    },
  });

  assert.equal(await btn.click(), true);

  assert.deepEqual(calls, [
    ['confirm', 'Rebuild?', 'Repair'],
    ['fetch', '/api/system/actions/memory.rebuild_semantic_index', 'POST', 'same-origin'],
  ]);
  assert.equal(refreshed, true);
  assert.equal(toasted, true);
});

test('restores button and reports error when system status action fails', async () => {
  const btn = fakeButton({
    systemActionEndpoint: '/api/system/actions/tool_servers.reconnect_failed',
    systemActionMethod: 'POST',
  });
  let errorMessage = '';

  wireSystemStatusActions({
    querySelectorAll() {
      return [btn];
    },
  }, {
    fetchImpl: async () => ({
      ok: false,
      status: 409,
      async json() {
        return { detail: 'Unavailable' };
      },
    }),
    showError: (message) => {
      errorMessage = message;
    },
  });

  await btn.click();

  assert.equal(btn.disabled, false);
  assert.equal(btn.textContent, 'Repair');
  assert.equal(errorMessage, 'Unavailable');
});
