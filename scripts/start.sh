#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ ! -x "$ROOT_DIR/backend/.venv/bin/uvicorn" ]]; then
  echo "Python environment is missing. Run ./scripts/setup.sh first." >&2
  exit 1
fi

if [[ ! -f "$ROOT_DIR/frontend/dist/index.html" ]]; then
  npm --prefix "$ROOT_DIR/frontend" run build
fi

exec "$ROOT_DIR/backend/.venv/bin/uvicorn" app.main:app \
  --app-dir "$ROOT_DIR/backend" \
  --host 127.0.0.1 \
  --port "${PORT:-8000}"

