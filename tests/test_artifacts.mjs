import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

// The module must import under plain node (no DOM) — the pane is built
// lazily and every document/window touch is guarded.
const artifacts = await import('../static/js/artifacts.js');
const { extractArtifacts, wrapForPreview, wrapForNewTab, artifactFromCode, detectTitle } = artifacts;

const CSP_PREFIX = '<meta http-equiv="Content-Security-Policy"';
const HTML_DOC = [
  '<!DOCTYPE html>',
  '<html><head><title>Dashboard</title></head>',
  '<body><h1>Hello</h1><p>Some content here.</p></body></html>',
].join('\n');
const SVG_DOC = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><circle cx="50" cy="50" r="40" fill="tomato"/></svg>';

test('extractArtifacts finds html fences', () => {
  const md = 'Here you go:\n\n```html\n' + HTML_DOC + '\n```\n\nEnjoy.';
  const out = extractArtifacts(md);
  assert.equal(out.length, 1);
  assert.equal(out[0].lang, 'html');
  assert.equal(out[0].title, 'Dashboard');
  assert.ok(out[0].code.startsWith('<!DOCTYPE html>'));
  assert.ok(out[0].id);
});

test('extractArtifacts finds svg fences and xml fences that start with <svg', () => {
  const md = '```svg\n' + SVG_DOC + '\n```\n\ntext\n\n```xml\n  ' + SVG_DOC + '\n```\n\n```xml\n<note><to>Tove</to><from>Jani</from><body>Hi</body></note>\n```\n';
  const out = extractArtifacts(md);
  assert.equal(out.length, 2);
  assert.deepEqual(out.map(a => a.lang), ['svg', 'svg']);
  assert.equal(out[0].title, 'SVG artifact');
  assert.notEqual(out[0].id, out[1].id);
});

test('extractArtifacts ignores short blocks and non-artifact languages', () => {
  const md = '```html\n<b>hi</b>\n```\n\n```python\nprint("<html>" * 20)\n```\n\n```js\ndocument.body.innerHTML = "<div>" + "x".repeat(60) + "</div>";\n```\n';
  assert.deepEqual(extractArtifacts(md), []);
  assert.equal(artifactFromCode('<b>hi</b>', 'html'), null);
  assert.equal(artifactFromCode(HTML_DOC, 'python'), null);
});

test('title order: <title>, then first <h1>, then default', () => {
  const withTitle = '<html><head><title> My  App </title></head><body><h1>Other</h1>' + 'x'.repeat(40) + '</body></html>';
  const withH1 = '<div><h1 class="hero">Welcome <em>home</em></h1><p>' + 'x'.repeat(40) + '</p></div>';
  const plain = '<div><p>' + 'x'.repeat(60) + '</p></div>';
  assert.equal(detectTitle(withTitle, 'html'), 'My App');
  assert.equal(detectTitle(withH1, 'html'), 'Welcome home');
  assert.equal(detectTitle(plain, 'html'), 'HTML artifact');
  assert.equal(detectTitle(SVG_DOC, 'svg'), 'SVG artifact');
  assert.equal(artifactFromCode(withH1, 'html').title, 'Welcome home');
});

test('wrapForPreview: CSP meta is the first head element for every shape', () => {
  // Full document — used as-is, meta injected right after <head>.
  const full = wrapForPreview({ lang: 'html', code: HTML_DOC });
  assert.ok(full.includes('<title>Dashboard</title>'));
  const headIdx = full.search(/<head\b[^>]*>/i);
  assert.ok(headIdx >= 0);
  const afterHead = full.slice(full.indexOf('>', headIdx) + 1);
  assert.ok(afterHead.startsWith(CSP_PREFIX), 'CSP meta must directly follow <head>');
  assert.ok(full.includes("default-src 'none'"));

  // Fragment — wrapped in a minimal document with charset + base + styles.
  const frag = wrapForPreview({ lang: 'html', code: '<h1>Hi</h1><p>' + 'y'.repeat(50) + '</p>' });
  assert.ok(/^<!DOCTYPE html>/i.test(frag));
  const fragAfterHead = frag.slice(frag.indexOf('<head>') + '<head>'.length);
  assert.ok(fragAfterHead.startsWith(CSP_PREFIX));
  assert.ok(frag.includes('<meta charset="utf-8">'));
  assert.ok(frag.includes('<base target="_blank">'));
  assert.ok(frag.includes('color-scheme:light dark'));
  assert.ok(frag.includes('<h1>Hi</h1>'));

  // Document with <html> but no <head> — a head is synthesised for the meta.
  const noHead = wrapForPreview({ lang: 'html', code: '<html><body><p>' + 'z'.repeat(50) + '</p></body></html>' });
  assert.ok(noHead.startsWith('<html><head>' + CSP_PREFIX));
});

test('wrapForPreview: svg wrapper centers the svg and contains it verbatim', () => {
  const out = wrapForPreview({ lang: 'svg', code: SVG_DOC });
  assert.ok(out.includes(SVG_DOC));
  assert.ok(out.includes('max-width:100%'));
  const afterHead = out.slice(out.indexOf('<head>') + '<head>'.length);
  assert.ok(afterHead.startsWith(CSP_PREFIX));
  assert.ok(out.includes('<base target="_blank">'));
});

test('wrapForNewTab embeds the preview in a sandboxed srcdoc frame (no allow-same-origin)', () => {
  const out = wrapForNewTab({ lang: 'html', title: 'T & <x>', code: HTML_DOC });
  assert.ok(out.includes('sandbox="allow-scripts allow-modals allow-popups"'));
  assert.ok(!out.includes('allow-same-origin'));
  assert.ok(out.includes('srcdoc="'));
  assert.ok(out.includes('<title>T &amp; &lt;x&gt;</title>'));
});

test('module source never grants allow-same-origin', () => {
  const here = path.dirname(fileURLToPath(import.meta.url));
  const src = readFileSync(path.join(here, '..', 'static', 'js', 'artifacts.js'), 'utf8');
  assert.ok(!src.includes('allow-same-origin'));
  assert.ok(src.includes('sandbox="${FRAME_SANDBOX}"') || src.includes('sandbox='));
});

test('DOM-only entry points are safe no-ops under node', () => {
  assert.equal(artifacts.isOpen(), false);
  assert.equal(artifacts.open({ lang: 'html', code: HTML_DOC }), null);
  assert.equal(artifacts.autoOpenFromMessage('```html\n' + HTML_DOC + '\n```'), null);
  assert.equal(artifacts.isAutoOpenEnabled(), true);
  assert.doesNotThrow(() => artifacts.close());
});
