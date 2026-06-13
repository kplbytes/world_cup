#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

cleanup() {
  jobs -pr | xargs -r kill 2>/dev/null || true
}
trap cleanup EXIT INT TERM

"$ROOT_DIR/backend/.venv/bin/uvicorn" app.main:app \
  --app-dir "$ROOT_DIR/backend" \
  --host 127.0.0.1 \
  --port 8000 \
  --reload &
npm --prefix "$ROOT_DIR/frontend" run dev -- --host 127.0.0.1 &
wait

