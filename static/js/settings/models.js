// Pure selection policy shared by settings model panels.

export function endpointLabel(endpoint) {
  return `${endpoint.name || ''}${endpoint.online ? '' : ' (offline)'}`;
}

export function selectableModels(models, modelMeta = {}, { chatOnly = false } = {}) {
  return (models || []).map((id) => {
    const kind = modelMeta[id]?.kind || '';
    if (chatOnly && kind === 'embedding') return null;
    return {
      id,
      label: String(id).split('/').pop(),
      disabled: chatOnly && kind === 'unsupported',
      kind,
    };
  }).filter(Boolean);
}
