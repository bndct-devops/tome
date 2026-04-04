#!/bin/bash
# Start Tome dev servers (backend + frontend)
# Usage: ./dev.sh

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Kill any existing Tome dev servers (uvicorn on 8080, vite/node on 5173)
lsof -ti:8080 | while read pid; do
  cmd=$(ps -p "$pid" -o comm= 2>/dev/null)
  [[ "$cmd" == *python* || "$cmd" == *uvicorn* ]] && kill -9 "$pid" 2>/dev/null
done
lsof -ti:5173 | while read pid; do
  cmd=$(ps -p "$pid" -o comm= 2>/dev/null)
  [[ "$cmd" == *node* || "$cmd" == *vite* ]] && kill -9 "$pid" 2>/dev/null
done
sleep 1

echo "📚 Starting Tome dev servers..."

# Backend
cd "$SCRIPT_DIR"
source .venv/bin/activate
mkdir -p data library bindery bindery/chapters

# Load .env if present (for optional tokens like TOME_HARDCOVER_TOKEN)
if [ -f "$SCRIPT_DIR/.env" ]; then
  set -a
  source "$SCRIPT_DIR/.env"
  set +a
fi

TOME_SECRET_KEY=dev TOME_DATA_DIR=./data TOME_LIBRARY_DIR=./library TOME_INCOMING_DIR=./bindery \
  python -m uvicorn backend.main:app --host 0.0.0.0 --port 8080 --reload --log-level info > /tmp/tome-backend.log 2>&1 < /dev/null &
BACKEND_PID=$!
disown $BACKEND_PID
echo "  ✓ Backend PID $BACKEND_PID → http://localhost:8080/api/docs"

# Frontend (Vite HMR handles live reload)
cd "$SCRIPT_DIR/frontend"
npm run dev -- --port 5173 > /tmp/tome-vite.log 2>&1 < /dev/null &
FRONTEND_PID=$!
disown $FRONTEND_PID
echo "  ✓ Frontend PID $FRONTEND_PID → http://localhost:5173"

echo ""
echo "  Logs: tail -f /tmp/tome-backend.log"
echo "  Stop: kill $BACKEND_PID $FRONTEND_PID"
echo ""
echo "  ➜ Open http://localhost:5173"
echo ""
