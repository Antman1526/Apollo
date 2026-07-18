// Draft serialization and debounce policy. Notes UI owns form wiring.

export function draftKey(id, prefix = 'apollo-note-draft-') {
  return `${prefix}${id || '__new__'}`;
}

export function loadDraft(storage, id, prefix) {
  try { return JSON.parse(storage.getItem(draftKey(id, prefix)) || 'null'); } catch { return null; }
}

export function clearDraft(storage, id, prefix) {
  try { storage.removeItem(draftKey(id, prefix)); } catch {}
}

export function isDraftEmpty(draft) {
  if (!draft) return true;
  if (String(draft.title || '').trim() || String(draft.content || '').trim()) return false;
  return !(Array.isArray(draft.items) && draft.items.some((item) => String(item?.text || '').trim()));
}

export function scheduleDraftSave({ timer, setTimer, clearTimer, delay = 600, save }) {
  if (timer) clearTimer(timer);
  return setTimer(save, delay);
}
