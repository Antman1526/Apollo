// static/js/commandParse.js
//
// Pure command-parsing / provider-detection / time-parsing helpers extracted
// from slashCommands.js so they can be unit-tested under Node. slashCommands.js
// itself pulls in the whole UI chain (crashes on Node import), so — as with
// modelMeta.js — the pure logic lives here in a dependency-free leaf module
// (ZERO imports, no DOM/window at module scope).
//
// State-dependent helpers are PARAMETERIZED (the caller passes COMMANDS /
// alias maps / provider tables), so this module holds no app state. Thin
// wrappers in slashCommands.js close over the module constants and keep the
// original private names + signatures, so no call sites there change.

/** Levenshtein edit distance. */
export function levenshtein(a, b) {
  const m = a.length, n = b.length;
  const dp = Array.from({ length: m + 1 }, () => new Array(n + 1).fill(0));
  for (let i = 0; i <= m; i++) dp[i][0] = i;
  for (let j = 0; j <= n; j++) dp[0][j] = j;
  for (let i = 1; i <= m; i++) {
    for (let j = 1; j <= n; j++) {
      dp[i][j] = a[i-1] === b[j-1]
        ? dp[i-1][j-1]
        : 1 + Math.min(dp[i-1][j], dp[i][j-1], dp[i-1][j-1]);
    }
  }
  return dp[m][n];
}

/** Build {alias|name → canonical name} from a COMMANDS map. */
export function buildAliasMap(commands) {
  const map = {};
  for (const [name, def] of Object.entries(commands)) {
    map[name] = name;
    if (def.alias) def.alias.forEach(a => { map[a] = name; });
  }
  return map;
}

/** Resolve a typed command to its canonical key via a prebuilt alias map. */
export function resolveCommand(aliasMap, cmd) {
  return aliasMap[cmd] || null;
}

/** Resolve a subcommand within a command definition, checking sub aliases. */
export function resolveSubcommand(def, sub) {
  if (!def.subs) return null;
  if (def.subs[sub]) return sub;
  for (const [name, sDef] of Object.entries(def.subs)) {
    if (sDef.alias && sDef.alias.includes(sub)) return name;
  }
  return null;
}

/** Suggest close matches for a mistyped command. */
export function fuzzyMatch(typed, aliasMap, legacyAliases, maxDist) {
  maxDist = maxDist || 2;
  const candidates = Object.keys(aliasMap);
  // Also include legacy alias keys
  Object.keys(legacyAliases || {}).forEach(k => { if (!candidates.includes(k)) candidates.push(k); });
  const matches = [];
  for (const c of candidates) {
    const d = levenshtein(typed, c);
    if (d > 0 && d <= maxDist) matches.push(c);
  }
  return matches;
}

/** Is this string a slash/bang command? */
export function isCmd(str) { return str.startsWith('/') || str.startsWith('!'); }

/** Mask an API key for display. */
export function maskKey(key) {
  if (key.length <= 12) return key.slice(0, 4) + '...' + key.slice(-2);
  return key.slice(0, 6) + '...' + key.slice(-4);
}

/** Normalize a user-typed endpoint base URL to a clean `…/v1`-style base. */
export function normalizeSetupBaseUrl(raw) {
  let u = (raw || '').trim();
  u = u.replace(/^https?:\/(?!\/)/, m => m + '/');
  u = u.replace(/^htp:/, 'http:').replace(/^htps:/, 'https:');
  if (!/^https?:\/\//i.test(u)) u = 'http://' + u;
  u = u.replace(/\/+$/, '');
  u = u.replace(/\/v1\/(models|chat\/completions|completions|messages)\/?$/i, '/v1');
  u = u.replace(/\/(models|chat\/completions|completions|v1\/messages)\/?$/i, '');
  u = u.replace(/\/v1\/v1$/i, '/v1');
  if (!u.includes('api.') && !u.includes('openrouter') && !u.endsWith('/v1')) {
    try {
      const parsed = new URL(u);
      if (!parsed.pathname || parsed.pathname === '/') u += '/v1';
    } catch (_) {}
  }
  return u;
}

/**
 * Detect provider from a pasted API key or URL, given the provider-pattern
 * table. Returns { base_url, api_key, name }, { ambiguous, api_key }, or null.
 */
export function detectProvider(input, providerPatterns) {
  const trimmed = input.trim();
  // URL or bare IP/hostname — self-hosted endpoint
  if (/^https?:\/\//i.test(trimmed) || /^(\d{1,3}\.){1,3}\d{1,3}(:\d+)?/i.test(trimmed) || /^(localhost|[\w.-]+:\d{2,5})/i.test(trimmed)) {
    let url = trimmed.replace(/\/+$/, '');
    if (!/^https?:\/\//i.test(url)) url = 'http://' + url;
    // Strip trailing path segments to get a clean base
    for (const suffix of ['/models', '/chat/completions', '/completions', '/v1/messages']) {
      if (url.endsWith(suffix)) url = url.slice(0, -suffix.length).replace(/\/+$/, '');
    }
    url = url.replace(/\/api\/(chat|tags|generate)\/?$/i, '/api');
    try {
      const parsed = new URL(url);
      if (parsed.hostname.endsWith('ollama.com')) url = 'https://ollama.com/api';
    } catch(e) {}
    // Add /v1 if bare host:port
    if (/^https?:\/\/[^/]+$/.test(url) && !url.includes('api.') && !url.includes('ollama.com')) url += '/v1';
    return { base_url: url, api_key: '', name: '' };
  }
  // Known key patterns
  for (const p of providerPatterns) {
    if (p.re.test(input)) {
      return { base_url: p.url, api_key: input, name: p.name };
    }
  }
  // Generic sk- keys are ambiguous (OpenAI legacy, DeepSeek, and others).
  // Never guess a provider for a secret: asking avoids sending the key to
  // OpenRouter/OpenAI/etc. by mistake during setup probing.
  if (/^sk-[a-zA-Z0-9_\-]{20,}$/.test(input)) {
    return { ambiguous: true, api_key: input };
  }
  return null;
}

/**
 * Extract a provider + credential from free text like "deepseek sk-...".
 * `providerUrls` is the SETUP_PROVIDER_URLS table. Returns { provider,
 * credential } or null.
 */
export function extractSetupProviderCredential(input, providerUrls) {
  const raw = (input || '').trim();
  if (!raw) return null;
  const providerAliases = [
    ['deepseek ai', 'deepseek'], ['deepseek', 'deepseek'],
    ['open router', 'openrouter'], ['openrouter', 'openrouter'],
    ['ollama cloud', 'ollama'], ['ollama', 'ollama'],
    ['open ai', 'openai'], ['openai', 'openai'], ['chatgpt', 'openai'],
    ['anthropic', 'anthropic'], ['claude', 'anthropic'],
    ['groq', 'groq'],
    ['google', 'gemini'], ['gemini', 'gemini'],
    ['x ai', 'xai'], ['xai', 'xai'], ['grok', 'xai'],
  ];
  for (const [alias, key] of providerAliases) {
    const re = new RegExp('(^|\\s|[,;:])(' + alias.replace(/\s+/g, '\\s+') + ')(?=$|\\s|[,;:])', 'i');
    const match = raw.match(re);
    if (!match) continue;
    const provider = providerUrls[key];
    const credential = raw.replace(match[0], match[1] || '').replace(/^[\s,;:]+|[\s,;:]+$/g, '');
    return { provider, credential };
  }
  return null;
}

/** Zero-pad to 2 digits. */
export function pad2(n) { return String(n).padStart(2, '0'); }

/** Local-time ISO-8601 string (no Z, no offset) — what the calendar API wants. */
export function toLocalIso(d) {
  return `${d.getFullYear()}-${pad2(d.getMonth()+1)}-${pad2(d.getDate())}T${pad2(d.getHours())}:${pad2(d.getMinutes())}:00`;
}

/**
 * Parse a natural-language time spec from the *start* of the string.
 * Returns { date: Date, rest: string } or null if nothing matched.
 * `now` (a Date) is injectable so tests are deterministic; defaults to real now.
 * Supported: "in 30m/2h/1d", "today/tomorrow 9am", "HH:MM"/"9am", "YYYY-MM-DD HH:MM".
 */
export function parseTimeSpec(input, now) {
  let s = (input || '').trim().replace(/^(me\s+)/i, '').trim();
  now = now || new Date();

  // "in 30m" / "in 2h" / "in 1d"
  let m = s.match(/^in\s+(\d+)\s*(m|min|mins|minutes|h|hr|hrs|hours|d|day|days)\b\s*(?:to\s+)?(.*)$/i);
  if (m) {
    const n = parseInt(m[1], 10);
    const unit = m[2].toLowerCase();
    const d = new Date(now);
    if (unit.startsWith('m')) d.setMinutes(d.getMinutes() + n);
    else if (unit.startsWith('h')) d.setHours(d.getHours() + n);
    else d.setDate(d.getDate() + n);
    return { date: d, rest: m[3].trim() };
  }

  // "YYYY-MM-DD HH:MM"
  m = s.match(/^(\d{4})-(\d{2})-(\d{2})[T\s]+(\d{1,2}):(\d{2})\s*(?:to\s+)?(.*)$/i);
  if (m) {
    const d = new Date(+m[1], +m[2]-1, +m[3], +m[4], +m[5]);
    return { date: d, rest: m[6].trim() };
  }

  // "today HH:MM" / "tomorrow HH:MM" / "today 9am" / "tomorrow 9pm"
  m = s.match(/^(today|tomorrow)\s+(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\s*(?:to\s+)?(.*)$/i);
  if (m) {
    const d = new Date(now);
    if (m[1].toLowerCase() === 'tomorrow') d.setDate(d.getDate() + 1);
    let hh = parseInt(m[2], 10);
    const mm = m[3] ? parseInt(m[3], 10) : 0;
    const mer = (m[4] || '').toLowerCase();
    if (mer === 'pm' && hh < 12) hh += 12;
    if (mer === 'am' && hh === 12) hh = 0;
    if (hh > 23 || mm > 59) return null;
    d.setHours(hh, mm, 0, 0);
    return { date: d, rest: m[5].trim() };
  }

  // bare "HH:MM" / "9am" / "9pm" / "at HH:MM" — today, or tomorrow if past
  m = s.match(/^(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b\s*(?:to\s+)?(.*)$/i);
  if (m) {
    const d = new Date(now);
    let hh = parseInt(m[1], 10);
    const mm = m[2] ? parseInt(m[2], 10) : 0;
    const mer = (m[3] || '').toLowerCase();
    if (mer === 'pm' && hh < 12) hh += 12;
    if (mer === 'am' && hh === 12) hh = 0;
    // Require a valid hour/minute and either a minute field or am/pm to
    // avoid eating plain numbers like "3 apples".
    if (hh > 23 || mm > 59) return null;
    if (m[2] == null && !mer) return null;
    d.setHours(hh, mm, 0, 0);
    if (d.getTime() <= now.getTime()) d.setDate(d.getDate() + 1);
    return { date: d, rest: m[4].trim() };
  }

  return null;
}
