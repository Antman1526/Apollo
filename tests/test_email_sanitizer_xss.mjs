import assert from 'node:assert/strict';
import test from 'node:test';
import fs from 'node:fs';

// Source-level check of the isDangerousUrl scheme filter — it must strip ALL
// ASCII control chars (tab/CR/LF the URL parser ignores) before testing the
// scheme, not just trim(). Regression for the embedded-tab javascript: bypass.
const src = fs.readFileSync(new URL('../static/js/emailLibrary/utils.js', import.meta.url), 'utf8');

test('isDangerousUrl strips u0000-u0020 before scheme check, not bare trim()', () => {
  assert.match(
    src,
    /replace\(\/\[\\u0000-\\u0020\]\/g, ''\)\.toLowerCase\(\)/,
    'sanitizer must strip control chars before the scheme check',
  );
});

// Behavioral: reproduce the exact filter and prove the bypass vectors are caught.
test('embedded-control-char javascript: URLs are flagged dangerous', () => {
  const isDangerousUrl = (val) => {
    if (!val) return false;
    const v = String(val).replace(/[\u0000-\u0020]/g, '').toLowerCase();
    return v.startsWith('javascript:') || v.startsWith('vbscript:') || v.startsWith('data:');
  };
  const TAB = String.fromCharCode(9);
  const LF = String.fromCharCode(10);
  const CR = String.fromCharCode(13);
  const CTRL = String.fromCharCode(1);
  assert.equal(isDangerousUrl('jav' + TAB + 'ascript:alert(1)'), true);
  assert.equal(isDangerousUrl('java' + LF + 'script:alert(1)'), true);
  assert.equal(isDangerousUrl('javascript' + CR + ':alert(1)'), true);
  assert.equal(isDangerousUrl('  JavaScript:alert(1)'), true);
  assert.equal(isDangerousUrl(CTRL + 'javascript:x'), true);
  assert.equal(isDangerousUrl('https://example.com'), false);
  assert.equal(isDangerousUrl('/relative/path'), false);
});
