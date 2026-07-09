import assert from 'node:assert/strict';
import test from 'node:test';

import { detectSensitive } from '../static/js/censor.js';

const labels = (text) => detectSensitive(text).map((m) => m.label);
const texts = (text) => detectSensitive(text).map((m) => m.text);

// ── empty / clean ──
test('empty and clean text yield nothing', () => {
  assert.deepEqual(detectSensitive(''), []);
  assert.deepEqual(detectSensitive(null), []);
  assert.deepEqual(detectSensitive('just some normal prose without secrets'), []);
});

// ── emails ──
test('detects an email', () => {
  const out = detectSensitive('reach me at alice.smith+tag@example.co.uk today');
  assert.equal(out.length, 1);
  assert.equal(out[0].label, 'email');
  assert.equal(out[0].text, 'alice.smith+tag@example.co.uk');
});

// ── API keys ──
test('detects common API-key prefixes', () => {
  assert.ok(labels('key sk-' + 'a'.repeat(24)).includes('api-key'));
  assert.ok(labels('ghp_' + 'b'.repeat(36)).includes('api-key'));
  assert.ok(labels('AKIA' + 'C'.repeat(16)).includes('api-key'));
  assert.ok(labels('token xoxb-' + '1'.repeat(20)).includes('api-key'));
});

// ── bearer tokens ──
test('detects a Bearer token', () => {
  assert.ok(labels('Authorization: Bearer ' + 'x'.repeat(30)).includes('token'));
});

// ── credentials (key: value / key=value / tabular) ──
test('detects credential assignments', () => {
  assert.ok(labels('password: hunter2xyz').includes('credential'));
  assert.ok(labels('api_key=abcd1234efgh').includes('credential'));
  assert.ok(labels('Password    s3cretValue').includes('credential'));
});

// ── PEM private key ──
test('detects a PEM private key block', () => {
  const pem = '-----BEGIN RSA PRIVATE KEY-----\nMIIBOwIBAAJB\n-----END RSA PRIVATE KEY-----';
  assert.ok(labels(pem).includes('private-key'));
});

// ── JWT ──
test('detects a JWT', () => {
  const jwt = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.SflKxwRJSMeKKF2QT4';
  assert.ok(labels(jwt).includes('jwt'));
});

// ── long hex (hash/token) ──
test('detects a long hex string', () => {
  assert.ok(labels('digest ' + 'a1b2c3d4'.repeat(4)).includes('hash'));
  // 31 hex chars is below the 32 threshold → not flagged
  assert.ok(!labels('x' + '0'.repeat(0) + 'a'.repeat(31) + ' end').includes('hash'));
});

// ── internal IPs ──
test('detects internal IP ranges with optional port', () => {
  assert.ok(labels('host 10.0.0.5:8080').includes('internal-ip'));
  assert.ok(labels('192.168.1.1').includes('internal-ip'));
  assert.ok(labels('172.16.0.9').includes('internal-ip'));
  // public IP is NOT an internal-ip
  assert.ok(!labels('8.8.8.8').includes('internal-ip'));
});

// ── dedup / overlap merge ──
test('overlapping matches are merged, start-sorted', () => {
  const out = detectSensitive('a@b.com then password: secret99');
  // sorted by start
  for (let i = 1; i < out.length; i++) {
    assert.ok(out[i].start >= out[i - 1].start, 'start-sorted');
    assert.ok(out[i].start >= out[i - 1].end, 'non-overlapping after merge');
  }
});

test('multiple secrets in one string all surface', () => {
  const t = 'mail x@y.com key sk-' + 'z'.repeat(24) + ' ip 10.1.2.3';
  const ls = labels(t);
  assert.ok(ls.includes('email'));
  assert.ok(ls.includes('api-key'));
  assert.ok(ls.includes('internal-ip'));
});

// ── match objects have the shape _processElement consumes ──
test('match objects expose start/end/text/label', () => {
  const [m] = detectSensitive('bob@acme.com');
  assert.equal(typeof m.start, 'number');
  assert.equal(typeof m.end, 'number');
  assert.equal(m.text, 'bob@acme.com');
  assert.equal(m.end - m.start, m.text.length);
  assert.equal(m.label, 'email');
});
