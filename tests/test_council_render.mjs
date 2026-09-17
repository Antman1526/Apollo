import test from 'node:test';
import assert from 'node:assert/strict';
import { pickCouncilMembers, renderCouncilHTML } from '../static/js/council.js';

const isChatCapable = () => true;

const items = [
  {
    url: 'http://a', endpoint_name: 'Endpoint A',
    models: ['model-a1', 'model-a2'], models_display: ['Model A1', 'Model A2'],
  },
  {
    url: 'http://b', endpoint_name: 'Endpoint B',
    models: ['model-b1'], models_display: ['Model B1'],
  },
  {
    url: 'http://c', endpoint_name: 'Endpoint C',
    models: ['model-c1'], models_display: ['Model C1'],
  },
  {
    url: 'http://img', endpoint_name: 'Image Endpoint', model_type: 'image',
    models: ['dall-e-3'], models_display: ['DALL-E 3'],
  },
];

test('pickCouncilMembers puts the current model first', () => {
  const current = { model: 'model-a1', url: 'http://a' };
  const picked = pickCouncilMembers(items, current, isChatCapable);
  assert.equal(picked[0].model, 'model-a1');
  assert.equal(picked[0].endpoint_url, 'http://a');
});

test('pickCouncilMembers prefers distinct endpoints over a second model on the same one', () => {
  const current = { model: 'model-a1', url: 'http://a' };
  const picked = pickCouncilMembers(items, current, isChatCapable);
  const endpoints = picked.map((m) => m.endpoint_url);
  // model-a2 shares an endpoint with the current pick; b/c are on distinct
  // endpoints and should be preferred for the remaining two slots.
  assert.deepEqual(new Set(endpoints).size, endpoints.length);
  assert.ok(endpoints.includes('http://b'));
  assert.ok(endpoints.includes('http://c'));
});

test('pickCouncilMembers caps at 3 members', () => {
  const manyItems = Array.from({ length: 6 }, (_, i) => ({
    url: `http://ep${i}`, endpoint_name: `Endpoint ${i}`,
    models: [`m${i}`], models_display: [`M${i}`],
  }));
  const picked = pickCouncilMembers(manyItems, {}, isChatCapable);
  assert.equal(picked.length, 3);
});

test('pickCouncilMembers skips image endpoints', () => {
  const picked = pickCouncilMembers(items, {}, isChatCapable);
  assert.ok(!picked.some((m) => m.endpoint_url === 'http://img'));
});

test('pickCouncilMembers skips models isChatCapable rejects', () => {
  const notCapable = (item, modelId) => modelId !== 'model-b1';
  const picked = pickCouncilMembers(items, {}, notCapable);
  assert.ok(!picked.some((m) => m.model === 'model-b1'));
});

test('renderCouncilHTML escapes a hostile model name', () => {
  const html = renderCouncilHTML({
    answers: [{ model: '<b>x</b>', text: 'hi', error: null }],
    synthesis: null,
  });
  assert.ok(!html.includes('<b>x</b>'));
  assert.ok(html.includes('&lt;b&gt;x&lt;/b&gt;'));
});

test('renderCouncilHTML renders three labelled synthesis sections', () => {
  const html = renderCouncilHTML({
    answers: [{ model: 'm1', text: 'a', error: null }],
    synthesis: {
      model: 'rev',
      text: 'CONSENSUS: x',
      sections: { consensus: 'They agree', disagreements: 'None', recommended: 'Go with A' },
    },
  });
  assert.ok(html.includes('council-synthesis'));
  assert.ok(html.includes('Consensus'));
  assert.ok(html.includes('Disagreements'));
  assert.ok(html.includes('Recommended answer'));
  assert.ok(html.includes('They agree'));
  assert.ok(html.includes('Go with A'));
});

test('renderCouncilHTML marks a failed answer with council-answer--error', () => {
  const html = renderCouncilHTML({
    answers: [
      { model: 'good', text: 'ok', error: null },
      { model: 'bad', text: '', error: 'timeout' },
    ],
    synthesis: null,
  });
  assert.ok(html.includes('council-answer--error'));
  assert.ok(html.includes('timeout'));
});

test('renderCouncilHTML renders nothing for synthesis when null', () => {
  const html = renderCouncilHTML({ answers: [{ model: 'm1', text: 'a', error: null }], synthesis: null });
  assert.ok(!html.includes('council-synthesis'));
});
