import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';

// theme.js imports a DOM-heavy module chain, so validate the preset table at
// source level: presence, well-formed palettes, and readable contrast.
const src = fs.readFileSync(new URL('../static/js/theme.js', import.meta.url), 'utf8');

function paletteOf(name) {
  const m = src.match(new RegExp(
    `^\\s*${name}:\\s*\\{\\s*bg:'(#[0-9a-fA-F]{6})',\\s*fg:'(#[0-9a-fA-F]{6})',` +
    `\\s*panel:'(#[0-9a-fA-F]{6})',\\s*border:'(#[0-9a-fA-F]{6})',\\s*red:'(#[0-9a-fA-F]{6})'`, 'm'));
  if (!m) return null;
  return { bg: m[1], fg: m[2], panel: m[3], border: m[4], red: m[5] };
}

function luminance(hex) {
  const c = [1, 3, 5].map((i) => {
    const v = parseInt(hex.slice(i, i + 2), 16) / 255;
    return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
}

function contrast(a, b) {
  const [l1, l2] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (l1 + 0.05) / (l2 + 0.05);
}

const NEW_DARK = ['nord', 'dracula', 'gruvbox', 'rosepine', 'sunset'];
const NEW_LIGHT = ['solarized', 'mint', 'contrast'];

test('the eight new preset themes exist with complete palettes', () => {
  for (const name of [...NEW_DARK, ...NEW_LIGHT]) {
    const palette = paletteOf(name);
    assert.ok(palette, `theme ${name} missing or malformed`);
  }
});

test('new themes keep readable text contrast (WCAG-ish AA for body text)', () => {
  for (const name of [...NEW_DARK, ...NEW_LIGHT]) {
    const p = paletteOf(name);
    const ratio = contrast(p.bg, p.fg);
    assert.ok(ratio >= 4.5, `${name}: fg/bg contrast ${ratio.toFixed(2)} < 4.5`);
  }
});

test('light themes are actually light and dark themes dark', () => {
  for (const name of NEW_LIGHT) {
    assert.ok(luminance(paletteOf(name).bg) > 0.5, `${name} bg should be light`);
  }
  for (const name of NEW_DARK) {
    assert.ok(luminance(paletteOf(name).bg) < 0.2, `${name} bg should be dark`);
  }
});
