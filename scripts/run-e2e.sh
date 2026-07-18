#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PYTHON:-$ROOT_DIR/venv/bin/python}"
[[ -x "$PYTHON" ]] || PYTHON=python3
TMP_DIR="$(mktemp -d)"
PORT="$($PYTHON - <<'PY'
import socket
s = socket.socket(); s.bind(('127.0.0.1', 0)); print(s.getsockname()[1]); s.close()
PY
)"
CHROMIUM="${APOLLO_E2E_CHROMIUM:-$(find "$HOME/Library/Caches/ms-playwright" -type f -name 'Google Chrome for Testing' -print -quit 2>/dev/null || true)}"
[[ -n "$CHROMIUM" ]] || { echo "Chromium is unavailable; run: python -m playwright install chromium"; exit 1; }
cleanup() { kill "${SERVER_PID:-}" 2>/dev/null || true; rm -rf "$TMP_DIR"; }
trap cleanup EXIT
AUTH_ENABLED=false DATABASE_URL="sqlite:///$TMP_DIR/apollo.db" "$PYTHON" -m uvicorn app:app --host 127.0.0.1 --port "$PORT" >"$TMP_DIR/server.log" 2>&1 &
SERVER_PID=$!
for _ in $(seq 1 60); do curl -fsS "http://127.0.0.1:$PORT/" >/dev/null && break; sleep 1; done
curl -fsS "http://127.0.0.1:$PORT/" >/dev/null || { cat "$TMP_DIR/server.log"; exit 1; }
APOLLO_E2E_BASE_URL="http://127.0.0.1:$PORT/" APOLLO_E2E_CHROMIUM="$CHROMIUM" "$PYTHON" -m pytest -q tests/e2e
