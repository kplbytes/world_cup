#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

python3 -m venv "$ROOT_DIR/backend/.venv"
"$ROOT_DIR/backend/.venv/bin/pip" install -e "$ROOT_DIR/backend[test]"
npm --prefix "$ROOT_DIR/frontend" install
npm --prefix "$ROOT_DIR/frontend" run build

echo "Setup complete. Run: $ROOT_DIR/scripts/start.sh"

