# Embedded Interactive Browser — Design

**Date:** 2026-06-12
**Status:** Approved (user chose: fully interactive · stream everything · shared session)

## Goal

The Browser panel becomes a real embedded browser: a live view of Apollo's
server-side Chromium (the agent browser) that the user can click, scroll,
and type into — for every site, with no iframe/framing limitations. The
session is shared with the agent's browser tool, so the user can watch and
assist agent browsing live.

## Architecture

```
Panel (canvas) ⇄ WebSocket /api/browser/ws ⇄ embedded_browser (Playwright Chromium)
   frames ↓ (CDP Page.startScreencast, JPEG)      input ↑ (page.mouse/keyboard)
```

- **Frames:** CDP screencast (`Page.startScreencast`, JPEG quality ~70,
  maxWidth 1280, ack each frame). Each frame carries device width/height so
  the client can scale input coordinates.
- **Input:** client forwards mouse (move/down/up/wheel) and keyboard
  (down/up) events over the WS; server replays via Playwright
  `page.mouse.*` / `page.keyboard.*` (W3C key values — no raw CDP needed
  for input).
- **Nav sync:** server pushes `{type:"url"}` on `framenavigated`; client
  updates the address bar. Back/forward/reload/navigate are WS commands
  (`page.go_back/go_forward/reload/goto`).
- **Shared session:** same Playwright page as the agent tools — agent
  actions appear live in the panel. Single-viewer screencast (a second
  panel connection takes over the stream).
- **Auth:** WebSockets bypass the HTTP AuthMiddleware (per app.py); the WS
  handler must validate the session cookie + `can_use_browser` privilege
  itself before streaming.
- **Old fallback:** the iframe viewport and the screenshot-fallback from
  0e179fb are superseded; the canvas view replaces them for all URLs. The
  `frameable` field in /api/browser/navigate stays (harmless, API-stable).
  The localhost dev-server detection and the console events pane stay.

## Components

1. `services/browser/embedded_browser.py`
   - `start_screencast(on_frame)` / `stop_screencast()` (CDP session per
     page; re-arm on page recreation)
   - `input_mouse(kind,x,y,button,clicks,dx,dy)`, `input_key(kind,key)`
   - `go_back()`, `go_forward()`, `reload()`
   - `add_url_listener(cb)` → framenavigated push
2. `routes/browser_routes.py` (or websocket route registered in app.py if
   APIRouter WS mounting differs): `websocket /api/browser/ws` with cookie
   auth + privilege check; JSON protocol; screencast lifecycle tied to the
   connection.
3. `static/js/browserPanel.js` + `static/index.html`: `<canvas>` viewport
   (replaces iframe display path), WS client, input capture (focus model:
   click canvas to focus; Esc or clicking the address bar releases),
   coordinate scaling, address/history sync.

## Error handling

- Playwright/agent browser unavailable → panel shows the existing status
  message; no WS connect loop (single retry + message).
- WS drop → "stream disconnected — reload to reconnect" note.
- Input replay failures are logged server-side, never crash the stream.

## Testing

- Unit: input mapping + protocol handlers with a stubbed session; WS auth
  gate (403 without privilege/cookie when auth enabled).
- Live: drive the panel via Playwright-on-Apollo — load yahoo.com, observe
  frames painted (canvas non-blank), click a link via the canvas and see
  the URL change, type in a search box, scroll. Verify agent-tool browsing
  shows up in the panel.
