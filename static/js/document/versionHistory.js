// Version-history comparisons are kept independent from the document panel.

function escapeHtml(value = '') {
  return String(value).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

export function buildVersionDiffSummary(oldText = '', newText = '') {
  if (!oldText && !newText) return '';
  const oldLines = oldText.split('\n');
  const newLines = newText.split('\n');
  const added = [];
  const removed = [];
  const maxCheck = Math.max(oldLines.length, newLines.length);
  for (let index = 0; index < maxCheck; index += 1) {
    const oldLine = oldLines[index];
    const newLine = newLines[index];
    if (oldLine === newLine) continue;
    if (oldLine !== undefined) removed.push(oldLine.trim());
    if (newLine !== undefined) added.push(newLine.trim());
  }
  const parts = [];
  for (const line of removed.slice(0, 2)) {
    if (line) parts.push(`<span class="diff-del">${escapeHtml(line.slice(0, 60))}</span>`);
  }
  for (const line of added.slice(0, 2)) {
    if (line) parts.push(`<span class="diff-add">${escapeHtml(line.slice(0, 60))}</span>`);
  }
  const extra = added.length + removed.length - 4;
  if (extra > 0) parts.push(`<span>+${extra} more changes</span>`);
  return parts.join('<br>');
}
