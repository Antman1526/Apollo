#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PYTHON="${PYTHON:-}"
if [[ -z "$PYTHON" ]]; then
  if [[ -x "$ROOT_DIR/venv/bin/python" ]]; then
    PYTHON="$ROOT_DIR/venv/bin/python"
  else
    PYTHON="python3"
  fi
fi

"$PYTHON" -m compileall -q app.py companion core routes services src scripts/apollo-ralph scripts/check-paperclip-browser
"$PYTHON" scripts/check_runtime_paths.py --root "$ROOT_DIR"
"$PYTHON" scripts/check_module_sizes.py
"$PYTHON" -m pytest -q
npm run test:js
if [[ "${APOLLO_STARTUP_SMOKE:-0}" == "1" ]]; then
  "$PYTHON" scripts/smoke_startup.py
fi
