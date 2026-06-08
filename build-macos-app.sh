#!/bin/bash
# Build a downloadable macOS launcher app + .dmg for Apollo.
#
#   ./build-macos-app.sh
#
# Produces:
#   dist/Apollo.app   — double-click: starts the local server (using this
#                         repo's venv) and opens the UI in an app-style window.
#   dist/Apollo.dmg   — drag-to-Applications disk image (the downloadable).
#
# This is a *launcher* wrapper: it drives the venv we set up in this repo, it
# does not bundle Python. The install path is baked into the app at build time,
# so rebuild if you move the repo. Override the port with APOLLO_PORT.
set -e

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_NAME="Apollo"
INSTALL_DIR="$REPO_DIR"
PORT="${APOLLO_PORT:-7860}"
DIST="$REPO_DIR/dist"
APP="$DIST/$APP_NAME.app"

echo "Building $APP_NAME.app"
echo "  install dir: $INSTALL_DIR"
echo "  port:        $PORT"

# ── Bootstrap the venv if missing, so this is a one-command build from a fresh
#    clone. The clickable app drives this same venv at runtime. Skip with
#    APOLLO_SKIP_VENV=1 if you manage the environment yourself.
if [ -z "$APOLLO_SKIP_VENV" ] && [ ! -x "$INSTALL_DIR/venv/bin/uvicorn" ]; then
  echo "  venv:        not found — setting it up (first build only)…"
  # Prefer an arm64 Homebrew Python on Apple Silicon (matches start-macos.sh);
  # fall back to whatever python3.11+ is on PATH elsewhere.
  PY=""
  if [ "$(uname -m)" = "arm64" ]; then
    cands="/opt/homebrew/bin/python3.13 /opt/homebrew/bin/python3.12 /opt/homebrew/bin/python3.11"
  else
    cands="python3.13 python3.12 python3.11 python3"
  fi
  for cand in $cands; do
    p="$(command -v "$cand" 2>/dev/null)" || continue
    if "$p" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] >= (3, 11) else 1)' 2>/dev/null; then
      PY="$p"; break
    fi
  done
  if [ -z "$PY" ]; then
    echo "  ✗ Need Python 3.11+ to build the venv. Install it (e.g. brew install python@3.11)"
    echo "    or run ./start-macos.sh first, then re-run this script."
    exit 1
  fi
  echo "  python:      $("$PY" --version 2>&1) ($PY)"
  "$PY" -m venv "$INSTALL_DIR/venv"
  "$INSTALL_DIR/venv/bin/python" -m pip install --quiet --upgrade pip
  "$INSTALL_DIR/venv/bin/python" -m pip install -r "$INSTALL_DIR/requirements.txt"
  APOLLO_SKIP_RUN_HINT=1 "$INSTALL_DIR/venv/bin/python" "$INSTALL_DIR/setup.py" || true
  echo "  venv:        ready"
else
  echo "  venv:        $( [ -x "$INSTALL_DIR/venv/bin/uvicorn" ] && echo reusing existing || echo skipped )"
fi

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

# ── Icon (best effort) — center-crop docs/apollo.jpg to a square .icns ──
if [ -f "$REPO_DIR/docs/apollo.jpg" ] && command -v sips >/dev/null 2>&1; then
  TMPIMG="$(mktemp -d)"
  # Center-crop to a square, scale to 512 (sips' icns encoder caps at 512), and
  # let sips emit the .icns directly — more robust across macOS versions than
  # building an .iconset by hand.
  sips -c 720 720 "$REPO_DIR/docs/apollo.jpg" --out "$TMPIMG/sq.png" >/dev/null 2>&1 || cp "$REPO_DIR/docs/apollo.jpg" "$TMPIMG/sq.png"
  sips -z 512 512 "$TMPIMG/sq.png" --out "$TMPIMG/icon.png" >/dev/null 2>&1
  if sips -s format icns "$TMPIMG/icon.png" --out "$APP/Contents/Resources/apollo.icns" >/dev/null 2>&1; then
    echo "  icon:        apollo.icns"
  else
    echo "  icon:        (skipped — conversion failed)"
  fi
  rm -rf "$TMPIMG"
else
  echo "  icon:        (skipped — no docs/apollo.jpg)"
fi

# ── Info.plist ──
cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>            <string>$APP_NAME</string>
    <key>CFBundleDisplayName</key>     <string>$APP_NAME</string>
    <key>CFBundleIdentifier</key>      <string>com.apollo.launcher</string>
    <key>CFBundleVersion</key>         <string>1.0</string>
    <key>CFBundleShortVersionString</key><string>1.0</string>
    <key>CFBundlePackageType</key>     <string>APPL</string>
    <key>CFBundleExecutable</key>      <string>$APP_NAME</string>
    <key>CFBundleIconFile</key>        <string>apollo</string>
    <key>LSMinimumSystemVersion</key>  <string>11.0</string>
    <key>NSHighResolutionCapable</key> <true/>
    <key>LSUIElement</key>             <false/>
</dict>
</plist>
PLIST

# ── Launcher executable (placeholders filled below) ──
cat > "$APP/Contents/MacOS/$APP_NAME.tmpl" <<'LAUNCHER'
#!/bin/bash
# Apollo.app — start the local server and open the UI in an app window.
INSTALL_DIR="__INSTALL_DIR__"
PORT="__PORT__"
URL="http://127.0.0.1:${PORT}"
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"
# Run Paperclip in native mode (Apollo supervises it + auto-provisions Node when
# the feature is enabled in Settings/.env). No-op unless PAPERCLIP_ENABLED=true.
export PAPERCLIP_MODE="${PAPERCLIP_MODE:-native}"

UVICORN="$INSTALL_DIR/venv/bin/uvicorn"
LOG="$INSTALL_DIR/logs/apollo-app.log"

notify() { /usr/bin/osascript -e "display notification \"$1\" with title \"Apollo\"" >/dev/null 2>&1; }
die_gui() {
  /usr/bin/osascript -e "display dialog \"$1\" with title \"Apollo\" buttons {\"OK\"} default button 1 with icon stop" >/dev/null 2>&1
  exit 1
}

[ -x "$UVICORN" ] || die_gui "Apollo isn't set up yet. Open Terminal and run:

cd $INSTALL_DIR
python3.11 -m venv venv
./venv/bin/pip install -r requirements.txt
./venv/bin/python setup.py"

# Open the UI in a chrome-less app window (Chromium browsers), else default browser.
open_ui() {
  local b base exe bin
  for b in "Google Chrome" "Microsoft Edge" "Brave Browser" "Chromium"; do
    for base in "/Applications" "$HOME/Applications"; do
      if [ -d "$base/$b.app" ]; then
        exe="$(/usr/bin/defaults read "$base/$b.app/Contents/Info" CFBundleExecutable 2>/dev/null)"
        bin="$base/$b.app/Contents/MacOS/$exe"
        if [ -x "$bin" ]; then
          "$bin" --app="$URL" --new-window >/dev/null 2>&1 &
          return 0
        fi
      fi
    done
  done
  /usr/bin/open "$URL"
}

mkdir -p "$INSTALL_DIR/logs"

# Already running? Just open the UI.
if /usr/bin/curl -s -o /dev/null --max-time 2 "$URL"; then
  open_ui
  exit 0
fi

notify "Starting…"
cd "$INSTALL_DIR" || die_gui "Install folder not found: $INSTALL_DIR"
if [ "$(uname -m)" = "arm64" ]; then
  arch -arm64 "$UVICORN" app:app --host 127.0.0.1 --port "$PORT" >>"$LOG" 2>&1 &
else
  "$UVICORN" app:app --host 127.0.0.1 --port "$PORT" >>"$LOG" 2>&1 &
fi
SERVER_PID=$!

# Quitting the app stops the server it started.
trap 'kill $SERVER_PID 2>/dev/null; exit 0' TERM INT

# Wait for readiness (first run downloads an embedding model — allow ~2 min).
READY=0
for i in $(seq 1 120); do
  /usr/bin/curl -s -o /dev/null --max-time 2 "$URL" && { READY=1; break; }
  kill -0 "$SERVER_PID" 2>/dev/null || die_gui "Apollo failed to start. Log:
$LOG"
  sleep 1
done

if [ "$READY" = "1" ]; then
  open_ui
else
  notify "Apollo is taking a while — open $URL once it finishes starting."
fi
wait "$SERVER_PID"
LAUNCHER

sed -e "s|__INSTALL_DIR__|$INSTALL_DIR|g" -e "s|__PORT__|$PORT|g" \
    "$APP/Contents/MacOS/$APP_NAME.tmpl" > "$APP/Contents/MacOS/$APP_NAME"
rm -f "$APP/Contents/MacOS/$APP_NAME.tmpl"
chmod +x "$APP/Contents/MacOS/$APP_NAME"

# Refresh Finder's icon cache for the new bundle.
touch "$APP"

# ── .dmg (drag-to-Applications) ──
echo "Packaging dist/$APP_NAME.dmg"
STAGE="$(mktemp -d)/dmg"
mkdir -p "$STAGE"
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
rm -f "$DIST/$APP_NAME.dmg"
hdiutil create -volname "$APP_NAME" -srcfolder "$STAGE" -ov -format UDZO "$DIST/$APP_NAME.dmg" >/dev/null
rm -rf "$STAGE"

echo ""
echo "Done:"
echo "  $APP"
echo "  $DIST/$APP_NAME.dmg"
echo ""
echo "Run it:        open '$APP'"
echo "Install:       open '$DIST/$APP_NAME.dmg'  (drag Apollo to Applications)"
