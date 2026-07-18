// Export naming and document HTML are pure so every export path agrees.

const EXTENSIONS = {
  javascript: '.js', python: '.py', html: '.html', css: '.css', markdown: '.md',
  json: '.json', yaml: '.yml', bash: '.sh', sql: '.sql', rust: '.rs', go: '.go',
  java: '.java', c: '.c', cpp: '.cpp', csharp: '.cs', typescript: '.ts', ruby: '.rb',
  php: '.php', text: '.txt', xml: '.xml', toml: '.toml', ini: '.ini', csv: '.csv',
};

export function getExportMetadata({ title = 'document', version, language = '' } = {}) {
  const safeTitle = title.replace(/[^a-zA-Z0-9_\-. ]/g, '_').trim() || 'document';
  const suffix = version ? `_v${version}` : '';
  const extension = EXTENSIONS[language] || '.txt';
  const mime = language === 'csv' ? 'text/csv'
    : language === 'json' ? 'application/json'
      : 'text/plain';
  return { baseName: `${safeTitle}${suffix}`, extension, mime };
}

export function escapeDocumentHtml(text = '') {
  return String(text).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

export function renderExportHtml({ title = 'document', body = '' } = {}) {
  return `<!DOCTYPE html>\n<html><head><meta charset="utf-8"><title>${escapeDocumentHtml(title)}</title></head><body style="max-width:800px;margin:40px auto;font-family:sans-serif;line-height:1.6;padding:0 20px;">\n${body}\n</body></html>`;
}
