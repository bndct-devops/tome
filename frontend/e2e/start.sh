#!/usr/bin/env bash
# Boot a throwaway Tome instance for the E2E suite.
#
# Builds the frontend (the backend serves frontend/dist), prepares a fresh
# data/library sandbox under e2e/.data, and execs uvicorn on port 8199.
# Launched and torn down by Playwright's webServer config.
set -euo pipefail

cd "$(dirname "$0")/.."   # frontend/
npm run build

E2E_DIR="$(pwd)/e2e/.data"
rm -rf "$E2E_DIR"
mkdir -p "$E2E_DIR/data" "$E2E_DIR/library" "$E2E_DIR/bindery"

ROOT="$(cd .. && pwd)"
PY="${E2E_PYTHON:-$ROOT/.venv/bin/python}"

export TOME_SECRET_KEY=e2e-secret
export TOME_DATA_DIR="$E2E_DIR/data"
export TOME_LIBRARY_DIR="$E2E_DIR/library"
export TOME_INCOMING_DIR="$E2E_DIR/bindery"
export TOME_UPDATE_CHECK=false

cd "$ROOT"
exec "$PY" -m uvicorn backend.main:app --port 8199
