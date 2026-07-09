# static/css/

The former single `static/style.css` (~36k lines) split into per-feature
files. **Load order matters** — the files are linked from `index.html` in the
exact order below, which is the original source order. Source order carries
CSS specificity intent, so **`mobile-overrides.css` must stay last** (several
of its rules deliberately win specificity ties by coming last).

Order:

1. `variables.css` — `:root` palette tokens (dark + `:root.light`)
2. `base.css` — reset, Fira Code font, code/scrollbar baseline
3. `paperclip-floor.css` — the isometric "paperclip floor" agent scene
4. `layout.css` — sidebar, chat container, composer, mobile shell
5. `controls.css` — radio/preset/toolbar controls, color palette
6. `overlays.css` — voice, search overlay, theme popup, syntax highlighting
7. `chat-components.css` — chat markdown, agent UI, input area
8. `agent-thread.css` — slash responses, agent thread timeline, tool output
9. `memory.css` — memory modal
10. `doc-editor.css` — document/Artifacts editor panel
11. `admin.css` — admin panel, archive browser
12. `gallery-compare.css` — gallery, scoreboard, compare results
13. `cookbook.css` — cookbook / local-model serving
14. `settings-tasks.css` — settings modal, tasks
15. `research.css` — deep-research synapse viz + findings
16. `email.css` — group chat + email
17. `notes.css` — notes, goals, today view
18. `calendar.css` — calendar, personal assistant
19. `theme-extras.css` — in-house color picker, frosted-glass theme
20. `mobile-overrides.css` — iOS focus-zoom fix, voice-call overlay (**last**)

`../style.css` remains as a compatibility shim that `@import`s all of the
above, so clients holding cached old HTML (which linked `/static/style.css`)
still render. New rules go in the split files, not the shim.

Keep the `<link>` list in `index.html` and the `PRECACHE` list in `sw.js` in
sync with this set.
