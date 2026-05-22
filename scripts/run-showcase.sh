#!/usr/bin/env bash
# Spin up the showcase stack: backend on :8090, frontend on :5174.
#
# The showcase reads from data/showcase/ (built by scripts/seed_showcase.py)
# and runs alongside your normal ./dev.sh on :8080/:5173 without conflict.
#
# Usage:
#   scripts/run-showcase.sh         # foreground, Ctrl-C to stop
#
# After this is up:
#   http://localhost:5174           — browse the showcase UI
#   http://localhost:8090           — showcase backend (API only)
#   (Re-)build covers/sessions by re-running scripts/seed_showcase.py

set -euo pipefail
cd "$(dirname "$0")/.."

# Make sure the showcase DB exists. If not, seed it.
if [ ! -f data/showcase/tome.db ]; then
  echo "No showcase DB found at data/showcase/tome.db — seeding…"
  source .venv/bin/activate
  python scripts/seed_showcase.py
fi

source .venv/bin/activate

echo "Starting showcase backend on :8090 (data/showcase/)"
TOME_SECRET_KEY=dev \
TOME_DATA_DIR=./data/showcase \
TOME_LIBRARY_DIR=./library/showcase \
TOME_INCOMING_DIR=./bindery/showcase \
  python -m uvicorn backend.main:app --port 8090 --host 0.0.0.0 &
BACKEND_PID=$!

echo "Starting showcase frontend on :5174 (proxying to :8090)"
( cd frontend && VITE_PORT=5174 VITE_API_TARGET=http://localhost:8090 npx vite ) &
FRONTEND_PID=$!

echo
echo "  Backend  : http://localhost:8090"
echo "  Frontend : http://localhost:5174"
echo "  Login    : benedict / showcase"
echo
echo "Ctrl-C to stop both."

cleanup() {
  echo
  echo "Stopping showcase…"
  kill "$BACKEND_PID" "$FRONTEND_PID" 2>/dev/null || true
  wait 2>/dev/null || true
}
trap cleanup INT TERM EXIT

wait
