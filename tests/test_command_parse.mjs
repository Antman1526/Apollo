import assert from 'node:assert/strict';
import test from 'node:test';

import {
  levenshtein, buildAliasMap, resolveCommand, resolveSubcommand,
  fuzzyMatch, isCmd, maskKey, normalizeSetupBaseUrl, detectProvider,
  extractSetupProviderCredential, pad2, toLocalIso, parseTimeSpec,
} from '../static/js/commandParse.js';

// Fixtures mirroring slashCommands.js constants
const COMMANDS = {
  todo:  { alias: ['task'], subs: { list: { alias: ['ls'] }, add: {} } },
  theme: { alias: ['t'] },
};
const LEGACY_ALIASES = { help: 'help', quit: 'quit' };
const PROVIDER_PATTERNS = [
  { re: /^sk-ant-/, name: 'Anthropic', url: 'https://api.anthropic.com/v1' },
  { re: /^gsk_/,    name: 'Groq',      url: 'https://api.groq.com/openai/v1' },
];
const SETUP_PROVIDER_URLS = {
  deepseek: { name: 'DeepSeek', url: 'https://api.deepseek.com/v1' },
  openai:   { name: 'OpenAI',   url: 'https://api.openai.com/v1' },
};

// ── levenshtein ──
test('levenshtein basic distances', () => {
  assert.equal(levenshtein('kitten', 'sitting'), 3);
  assert.equal(levenshtein('abc', 'abc'), 0);
  assert.equal(levenshtein('', 'abc'), 3);
});

// ── buildAliasMap / resolveCommand ──
test('buildAliasMap maps names + aliases to canonical', () => {
  const m = buildAliasMap(COMMANDS);
  assert.equal(m['todo'], 'todo');
  assert.equal(m['task'], 'todo');
  assert.equal(m['t'], 'theme');
});
test('resolveCommand returns canonical or null', () => {
  const m = buildAliasMap(COMMANDS);
  assert.equal(resolveCommand(m, 'task'), 'todo');
  assert.equal(resolveCommand(m, 'nope'), null);
});

// ── resolveSubcommand ──
test('resolveSubcommand direct + alias + miss', () => {
  const def = COMMANDS.todo;
  assert.equal(resolveSubcommand(def, 'list'), 'list');
  assert.equal(resolveSubcommand(def, 'ls'), 'list');
  assert.equal(resolveSubcommand(def, 'zzz'), null);
  assert.equal(resolveSubcommand(COMMANDS.theme, 'x'), null, 'no subs → null');
});

// ── fuzzyMatch ──
test('fuzzyMatch suggests near-misses within distance', () => {
  const m = buildAliasMap(COMMANDS);
  const out = fuzzyMatch('todi', m, LEGACY_ALIASES);
  assert.ok(out.includes('todo'));
});
test('fuzzyMatch excludes exact matches (distance 0)', () => {
  const m = buildAliasMap(COMMANDS);
  assert.ok(!fuzzyMatch('todo', m, LEGACY_ALIASES).includes('todo'));
});
test('fuzzyMatch includes legacy alias keys', () => {
  const m = buildAliasMap(COMMANDS);
  assert.ok(fuzzyMatch('hepl', m, LEGACY_ALIASES).includes('help'));
});

// ── isCmd ──
test('isCmd detects / and ! prefixes', () => {
  assert.equal(isCmd('/theme'), true);
  assert.equal(isCmd('!run'), true);
  assert.equal(isCmd('hello'), false);
});

// ── maskKey ──
test('maskKey short vs long', () => {
  assert.equal(maskKey('sk-1234'), 'sk-1...34');
  assert.equal(maskKey('sk-ant-abcdefghijklmnop'), 'sk-ant...mnop');
});

// ── normalizeSetupBaseUrl ──
test('normalizeSetupBaseUrl adds scheme + /v1 for bare host', () => {
  assert.equal(normalizeSetupBaseUrl('localhost:8080'), 'http://localhost:8080/v1');
});
test('normalizeSetupBaseUrl strips endpoint suffixes', () => {
  assert.equal(normalizeSetupBaseUrl('https://api.x.ai/v1/chat/completions'), 'https://api.x.ai/v1');
});
test('normalizeSetupBaseUrl fixes single-slash + typo schemes', () => {
  assert.equal(normalizeSetupBaseUrl('https:/api.foo.com/v1'), 'https://api.foo.com/v1');
});

// ── detectProvider ──
test('detectProvider matches known key patterns', () => {
  assert.deepEqual(detectProvider('sk-ant-xyz', PROVIDER_PATTERNS),
    { base_url: 'https://api.anthropic.com/v1', api_key: 'sk-ant-xyz', name: 'Anthropic' });
  assert.equal(detectProvider('gsk_abc', PROVIDER_PATTERNS).name, 'Groq');
});
test('detectProvider handles bare URL as self-hosted', () => {
  const r = detectProvider('http://192.168.1.5:8000', PROVIDER_PATTERNS);
  assert.equal(r.base_url, 'http://192.168.1.5:8000/v1');
  assert.equal(r.name, '');
});
test('detectProvider flags ambiguous sk- keys', () => {
  const r = detectProvider('sk-proj0123456789abcdefghij', PROVIDER_PATTERNS);
  assert.equal(r.ambiguous, true);
});
test('detectProvider null for gibberish', () => {
  assert.equal(detectProvider('just some words', PROVIDER_PATTERNS), null);
});

// ── extractSetupProviderCredential ──
test('extractSetupProviderCredential pulls provider + credential', () => {
  const r = extractSetupProviderCredential('deepseek sk-abc123', SETUP_PROVIDER_URLS);
  assert.equal(r.provider.name, 'DeepSeek');
  assert.equal(r.credential, 'sk-abc123');
});
test('extractSetupProviderCredential null when no provider word', () => {
  assert.equal(extractSetupProviderCredential('sk-abc123', SETUP_PROVIDER_URLS), null);
  assert.equal(extractSetupProviderCredential('', SETUP_PROVIDER_URLS), null);
});

// ── pad2 / toLocalIso ──
test('pad2 zero-pads', () => {
  assert.equal(pad2(3), '03');
  assert.equal(pad2(42), '42');
});
test('toLocalIso formats local time without offset', () => {
  const d = new Date(2026, 6, 3, 9, 5, 0); // 2026-07-03 09:05 local
  assert.equal(toLocalIso(d), '2026-07-03T09:05:00');
});

// ── parseTimeSpec (deterministic via injected now) ──
const NOW = new Date(2026, 0, 1, 10, 0, 0); // 2026-01-01 10:00 local
test('parseTimeSpec relative minutes/hours/days', () => {
  assert.equal(parseTimeSpec('in 30m walk', NOW).date.getMinutes(), 30);
  assert.equal(parseTimeSpec('in 2h', NOW).date.getHours(), 12);
  assert.equal(parseTimeSpec('in 1d', NOW).date.getDate(), 2);
  assert.equal(parseTimeSpec('in 30m walk dog', NOW).rest, 'walk dog');
});
test('parseTimeSpec absolute YYYY-MM-DD HH:MM', () => {
  const r = parseTimeSpec('2026-03-15 14:30 dentist', NOW);
  assert.equal(r.date.getFullYear(), 2026);
  assert.equal(r.date.getMonth(), 2);
  assert.equal(r.date.getDate(), 15);
  assert.equal(r.rest, 'dentist');
});
test('parseTimeSpec today/tomorrow with am/pm', () => {
  assert.equal(parseTimeSpec('tomorrow 9am standup', NOW).date.getDate(), 2);
  assert.equal(parseTimeSpec('tomorrow 9am standup', NOW).date.getHours(), 9);
  assert.equal(parseTimeSpec('today 2pm', NOW).date.getHours(), 14);
});
test('parseTimeSpec bare time rolls to tomorrow if past', () => {
  // 9am is before NOW (10am) → tomorrow
  assert.equal(parseTimeSpec('9am', NOW).date.getDate(), 2);
  // 11am is after NOW → today
  assert.equal(parseTimeSpec('11am', NOW).date.getDate(), 1);
});
test('parseTimeSpec rejects plain numbers', () => {
  assert.equal(parseTimeSpec('3 apples', NOW), null);
  assert.equal(parseTimeSpec('random text', NOW), null);
});
