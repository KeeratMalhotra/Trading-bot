#!/usr/bin/env bash
# One-command local start: installs what's missing, builds the dashboard, starts QUORUM.
#   ./run.sh            -> http://localhost:8000
#   PORT=9000 ./run.sh
# Runs until you press Ctrl-C. If the server ever exits it restarts after 5 seconds, and on
# macOS the computer is kept awake while it runs (for 24/7 streams).
set -euo pipefail
cd "$(dirname "$0")"

if [ -f .env ]; then set -a; . ./.env; set +a; fi

PY=${PYTHON:-python3}
if [ ! -x backend/.venv/bin/python ]; then
  if ! "$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
    echo "QUORUM needs Python 3.11 or newer ($PY is $("$PY" -V 2>&1 || echo missing))."
    echo "Install it, then run: PYTHON=python3.11 ./run.sh"
    exit 1
  fi
  echo "» Creating Python virtualenv"
  "$PY" -m venv backend/.venv
fi
if ! backend/.venv/bin/python -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then
  echo "backend/.venv was made with Python older than 3.11. Delete it and run: PYTHON=python3.11 ./run.sh"
  exit 1
fi
echo "» Installing backend dependencies"
backend/.venv/bin/pip install -q -r backend/requirements.txt

if [ ! -f frontend/dist/index.html ] || [ "${REBUILD:-0}" = "1" ]; then
  echo "» Building dashboard (needs Node 20+)"
  (cd frontend && npm ci --silent && npm run build)
fi

KEEP_AWAKE=()
if [ "$(uname)" = "Darwin" ] && command -v caffeinate >/dev/null; then
  KEEP_AWAKE=(caffeinate -ims)    # no idle/system sleep while QUORUM runs (the display may still sleep)
fi

echo "» QUORUM running at http://localhost:${PORT:-8000}  (Ctrl-C to stop)"
cd backend
trap 'echo; echo "» Stopped"; exit 0' INT TERM
while true; do
  ${KEEP_AWAKE[@]+"${KEEP_AWAKE[@]}"} .venv/bin/uvicorn app.main:app --host "${HOST:-0.0.0.0}" --port "${PORT:-8000}" || true
  echo "» Server exited; restarting in 5 seconds (Ctrl-C to quit)"
  sleep 5
done
