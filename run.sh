#!/usr/bin/env bash
# One-command local start: installs what's missing, builds the dashboard, starts the arena.
#   ./run.sh            -> http://localhost:8000
#   PORT=9000 ./run.sh
set -euo pipefail
cd "$(dirname "$0")"

if [ -f .env ]; then set -a; . ./.env; set +a; fi

PY=${PYTHON:-python3}
if [ ! -x backend/.venv/bin/python ]; then
  echo "» Creating Python virtualenv (needs Python 3.11+)"
  "$PY" -m venv backend/.venv
fi
echo "» Installing backend dependencies"
backend/.venv/bin/pip install -q -r backend/requirements.txt

if [ ! -f frontend/dist/index.html ] || [ "${REBUILD:-0}" = "1" ]; then
  echo "» Building dashboard (needs Node 20+)"
  (cd frontend && npm ci --silent && npm run build)
fi

echo "» Bot Battle running at http://localhost:${PORT:-8000}"
cd backend
exec .venv/bin/uvicorn app.main:app --host "${HOST:-0.0.0.0}" --port "${PORT:-8000}"
