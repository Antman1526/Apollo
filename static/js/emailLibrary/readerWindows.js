// Reader-window tab slot allocation. Slots remain stable until their tab closes.

const slots = new Map();

export function allocReaderSlot(modalId) {
  if (slots.has(modalId)) return slots.get(modalId);
  const used = new Set(slots.values());
  let slot = 1;
  while (used.has(slot)) slot += 1;
  slots.set(modalId, slot);
  return slot;
}

export function freeReaderSlot(modalId) {
  slots.delete(modalId);
}

export function readerSlot(modalId) {
  return slots.get(modalId) || null;
}

export function resetReaderSlotsForTest() {
  slots.clear();
}
