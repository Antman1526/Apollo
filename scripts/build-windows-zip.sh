#!/usr/bin/env bash
# build-windows-zip.sh — package the source tree for a native Windows install
# (no Docker) as Apollo-Windows.zip.
#
#   ./scripts/build-windows-zip.sh [ref] [dest-dir]
#
#   ref        git ref to package        (default: origin/main)
#   dest-dir   where to write the zip    (default: $HOME/Desktop)
#
# Packaging rules, and why they are what they are:
#
#   * Built with `git archive`, so ONLY TRACKED files ship. Untracked secrets
#     (.env), the virtualenv, runtime data/ and logs/, and build artifacts
#     therefore cannot leak into the zip by construction rather than by a
#     hand-maintained exclude list that some future edit forgets to update.
#
#   * Everything the ref tracks is included verbatim except the two drops
#     below. This matters: an earlier hand-rolled build applied .gitignore
#     patterns WITHOUT their `!` negation rules and silently dropped tracked
#     runtime files — including static/js/editor/build/*.js, which
#     galleryEditor.js imports, so the gallery editor failed to load. The
#     REQUIRED list below is a regression guard against exactly that.
#
#   * Dropped: macOS/Linux-only launchers (useless on Windows) and docs/
#     (~19MB of demo media, not runtime code).
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REF="${1:-origin/main}"
DEST_DIR="${2:-$HOME/Desktop}"
NAME="Apollo-Windows"

# Tracked paths that exist only to run Apollo on macOS/Linux.
DROP_PATHS=(
  start-macos.sh
  build-macos-app.sh
  build-macos-bundle.sh
  install-service.sh
  apollo-ui.service
  docs
)

# Top-level paths that must NEVER appear in the archive. Checked exactly (not
# recursively) so that e.g. static/js/editor/build/ is unaffected by "build".
FORBIDDEN_PATHS=(
  .env .git venv data logs dist build
  SECURITY-FIXLIST.local.md .apollo .claude .pytest_cache
)

# Tracked files whose absence has broken a shipped zip before, or that the
# Windows install cannot work without. A missing entry fails the build loudly
# instead of shipping a subtly broken archive.
REQUIRED_FILES=(
  launch-windows.ps1
  update_windows.bat
  WINDOWS-SETUP.md
  requirements.txt
  app.py
  setup.py
  services/hwfit/data/hf_models.json
  services/docs/service.py
  static/js/editor/build/toolbar.js
  static/js/editor/build/controls.js
  static/js/editor/build/popups.js
)

command -v zip >/dev/null || { echo "error: 'zip' is not installed" >&2; exit 1; }
git -C "$ROOT_DIR" rev-parse --verify --quiet "$REF" >/dev/null \
  || { echo "error: '$REF' is not a valid git ref" >&2; exit 1; }

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
BUILD="$STAGE/$NAME"
mkdir -p "$BUILD"

echo "Packaging $(git -C "$ROOT_DIR" rev-parse --short "$REF") ($REF)"
git -C "$ROOT_DIR" archive "$REF" | tar -x -C "$BUILD"

for path in "${DROP_PATHS[@]}"; do
  rm -rf "${BUILD:?}/$path"
done

# --- gates -----------------------------------------------------------------
fail=0

for path in "${FORBIDDEN_PATHS[@]}"; do
  if [[ -e "$BUILD/$path" ]]; then
    echo "error: forbidden path in archive: $path" >&2
    fail=1
  fi
done

# Defence in depth: catch secret-shaped files even if something above changes.
while IFS= read -r hit; do
  echo "error: secret-shaped file in archive: ${hit#"$BUILD/"}" >&2
  fail=1
done < <(find "$BUILD" -type f \( -name '*.key' -o -name '*.pem' -o -name '.app_key' \
  -o -name '*.sqlite' -o -name '*.sqlite3' -o -name '*.db' \) 2>/dev/null)

for path in "${REQUIRED_FILES[@]}"; do
  if [[ ! -f "$BUILD/$path" ]]; then
    echo "error: required file missing from archive: $path" >&2
    fail=1
  fi
done

[[ "$fail" -eq 0 ]] || { echo "build aborted: gate failures above" >&2; exit 1; }

# --- package ---------------------------------------------------------------
( cd "$STAGE" && zip -qr "$NAME.zip" "$NAME" \
    -x '*.DS_Store' -x '*__pycache__*' -x '*.pyc' )
unzip -tq "$STAGE/$NAME.zip" >/dev/null \
  || { echo "error: archive failed its integrity check" >&2; exit 1; }

mkdir -p "$DEST_DIR"
mv -f "$STAGE/$NAME.zip" "$DEST_DIR/$NAME.zip"

printf 'Wrote %s (%s, %s files)\n' \
  "$DEST_DIR/$NAME.zip" \
  "$(du -h "$DEST_DIR/$NAME.zip" | cut -f1 | tr -d ' ')" \
  "$(find "$BUILD" -type f | wc -l | tr -d ' ')"
