import assert from 'node:assert/strict';
import test from 'node:test';

import {
  shortModel, modelColor, getModelInfo, getModelCost,
  isLocalEndpoint, getImageCost, stripToolBlocks, MODEL_INFO,
} from '../static/js/modelMeta.js';

// ── shortModel ──
test('shortModel strips org prefix', () => {
  assert.equal(shortModel('anthropic/claude-sonnet-4-5'), 'claude-sonnet-4-5');
});
test('shortModel strips .gguf + quant suffix', () => {
  assert.equal(shortModel('Qwen2.5-7B-Q4_K_M.gguf'), 'Qwen2.5-7B');
});
test('shortModel handles empty / non-string', () => {
  assert.equal(shortModel(''), '...');
  assert.equal(shortModel(null), '...');
  assert.equal(shortModel(12345), '12345');
});
test('shortModel truncates long names at a size boundary', () => {
  // >25 chars: prefers cutting at a model-size token (e.g. -35B) over a hard …
  assert.equal(shortModel('some-really-long-fine-tune-name-35B-extra-tag-here'),
    'some-really-long-fine-tune-name-35B');
  // No size token → hard truncate to 22 chars + ellipsis
  const hard = shortModel('abcdefghijklmnopqrstuvwxyz-no-size-token-here');
  assert.ok(hard.endsWith('…'));
  assert.equal(hard.length, 23);
});

// ── modelColor ──
test('modelColor is deterministic and hsl', () => {
  const a = modelColor('gpt-4o');
  assert.equal(a, modelColor('GPT-4O'), 'case-insensitive');
  assert.match(a, /^hsl\(\d+, 55%, 65%\)$/);
});
test('modelColor null for empty', () => {
  assert.equal(modelColor(''), null);
});

// ── getModelInfo ──
test('getModelInfo substring match returns key + pricing', () => {
  const info = getModelInfo('anthropic/claude-3-haiku-20240307');
  assert.equal(info.key, 'claude-3-haiku');
  assert.equal(info.input, 0.25);
  assert.equal(info.ctx, 200000);
});
test('getModelInfo matches first table entry on collision (order-dependent)', () => {
  // Substring match walks the table in order: "gpt-4o" precedes "gpt-4o-mini",
  // so a mini id resolves to gpt-4o. Documenting the (preserved) behavior.
  assert.equal(getModelInfo('gpt-4o-mini').key, 'gpt-4o');
});
test('getModelInfo null for unknown / empty', () => {
  assert.equal(getModelInfo('totally-unknown-model'), null);
  assert.equal(getModelInfo(''), null);
});

// ── getModelCost ──
test('getModelCost computes blended per-token cost', () => {
  // gpt-4o: input 2.50, output 10.00 per 1M
  const cost = getModelCost('gpt-4o', 1_000_000, 1_000_000);
  assert.equal(cost, 12.5);
});
test('getModelCost null for unknown', () => {
  assert.equal(getModelCost('mystery', 100, 100), null);
  assert.equal(getModelCost('', 100, 100), null);
});

// ── isLocalEndpoint ──
test('isLocalEndpoint true for loopback / LAN / single-label / tailscale', () => {
  for (const u of [
    'http://localhost:8080', 'http://127.0.0.1:1234', 'http://192.168.1.5:8000',
    'http://10.0.0.9', 'http://172.16.0.1', 'http://llamaswap:8080',
    'http://box.local', 'http://100.100.5.5:11434',
  ]) assert.equal(isLocalEndpoint(u), true, u);
});
test('isLocalEndpoint false for public FQDN', () => {
  assert.equal(isLocalEndpoint('https://api.openai.com/v1'), false);
  assert.equal(isLocalEndpoint('https://openrouter.ai/api/v1'), false);
});
test('isLocalEndpoint true for missing / garbage (bias to free)', () => {
  assert.equal(isLocalEndpoint(''), true);
  assert.equal(isLocalEndpoint('not a url'), true);
});

// ── getImageCost ──
test('getImageCost looks up by model x quality x size', () => {
  assert.equal(getImageCost('gpt-image-1', 'high', '1024x1024'), 0.167);
  // falls back to 1024x1024 for unknown size
  assert.equal(getImageCost('gpt-image-1', 'low', '999x999'), 0.011);
  // defaults quality to medium
  assert.equal(getImageCost('gpt-image-1', null, '1024x1024'), 0.042);
});
test('getImageCost null for unknown model', () => {
  assert.equal(getImageCost('dall-e-2', 'high', '1024x1024'), null);
  assert.equal(getImageCost('', 'high', '1024x1024'), null);
});

// ── stripToolBlocks ──
test('stripToolBlocks removes TOOL_CALL blocks', () => {
  assert.equal(stripToolBlocks('hello [TOOL_CALL]x[/TOOL_CALL] world'), 'hello  world');
});
test('stripToolBlocks removes XML invoke + collapses blank lines', () => {
  const out = stripToolBlocks('a\n\n\n\nb <invoke name="foo">stuff</invoke>');
  assert.ok(!out.includes('invoke'));
  assert.ok(!/\n{3,}/.test(out));
});
test('stripToolBlocks leaves plain prose untouched', () => {
  assert.equal(stripToolBlocks('  just text  '), 'just text');
});

// ── sanity: table is populated ──
test('MODEL_INFO has expected providers', () => {
  assert.ok(MODEL_INFO['gpt-4o']);
  assert.ok(MODEL_INFO['claude-opus-4']);
  assert.ok(MODEL_INFO['gemini-2.5-pro']);
});
