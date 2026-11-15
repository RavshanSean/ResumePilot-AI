#!/bin/sh
# Start from any working directory using this backend's installed environment.
set -eu
BACKEND_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PYTHON="$BACKEND_DIR/.venv/bin/python"
if [ ! -x "$PYTHON" ]; then
  echo "Backend environment missing. Run: python3 -m venv backend/.venv" >&2
  echo "Then: backend/.venv/bin/pip install -r backend/requirements.txt" >&2
  exit 1
fi
exec "$PYTHON" -m uvicorn app.main:app --app-dir "$BACKEND_DIR" --host 127.0.0.1 --port 8000
