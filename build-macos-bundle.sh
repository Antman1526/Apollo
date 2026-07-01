#!/bin/bash
# Build a SELF-CONTAINED macOS app + .dmg for Apollo.
#
#   ./build-macos-bundle.sh
#
# Unlike build-macos-app.sh (a launcher that drives this repo's venv), this
# bundles Python + all dependencies via PyInstaller so the resulting Apollo.app
# runs on any Apple-Silicon Mac WITHOUT the repo or a preinstalled venv.
#
# Produces:
#   dist/Apollo.app   — double-click: starts the bundled server, opens the UI.
#   dist/Apollo.dmg   — drag-to-Applications disk image.
#
# Requirements to BUILD (not to run): a working venv at ./venv with the app's
# deps + pyinstaller installed. Override the port with APOLLO_PORT.
set -e

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_NAME="Apollo"
PORT="${APOLLO_PORT:-7860}"
DIST="$REPO_DIR/dist"
APP="$DIST/$APP_NAME.app"
VENV="$REPO_DIR/venv"
ONEDIR="$DIST/apollo"          # PyInstaller COLLECT output (name=apollo)
EXE_NAME="apollo"              # PyInstaller EXE name

echo "Building self-contained $APP_NAME.app"
echo "  repo:  $REPO_DIR"
echo "  port:  $PORT"

# ── 1. Ensure pyinstaller is available in the venv ──
if [ ! -x "$VENV/bin/pyinstaller" ]; then
  echo "  pyinstaller: installing into venv…"
  "$VENV/bin/python" -m pip install --quiet pyinstaller
fi

# ── 2. PyInstaller onedir build (arm64) ──
echo "  pyinstaller: building onedir (this takes a few minutes)…"
rm -rf "$REPO_DIR/build" "$ONEDIR"
( cd "$REPO_DIR" && "$VENV/bin/pyinstaller" packaging/apollo.spec \
    --noconfirm --distpath "$DIST" --workpath "$REPO_DIR/build" )

if [ ! -x "$ONEDIR/$EXE_NAME" ]; then
  echo "  ✗ PyInstaller did not produce $ONEDIR/$EXE_NAME" >&2
  exit 1
fi
echo "  pyinstaller: onedir ready ($(du -sh "$ONEDIR" | cut -f1))"

# ── 3. Assemble Apollo.app around the onedir ──
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

# Ship the whole PyInstaller onedir under Contents/Resources/apollo.
cp -R "$ONEDIR" "$APP/Contents/Resources/apollo"

# ── Icon (best effort) — center-crop docs/apollo.jpg to a square .icns ──
if [ -f "$REPO_DIR/docs/apollo.jpg" ] && command -v sips >/dev/null 2>&1; then
  TMPIMG="$(mktemp -d)"
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
    <key>CFBundleIdentifier</key>      <string>com.apollo.bundle</string>
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

# ── Launcher: start the bundled server, open the UI, stop server on quit ──
cat > "$APP/Contents/MacOS/$APP_NAME.tmpl" <<'LAUNCHER'
#!/bin/bash
# Apollo.app — self-contained. Starts the bundled server and opens the UI.
PORT="__PORT__"
URL="http://127.0.0.1:${PORT}"
HERE="$(cd "$(dirname "$0")" && pwd)"                 # Contents/MacOS
RES="$(cd "$HERE/../Resources" && pwd)"               # Contents/Resources
SERVER="$RES/apollo/apollo"                           # PyInstaller exe
HOME_DIR="$HOME/Library/Application Support/Apollo"
LOG="$HOME_DIR/apollo-app.log"

export APOLLO_PORT="$PORT"

notify() { /usr/bin/osascript -e "display notification \"$1\" with title \"Apollo\"" >/dev/null 2>&1; }
die_gui() {
  /usr/bin/osascript -e "display dialog \"$1\" with title \"Apollo\" buttons {\"OK\"} default button 1 with icon stop" >/dev/null 2>&1
  exit 1
}

[ -x "$SERVER" ] || die_gui "Apollo bundle is missing its server binary."

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

mkdir -p "$HOME_DIR"

# Already running? Just open the UI.
if /usr/bin/curl -s -o /dev/null --max-time 2 "$URL"; then
  open_ui
  exit 0
fi

notify "Starting…"
"$SERVER" >>"$LOG" 2>&1 &
SERVER_PID=$!
trap 'kill $SERVER_PID 2>/dev/null; exit 0' TERM INT

# First run downloads the FastEmbed embedding model — allow ~3 min.
READY=0
for i in $(seq 1 180); do
  /usr/bin/curl -s -o /dev/null --max-time 2 "$URL/api/health" && { READY=1; break; }
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

sed -e "s|__PORT__|$PORT|g" \
    "$APP/Contents/MacOS/$APP_NAME.tmpl" > "$APP/Contents/MacOS/$APP_NAME"
rm -f "$APP/Contents/MacOS/$APP_NAME.tmpl"
chmod +x "$APP/Contents/MacOS/$APP_NAME"

# Ad-hoc codesign so Gatekeeper allows launch on the build machine.
codesign --force --deep --sign - "$APP" >/dev/null 2>&1 || \
  echo "  codesign:    (skipped — ad-hoc signing failed)"

touch "$APP"
echo "  app:         $(du -sh "$APP" | cut -f1)  $APP"

# ── .dmg (drag-to-Applications) ──
echo "Packaging dist/$APP_NAME.dmg"
STAGE="$(mktemp -d)/dmg"
mkdir -p "$STAGE"
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
rm -f "$DIST/$APP_NAME.dmg"
hdiutil create -volname "$APP_NAME" -srcfolder "$STAGE" -ov -format UDZO "$DIST/$APP_NAME.dmg" >/dev/null
rm -rf "$STAGE"
echo "  dmg:         $(du -sh "$DIST/$APP_NAME.dmg" | cut -f1)  $DIST/$APP_NAME.dmg"

echo ""
echo "Done:"
echo "  $APP"
echo "  $DIST/$APP_NAME.dmg"
