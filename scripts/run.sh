#!/bin/bash
# Start the strudelbreaks FastAPI export server.
#
# The tempera template at strudel.cc POSTs captures payloads here; the
# server renders the chosen target via the modules under app/export/
# and streams the artifact back as a download.

set -e
cd "$(dirname "$0")/.."

if [ -z "$VIRTUAL_ENV" ] && [ -f "venv/bin/activate" ]; then
    source venv/bin/activate
fi

HOST="${STRUDELBREAKS_HTTP_HOST:-127.0.0.1}"
PORT="${STRUDELBREAKS_HTTP_PORT:-8000}"

echo "Starting strudelbreaks on http://${HOST}:${PORT}"
exec uvicorn app.main:app --host "$HOST" --port "$PORT" "$@"
