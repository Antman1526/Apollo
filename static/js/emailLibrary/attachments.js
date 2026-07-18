// Attachment filtering and markup. Event binding remains in emailLibrary.js.

function escapeHtml(value = '') {
  return String(value).replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

export function isLikelySignatureImage(attachment) {
  if (!attachment?.filename) return false;
  const name = String(attachment.filename).toLowerCase();
  if (!/\.(png|jpe?g|gif|bmp|svg|webp)$/i.test(name)) return false;
  const size = Number(attachment.size) || 0;
  return /^image\d{3,}\.(png|jpe?g|gif)$/i.test(name)
    || /^(signature|logo|sig|footer|banner)[-_\d]*\.(png|jpe?g|gif|svg)$/i.test(name)
    || (size > 0 && size < 30 * 1024);
}

export function buildAttachmentsHtml(uid, data) {
  if (!data?.attachments?.length) return '';
  const visible = data.attachments.filter((attachment) => !isLikelySignatureImage(attachment));
  if (!visible.length) return '';
  const chips = visible.map((attachment) => {
    const filename = escapeHtml(attachment.filename);
    const openable = /\.(pdf|docx|txt|md|markdown)$/i.test(attachment.filename || '');
    const open = openable
      ? `<span class="email-attachment-open" title="Open in document editor" data-open-uid="${escapeHtml(uid)}" data-open-index="${attachment.index}" data-open-name="${filename}"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="8" y1="13" x2="16" y2="13"/><line x1="8" y1="17" x2="16" y2="17"/><line x1="8" y1="9" x2="10" y2="9"/></svg><span class="email-attachment-open-label">Open</span></span>`
      : '';
    return `<button type="button" class="email-attachment-chip" data-att-uid="${escapeHtml(uid)}" data-att-index="${attachment.index}" data-att-name="${filename}"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="m21.44 11.05-9.19 9.19a6 6 0 0 1-8.49-8.49l8.57-8.57A4 4 0 1 1 17.93 8.8l-8.59 8.57a2 2 0 0 1-2.83-2.83l8.49-8.48"/></svg><span>${filename}</span><span class="att-size">${Math.round((attachment.size || 0) / 1024)} KB</span>${open}</button>`;
  }).join('');
  return '<div class="email-reader-atts-wrap collapsed">'
    + '<div class="email-reader-atts-header email-summary-toggle" role="button" tabindex="0">'
    + '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m21.44 11.05-9.19 9.19a6 6 0 0 1-8.49-8.49l8.57-8.57A4 4 0 1 1 17.93 8.8l-8.59 8.57a2 2 0 0 1-2.83-2.83l8.49-8.48"/></svg>'
    + `<span>Attachments (${data.attachments.length})</span>`
    + '<svg class="email-summary-chevron" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="margin-left:auto;transition:transform .15s ease;"><polyline points="6 9 12 15 18 9"/></svg>'
    + '</div><div class="email-reader-atts">' + chips + '</div></div>';
}
