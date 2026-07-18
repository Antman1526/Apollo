// Pure request-recovery decisions. chat.js owns controllers, DOM, and transport.

export function isRecoverableStreamError(error) {
  if (!error) return false;
  if (error.name === 'TypeError') return true;
  const message = String(error.message || '').toLowerCase();
  if (/\btool\b|unsupported|json|parse|\b4\d\d\b|\b5\d\d\b/.test(message)) return false;
  return /network|fetch|connection|reset|closed|aborted|stream|tim(?:e|ed)\s?out|econn|eof/.test(message);
}

export function buildRecoveryPrompt(accumulated = '') {
  const tail = String(accumulated).slice(-400);
  return tail
    ? `The stream dropped before you finished. It ended with:\n\n${tail}\n\nIf the task is fully complete, reply with just: DONE. Otherwise continue exactly where you left off and finish it — do not repeat what you already wrote.`
    : 'The stream dropped before you produced anything. If the task is already done, reply with just: DONE. Otherwise complete it now.';
}
