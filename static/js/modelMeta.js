// static/js/modelMeta.js
//
// Pure model-metadata + text helpers extracted from chatRenderer.js so they
// can be unit-tested under Node (chatRenderer.js itself imports the whole UI
// chain — ui.js → theme.js → colorPicker.js — which touches HTMLInputElement
// at import time and crashes outside a browser). This module has ZERO imports
// and no DOM/window access at module scope, mirroring vad.js / graphLayout.js.
//
// chatRenderer.js imports and re-exports every name here, so its public API is
// unchanged.

// Model info: pricing (per 1M tokens) + context window length.
export const MODEL_INFO = {
  // --- Anthropic ---
  'claude-sonnet-4-5':    { input: 3.00,  output: 15.00, ctx: 200000 },
  'claude-sonnet-4-6':    { input: 3.00,  output: 15.00, ctx: 200000 },
  'claude-sonnet-4':      { input: 3.00,  output: 15.00, ctx: 200000 },
  'claude-opus-4':        { input: 15.00, output: 75.00, ctx: 200000 },
  'claude-opus-4-6':      { input: 15.00, output: 75.00, ctx: 200000 },
  'claude-haiku-4':       { input: 0.80,  output: 4.00,  ctx: 200000 },
  'claude-haiku-3-5':     { input: 0.80,  output: 4.00,  ctx: 200000 },
  'claude-3-5-sonnet':    { input: 3.00,  output: 15.00, ctx: 200000 },
  'claude-3-5-haiku':     { input: 0.80,  output: 4.00,  ctx: 200000 },
  'claude-3-opus':        { input: 15.00, output: 75.00, ctx: 200000 },
  'claude-3-sonnet':      { input: 3.00,  output: 15.00, ctx: 200000 },
  'claude-3-haiku':       { input: 0.25,  output: 1.25,  ctx: 200000 },
  // --- OpenAI ---
  'gpt-5':                { input: 2.00,  output: 8.00,  ctx: 400000 },
  'gpt-4.1':              { input: 2.00,  output: 8.00,  ctx: 1047576 },
  'gpt-4.1-mini':         { input: 0.40,  output: 1.60,  ctx: 1047576 },
  'gpt-4.1-nano':         { input: 0.10,  output: 0.40,  ctx: 1047576 },
  'gpt-4o':               { input: 2.50,  output: 10.00, ctx: 128000 },
  'gpt-4o-mini':          { input: 0.15,  output: 0.60,  ctx: 128000 },
  'gpt-4-turbo':          { input: 10.00, output: 30.00, ctx: 128000 },
  'o1':                   { input: 15.00, output: 60.00, ctx: 200000 },
  'o1-mini':              { input: 3.00,  output: 12.00, ctx: 128000 },
  'o1-pro':               { input: 150.0, output: 600.0, ctx: 200000 },
  'o3':                   { input: 2.00,  output: 8.00,  ctx: 200000 },
  'o3-mini':              { input: 1.10,  output: 4.40,  ctx: 200000 },
  'o4-mini':              { input: 1.10,  output: 4.40,  ctx: 200000 },
  // --- DeepSeek ---
  'deepseek-chat':        { input: 0.27,  output: 1.10,  ctx: 64000 },
  'deepseek-coder':       { input: 0.27,  output: 1.10,  ctx: 64000 },
  'deepseek-reasoner':    { input: 0.55,  output: 2.19,  ctx: 64000 },
  'deepseek-r1':          { input: 0.55,  output: 2.19,  ctx: 64000 },
  'deepseek-v3':          { input: 0.27,  output: 1.10,  ctx: 64000 },
  'deepseek-v2':          { input: 0.14,  output: 0.28,  ctx: 64000 },
  // --- Google ---
  'gemini-2.5-pro':       { input: 1.25,  output: 10.00, ctx: 1048576 },
  'gemini-2.5-flash':     { input: 0.15,  output: 0.60,  ctx: 1048576 },
  'gemini-2.0-flash':     { input: 0.10,  output: 0.40,  ctx: 1048576 },
  'gemini-1.5-pro':       { input: 1.25,  output: 5.00,  ctx: 1048576 },
  'gemini-1.5-flash':     { input: 0.075, output: 0.30,  ctx: 1048576 },
  'gemma-3':              { input: 0.10,  output: 0.10,  ctx: 128000 },
  // --- Mistral ---
  'mistral-large':        { input: 2.00,  output: 6.00,  ctx: 128000 },
  'mistral-medium':       { input: 2.00,  output: 6.00,  ctx: 32000 },
  'mistral-small':        { input: 0.20,  output: 0.60,  ctx: 32000 },
  'mistral-nemo':         { input: 0.15,  output: 0.15,  ctx: 128000 },
  'mixtral':              { input: 0.24,  output: 0.24,  ctx: 32000 },
  'codestral':            { input: 0.30,  output: 0.90,  ctx: 32000 },
  'pixtral':              { input: 2.00,  output: 6.00,  ctx: 128000 },
  // --- xAI ---
  'grok-4':               { input: 3.00,  output: 15.00, ctx: 131072 },
  'grok-3':               { input: 3.00,  output: 15.00, ctx: 131072 },
  'grok-2':               { input: 2.00,  output: 10.00, ctx: 131072 },
  // --- Meta ---
  'llama-4':              { input: 0.20,  output: 0.20,  ctx: 1048576 },
  'llama-3.3':            { input: 0.20,  output: 0.20,  ctx: 131072 },
  'llama-3.2':            { input: 0.20,  output: 0.20,  ctx: 131072 },
  'llama-3.1':            { input: 0.20,  output: 0.20,  ctx: 131072 },
  'llama-3':              { input: 0.20,  output: 0.20,  ctx: 131072 },
  // --- Qwen ---
  'qwen3':                { input: 0.30,  output: 1.20,  ctx: 131072 },
  'qwen2.5':              { input: 0.30,  output: 1.20,  ctx: 131072 },
  'qwq':                  { input: 0.30,  output: 1.20,  ctx: 32768 },
  // --- Cohere ---
  'command-a':            { input: 2.50,  output: 10.00, ctx: 256000 },
  'command-r-plus':       { input: 2.50,  output: 10.00, ctx: 128000 },
  'command-r':            { input: 0.15,  output: 0.60,  ctx: 128000 },
  // --- Perplexity ---
  'sonar-pro':            { input: 3.00,  output: 15.00, ctx: 200000 },
  'sonar':                { input: 1.00,  output: 1.00,  ctx: 128000 },
  // --- MiniMax ---
  'minimax':              { input: 0.70,  output: 0.70,  ctx: 1000000 },
  // --- Kimi / Moonshot ---
  'moonshot':             { input: 1.00,  output: 1.00,  ctx: 128000 },
  'kimi':                 { input: 1.00,  output: 1.00,  ctx: 128000 },
  // --- Microsoft ---
  'phi-4':                { input: 0.07,  output: 0.14,  ctx: 16000 },
  'phi-3':                { input: 0.07,  output: 0.14,  ctx: 128000 },
  // --- Nvidia ---
  'nemotron':             { input: 0.30,  output: 1.20,  ctx: 131072 },
  // --- Nous ---
  'hermes':               { input: 0.20,  output: 0.20,  ctx: 131072 },
};

// Compat alias
export const MODEL_PRICING = MODEL_INFO;

// Image generation cost lookup (per-image, by model × quality × size)
export const IMAGE_PRICING = {
  'gpt-image-1.5': { 'low': { '1024x1024': 0.009, '1024x1536': 0.013, '1536x1024': 0.013 }, 'medium': { '1024x1024': 0.034, '1024x1536': 0.05, '1536x1024': 0.05 }, 'high': { '1024x1024': 0.133, '1024x1536': 0.2, '1536x1024': 0.2 } },
  'gpt-image-1':   { 'low': { '1024x1024': 0.011, '1024x1536': 0.016, '1536x1024': 0.016 }, 'medium': { '1024x1024': 0.042, '1024x1536': 0.063, '1536x1024': 0.063 }, 'high': { '1024x1024': 0.167, '1024x1536': 0.25, '1536x1024': 0.25 } },
  'gpt-image-1-mini': { 'low': { '1024x1024': 0.005, '1024x1536': 0.006, '1536x1024': 0.006 }, 'medium': { '1024x1024': 0.011, '1024x1536': 0.015, '1536x1024': 0.015 }, 'high': { '1024x1024': 0.036, '1024x1536': 0.052, '1536x1024': 0.052 } },
};

// Tool call syntax patterns to strip from displayed text
const TOOL_CALL_RE = /\[TOOL_CALL\][\s\S]*?\[\/TOOL_CALL\]/gi;
// Only strip fenced tool-call blocks that look like structured invocations, not regular code examples
const EXEC_FENCE_RE = /```(?:web_search|read_file|write_file|create_document|edit_document|update_document)\s*\n[\s\S]*?```/gi;
// XML-style tool calls: <minimax:tool_call>, <tool_call>, <function_call>, bare <invoke>
const XML_TOOL_CALL_RE = /<(?:[\w]+:)?(?:tool_call|function_call)>[\s\S]*?<\/(?:[\w]+:)?(?:tool_call|function_call)>/gi;
const XML_INVOKE_RE = /<invoke\s+name=['"][^'"]*['"]>[\s\S]*?<\/invoke>/gi;
// DeepSeek "DSML" tool-call markup (fullwidth-pipe ｜ or ascii | delimited) that
// leaks into content when the model emits a text tool call instead of a native
// one. Strip the whole block; the second pattern catches stray/partial tags
// (e.g. mid-stream before the closing tag arrives).
const DSML_TOOL_RE = /<\s*[｜|]+\s*DSML\s*[｜|]+\s*tool_calls\s*>[\s\S]*?(?:<\s*\/\s*[｜|]+\s*DSML\s*[｜|]+\s*tool_calls\s*>|$)/gi;
const DSML_STRAY_RE = /<\s*\/?\s*[｜|]+\s*DSML\s*[｜|]+[^>]*>/gi;
// Self-narration about tool results (model echoing stdout/exit_code)
const TOOL_NARRATION_RE = /(?:The (?:result|output) shows?:?\s*)?-?\s*(?:stdout|stderr|exit_code):\s*.+/gi;

export function shortModel(name) {
  if (!name) return '...';
  if (typeof name !== 'string') name = String(name);
  let short = name.split('/').pop();
  // Strip .gguf extension
  short = short.replace(/\.gguf$/i, '');
  // Strip quantization suffixes (Q4_K_M, Q8_0, etc.) and shard numbers
  short = short.replace(/-0000\d-of-\d+$/, '');
  short = short.replace(/[-_](Q\d[_A-Z\d]*|F16|F32|BF16|fp16|fp32)$/i, '');
  // Truncate if still too long (keep first meaningful part)
  if (short.length > 25) {
    // Try to find a natural break point (dash after model size like -35B or -7B)
    const sizeMatch = short.match(/^(.+?-\d+[BbMm])/);
    if (sizeMatch) short = sizeMatch[1];
    else short = short.substring(0, 22) + '…';
  }
  return short;
}

/**
 * Generate a consistent HSL color for a model name.
 * Returns an hsl() string. The hue is derived from a string hash,
 * saturation and lightness are fixed for readability on dark/light themes.
 */
export function modelColor(name) {
  if (!name) return null;
  const key = name.toLowerCase();
  let hash = 0;
  for (let i = 0; i < key.length; i++) {
    hash = ((hash << 5) - hash + key.charCodeAt(i)) | 0;
  }
  const hue = ((hash % 360) + 360) % 360;
  return `hsl(${hue}, 55%, 65%)`;
}

/** Look up model info (pricing + context) by substring match */
export function getModelInfo(modelName) {
  if (!modelName) return null;
  const name = modelName.toLowerCase();
  for (const [key, info] of Object.entries(MODEL_INFO)) {
    if (name.includes(key)) return { key, ...info };
  }
  return null;
}

export function getModelCost(modelName, inputTokens, outputTokens) {
  if (!modelName) return null;
  const name = modelName.toLowerCase();
  for (const [key, price] of Object.entries(MODEL_PRICING)) {
    if (name.includes(key)) {
      return (inputTokens * price.input + outputTokens * price.output) / 1_000_000;
    }
  }
  return null;
}

/**
 * Is this endpoint a local / self-hosted model server (vLLM, Ollama, …)?
 * Local models are free, so we must NOT bill them at cloud rates — the
 * pricing table matches on a name substring, so a local `qwen2.5-coder`
 * would otherwise be charged like cloud `qwen2.5`. When the serving host is
 * loopback, a private LAN range, Tailscale CGNAT (100.64–100.127.x), a
 * `.local` name, or the app's own host, the model is local → free.
 * Unknown / missing endpoint also counts as local (bias to not over-bill).
 */
export function isLocalEndpoint(url) {
  if (!url) return true;
  let host;
  try { host = new URL(url).hostname; } catch (_e) { return true; }
  if (!host) return true;
  if (host === 'localhost' || host === '0.0.0.0' || host === 'host.docker.internal' || host.endsWith('.local')) return true;
  if (typeof window !== 'undefined' && window.location && host === window.location.hostname) return true;
  // A single-label hostname (no dot) is an internal/Docker service name
  // (e.g. "nim-nano", "llamaswap", "nemotron-super-49b") or a LAN shortname —
  // never a public API, which always needs an FQDN. Treat as local → free.
  // (Without this, container-name endpoints get billed at cloud rates because
  // the pricing table matches on a name substring, e.g. "nemotron".)
  if (!host.includes('.')) return true;
  if (/^127\./.test(host)) return true;
  if (/^10\./.test(host)) return true;
  if (/^192\.168\./.test(host)) return true;
  if (/^172\.(1[6-9]|2\d|3[01])\./.test(host)) return true;
  const cg = host.match(/^100\.(\d+)\./);            // Tailscale CGNAT
  if (cg && +cg[1] >= 64 && +cg[1] <= 127) return true;
  return false;
}

export function getImageCost(model, quality, size) {
  if (!model) return null;
  const m = model.toLowerCase();
  for (const [key, quals] of Object.entries(IMAGE_PRICING)) {
    if (m.includes(key)) {
      const q = quals[(quality || 'medium').toLowerCase()] || quals['medium'];
      return q ? (q[size] || q['1024x1024'] || null) : null;
    }
  }
  return null;
}

/**
 * Strip tool invocation blocks from text before rendering.
 */
export function stripToolBlocks(text) {
  let cleaned = text.replace(TOOL_CALL_RE, '');
  cleaned = cleaned.replace(EXEC_FENCE_RE, '');
  cleaned = cleaned.replace(DSML_TOOL_RE, '');
  cleaned = cleaned.replace(DSML_STRAY_RE, '');
  cleaned = cleaned.replace(XML_TOOL_CALL_RE, '');
  cleaned = cleaned.replace(XML_INVOKE_RE, '');
  cleaned = cleaned.replace(TOOL_NARRATION_RE, '');
  cleaned = cleaned.replace(/\n{3,}/g, '\n\n');
  return cleaned.trim();
}
