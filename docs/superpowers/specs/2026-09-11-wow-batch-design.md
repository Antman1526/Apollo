# Wow Batch — Design (decomposition + build order)

**Date:** 2026-09-11
**Status:** Approved in direction ("Do all"); per-item designs below are
built under the stated assumptions because the session is autonomous.

## Scope

Nine items, grouped into four independent tracks. Each item lands as its own
commit with tests. Tracks are ordered so that shared files (`static/index.html`,
`static/style.css`, `static/js/chat.js`) are touched by one item at a time.

| # | Item | Track | Touches |
|---|------|-------|---------|
| 1 | README hero image is the upstream Odysseus screenshot | Polish | `docs/apollo.jpg` |
| 2 | Cookbook failure feedback: command + log tail + copy in UI | Reliability | `routes/cookbook*`, `static/js/cookbook*.js` |
| 3 | Dynamic tool selection for small-context local models | Reliability | agent tool assembly (backend only) |
| 4 | Browser co-pilot overlay: ghost cursor, action captions, take-over toggle | Browser | `services/browser/embedded_browser.py`, `routes/browser_routes.py`, `static/js/browserPanel.js`, `static/index.html` |
| 5 | Welcome screen as a live setup checklist | Home | `static/index.html`, `static/js/welcome*.js`, status endpoints |
| 6 | Home brief (email, calendar, tasks, notes, warm model) on the empty screen | Home | same + a `GET /api/home/brief` aggregator |
| 7 | Live artifact pane: sandboxed HTML/SVG preview beside chat, "open in Documents" | Chat | `static/js/chatRenderer.js`, `static/js/artifacts.js`, `static/index.html`, `static/style.css` |
| 8 | Hands-free voice mode | — | ALREADY SHIPPED upstream as `static/js/voiceCall.js` (call mode) — dropped |
| 9 | Split the CSS monolith | — | ALREADY SHIPPED upstream (`static/css/*.css`, 24 files) — dropped |

Deferred and stated explicitly: splitting `static/js/document.js` (9.5k lines)
is NOT in this batch. It has no test harness of its own and a mechanical split
without behavior tests is a regression risk that outweighs the benefit right
now. Recommended follow-up: add a JS smoke test for the editor first.

## Ordering rationale

Polish and Reliability first (isolated, low risk, immediate value). Browser next
(builds on the newest commits, contained to four files). Home track next (item 5
then 6 share the welcome DOM). Chat track after that (7 then 8 share the chat
stream). CSS split last so every earlier UI change is in place before the file
is carved up, and the split can be verified by byte-equality of concatenation.

## Cross-cutting rules

- No new build step. New JS is an ES module registered in `static/index.html`.
- Every backend change ships with a pytest; every JS module with a node `--test`
  file where the logic is pure enough to import.
- Untrusted content (model output, fetched pages) never runs with app origin:
  the artifact pane uses `<iframe sandbox>` with `srcdoc` and no `allow-same-origin`.
- Feature flags default ON for the overlay, checklist, brief, and artifacts;
  voice mode is opt-in from the composer (a button), not automatic.

## 2026-09-11 base correction

The local checkout was 247 commits behind `origin/main`. All work is built on
branch `wow-batch` from `origin/main` (e9966fd). Upstream already contains the
CSS split, the `document.js` module split, and a hands-free voice call mode,
so items 8 and 9 are dropped as done. The earlier local commits 525df3b and
ac5b03f (hero image + first scaffold on the stale base) are superseded.
