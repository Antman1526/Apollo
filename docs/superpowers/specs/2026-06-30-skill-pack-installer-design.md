# Skill-Pack Installer — Design Spec

**Date:** 2026-06-30
**Status:** Approved (design), pending implementation plan
**Branch:** `feature/skill-pack-installer` (worktree)

## 1. Summary

A first-class way to install **Agent Skills** into Apollo from a public GitHub
repo (or uploaded zip). The installer walks the source for every `SKILL.md`,
normalizes its frontmatter into Apollo's schema, records provenance, drops the
files into `data/skills/<category>/<name>/`, and triggers the existing ChromaDB
reindex — turning the large ecosystem of Anthropic + community skill packs
(humanizer, frontend-design, marketing, social, legal, finance, …) into
reviewable, one-action installs.

This is the **keystone** of a larger effort: the starter-import and domain-pack
tracks are just curated uses of this installer.

## 2. Why this is safe-by-default (the core design principle)

Installing third-party skills is a **supply-chain risk**. Apollo's agent can run
`bash`/`python`, and some packs ship `scripts/*.py`, hooks, or `.mcp.json`.
Auto-importing-and-running them would hand a malicious pack code execution. So:

- **Untrusted by default.** Imported skills are treated as untrusted content.
- **Two trust tiers, decided per skill at import:**
  - **Prose-only** (no `scripts/`, hooks, or `.mcp.json` in the skill folder) →
    imported as `status: published`, immediately usable. These are just
    instructions — low risk.
  - **Script-backed** (folder contains executable code / hooks / MCP config) →
    imported **quarantined**: `status: draft`, code copied but **never
    auto-run**, surfaced in the UI with a "ships executable code — review before
    enabling" warning. The user explicitly promotes to `published` after review.
- **No code executes during import.** The installer only reads/writes files and
  parses YAML/markdown. It never runs an imported script.
- **Fetch is SSRF-guarded.** The GitHub host is validated through the same
  private-network filter Apollo already uses for web fetch
  (`src/search/content.py`), so `base_url`-style SSRF (metadata endpoints,
  loopback, internal hosts) is rejected. Only GitHub tarball/zip hosts allowed.

This posture is consistent with the agent-subprocess env-scrub hardening already
in the codebase.

## 3. Goals / non-goals

### Goals
- Install skills from a GitHub repo URL (any depth — walk for `SKILL.md`).
- Install from an uploaded `.zip` (offline / private packs).
- Normalize frontmatter: keep `name`/`description`/body verbatim; add Apollo's
  `category`, `owner`, `platforms`; set `status` per trust tier; set
  `source: imported`.
- Record provenance: source repo URL + commit/ref + import timestamp, in
  frontmatter, so a pack is auditable and bulk-removable.
- Preview before commit: show the list of skills found + their trust tier +
  name/description; user confirms.
- Reuse Apollo's existing skill store + ChromaDB reindex (no new retrieval code).
- A management UI action in the existing Skills panel.

### Non-goals (deferred)
- Auto-running or sandboxing imported scripts (quarantine only for v1).
- Installing MCP connectors from a pack's `.mcp.json` (separate track; MCP
  already has its own setup path).
- Dependency resolution across cross-referencing skills (import the whole pack;
  don't try to satisfy "see other-skill" links automatically).
- A curated in-app marketplace/browse experience (v1 is "paste a repo URL").
- Updating/versioning an already-installed pack (v1 is install + remove).

## 4. Architecture

All server-side additions live in a new service module + a route; the frontend
adds one panel action. No changes to the skill *format* or retrieval.

### New / changed components
1. **`services/skills/pack_installer.py`** (new) — pure-ish logic:
   - `fetch_pack(source) -> local_dir` — download a GitHub repo tarball (via the
     SSRF-guarded URL check) or accept an uploaded zip; extract to a temp dir.
   - `discover_skills(local_dir) -> [FoundSkill]` — walk for `SKILL.md`; for each,
     parse frontmatter+body (reuse `skill_format.parse_frontmatter` /
     `Skill.from_markdown`), classify trust tier (scan the skill folder for
     `scripts/`, `hooks/`, `*.py`, `.mcp.json`, executable files).
   - `normalize(found, opts) -> Skill` — map to Apollo's schema: preserve
     name/description/body; set `category` (from opts or repo section), `owner`,
     `platforms`; `status = published|draft` by tier; `source = "imported"`;
     stash provenance (`imported_from`, `imported_ref`, `imported_at`) in
     frontmatter.
   - `install(found, opts) -> InstalledResult` — write into
     `data/skills/<category>/<name>/SKILL.md` (+ copy the skill's own files for
     script-backed, but flagged), via the existing store; trigger reindex.
   - The discover/normalize/classify parts are **pure and unit-testable**
     (operate on a temp dir of files, no network).
2. **`routes/skill_pack_routes.py`** (new) — `POST /api/skills/packs/preview`
   (fetch + discover, return the list + tiers, no writes) and
   `POST /api/skills/packs/install` (install a confirmed selection). Auth: admin,
   matching the existing skills routes. Registered in `app.py`.
3. **`static/js/skills.js`** (changed) — an "Install skill pack" button in the
   Skills panel → modal: source input (repo URL or file) → preview list with
   per-skill tier badge + checkbox → Install. Mirrors the existing skills CRUD
   fetch pattern.

### Reused (unchanged)
- `services/memory/skill_format.py` — `parse_frontmatter`, `Skill.from_markdown`,
  `Skill.to_frontmatter`.
- `services/memory/skills.py` — the on-disk store + ChromaDB reindex.
- `src/search/content.py` — the private-network/SSRF URL guard.
- `routes/skills_routes.py` — pattern for the new routes; existing skills list UI.

## 5. Data flow

```
repo URL / zip
  → fetch_pack (SSRF-guarded download + extract to temp)
  → discover_skills (walk SKILL.md; parse; classify tier)
  → PREVIEW to user (name, description, tier badge, per-skill checkbox)
  → user confirms selection
  → normalize (Apollo frontmatter + provenance + status by tier)
  → install (write data/skills/<cat>/<name>/, copy files if script-backed)
  → reindex (existing ChromaDB path)
  → prose skills live immediately; script-backed sit as draft/quarantined
```

## 6. Error handling & guards

- **SSRF:** reject non-GitHub / private / loopback / metadata hosts before any
  fetch; only allow the GitHub codeload/tarball host set.
- **Size/zip-bomb:** cap download size and extracted size; reject archives that
  exceed the cap or contain path-traversal (`../`) entries.
- **Malformed skill:** a `SKILL.md` that fails to parse is skipped and reported
  in the preview (not fatal to the whole pack).
- **Name collision:** if a skill name already exists, surface it in preview and
  require an explicit overwrite choice (default: skip).
- **No skills found:** report clearly ("no SKILL.md files under <source>").
- **Never execute:** the installer must not import-and-run any pack code; scripts
  are copied as inert files under the quarantined draft skill.
- **Provenance integrity:** every installed skill records `imported_from` /
  `imported_ref` so `remove pack` can find and delete exactly what it added.

## 7. Testing

- **Pure unit tests** (`pytest`, no network): point `discover_skills` /
  `classify_tier` / `normalize` at temp dirs built in the test:
  - a prose-only skill → tier=prose, status→published, body preserved.
  - a skill folder with `scripts/x.py` → tier=script, status→draft (quarantined).
  - a skill folder with `.mcp.json` → tier=script.
  - frontmatter normalization: Anthropic `name`/`description`/`license` → Apollo
    schema with added `category`/`owner`/`source`/provenance; `status` by tier.
  - zip extraction rejects path-traversal + oversize entries.
  - collision detection returns the existing name.
- **SSRF guard test** mirroring `tests/test_webhook_ssrf_resilience.py`: a
  private/loopback/metadata URL is rejected before fetch.
- **Route test:** `preview` returns the discovered list without writing;
  `install` writes the expected files and triggers reindex (store mocked).
- Network fetch itself is verified manually against a real small repo
  (e.g. `blader/humanizer`) — not in unit tests.

## 8. Implementation notes

- Keep `pack_installer.py` split so the file-walking/parsing/classifying logic is
  network-free and unit-testable; isolate the actual download in a thin
  `fetch_pack` that the tests don't exercise.
- Reuse `Skill.from_markdown` + `to_frontmatter` rather than re-implementing YAML
  handling; provenance fields ride along as extra frontmatter keys.
- Follow the existing admin-gated route-factory pattern in `routes/`.
- The follow-on tracks consume this: **starter import** = call the installer on
  humanizer / frontend-design / marketing / social; **domain packs** = same for
  legal / finance skill subdirs (connectors skipped).
