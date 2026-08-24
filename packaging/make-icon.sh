#!/bin/bash
# Regenerate Apollo's app icons from packaging/apollo-icon.svg.
#
# Produces:
#   packaging/apollo.icns   — true multi-resolution macOS icon (16…1024, @1x/@2x)
#   static/icon-512.png     — PWA / web-app icon
#   static/icon-192.png     — PWA / web-app icon
#
# Run this after editing apollo-icon.svg, then commit the outputs — the macOS
# bundle build consumes the committed .icns rather than generating one, so a
# machine without rsvg-convert can still build a correctly-branded app.
#
# Requires: rsvg-convert (brew install librsvg) and macOS iconutil for .icns.
set -e

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SVG="$REPO_DIR/packaging/apollo-icon.svg"
ICONSET="$(mktemp -d)/Apollo.iconset"

if [ ! -f "$SVG" ]; then
  echo "ERROR: $SVG not found" >&2
  exit 1
fi
if ! command -v rsvg-convert >/dev/null 2>&1; then
  echo "ERROR: rsvg-convert not found (brew install librsvg)" >&2
  exit 1
fi

render() {  # render <size> <out>
  rsvg-convert -w "$1" -h "$1" "$SVG" -o "$2"
}

echo "==> PWA icons"
render 512 "$REPO_DIR/static/icon-512.png"
render 192 "$REPO_DIR/static/icon-192.png"
echo "    static/icon-512.png, static/icon-192.png"

if ! command -v iconutil >/dev/null 2>&1; then
  echo "==> iconutil not available (non-macOS) — skipping .icns"
  exit 0
fi

echo "==> macOS .icns"
mkdir -p "$ICONSET"
# Apple's required iconset members. Both @1x and @2x are needed or iconutil
# silently produces an icon that looks soft in the Dock at Retina scale.
for spec in "16 icon_16x16" "32 icon_16x16@2x" "32 icon_32x32" "64 icon_32x32@2x" \
            "128 icon_128x128" "256 icon_128x128@2x" "256 icon_256x256" \
            "512 icon_256x256@2x" "512 icon_512x512" "1024 icon_512x512@2x"; do
  set -- $spec
  render "$1" "$ICONSET/$2.png"
done
iconutil -c icns "$ICONSET" -o "$REPO_DIR/packaging/apollo.icns"
rm -rf "$(dirname "$ICONSET")"
echo "    packaging/apollo.icns ($(du -h "$REPO_DIR/packaging/apollo.icns" | cut -f1))"
