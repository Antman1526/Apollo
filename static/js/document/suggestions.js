// Suggestion queue serialization and deduplication without DOM or storage.

export function serializeSuggestions(suggestions) {
  return suggestions.map(({ id, find, replace, reason }) => ({ id, find, replace, reason }));
}

export function parseStoredSuggestions(raw) {
  if (!raw) return [];
  try {
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed)
      ? parsed.filter((item) => item && item.id).map((item) => ({
        id: item.id, find: item.find, replace: item.replace, reason: item.reason, cardEl: null,
      }))
      : [];
  } catch {
    return [];
  }
}

export function appendUniqueSuggestions(current, incoming) {
  const existing = new Set(current.map((suggestion) => suggestion.id));
  const additions = [];
  for (const suggestion of incoming || []) {
    if (!suggestion?.id || existing.has(suggestion.id)) continue;
    existing.add(suggestion.id);
    additions.push({
      id: suggestion.id,
      find: suggestion.find,
      replace: suggestion.replace,
      reason: suggestion.reason,
      cardEl: null,
    });
  }
  return additions;
}
