// Pure document state helpers. The editor owns DOM updates and persistence.

export function findReusableDocumentId(docs, update, sessionId) {
  if (update.title) {
    for (const [id, doc] of docs) {
      if (doc.title === update.title && doc.sessionId === sessionId) return id;
    }
  }
  for (const [id, doc] of docs) {
    if (doc.sessionId === sessionId
      && (!doc.title || doc.title === 'Untitled')
      && (!doc.content || doc.content.trim() === '')) return id;
  }
  return null;
}

export function mergeDocumentUpdate(existing, update, sessionId) {
  const content = update.content || '';
  if (existing) {
    return {
      ...existing,
      content,
      version: update.version || existing.version,
      title: update.title || existing.title,
      language: update.language || existing.language,
    };
  }
  return {
    id: update.doc_id,
    title: update.title || '',
    language: update.language || '',
    content,
    version: update.version || 1,
    sessionId,
  };
}

export function deriveDocumentTitle(content) {
  const text = (content || '').trimStart();
  if (!text) return null;

  let title = text.match(/^#{1,3}\s+(.+)/m)?.[1]?.trim() || null;
  if (!title) title = text.match(/<h[1-3][^>]*>([^<]+)<\/h[1-3]>/i)?.[1]?.trim() || null;
  if (!title) {
    const firstLine = text.split('\n').find((line) => line.trim().length > 0)?.trim();
    if (firstLine && firstLine.length >= 2 && firstLine.length <= 60) title = firstLine;
  }
  if (!title) return null;
  title = title.replace(/[:#*`]+$/g, '').trim();
  if (title.length > 50) title = `${title.slice(0, 48)}...`;
  return title || null;
}
