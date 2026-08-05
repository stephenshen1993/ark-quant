#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
LABEL="ark_quant_server"
HOST="127.0.0.1"
PORT="8000"
LOG_FILE="$ROOT_DIR/logs/ark_quant_server.log"
PID_FILE="$ROOT_DIR/logs/ark_quant_server.pid"
PYTHON="$ROOT_DIR/.venv/bin/python"

mkdir -p "$ROOT_DIR/logs"

if command -v lsof >/dev/null 2>&1 && lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "ark-quant is already running at http://$HOST:$PORT"
  lsof -nP -iTCP:"$PORT" -sTCP:LISTEN
  exit 0
fi

rm -f "$PID_FILE"
touch "$LOG_FILE"

launchctl remove "$LABEL" >/dev/null 2>&1 || true
launchctl submit -l "$LABEL" -- /bin/zsh -lc \
  'cd "$1" && echo $$ > "$2" && exec "$3" run_app.py >> "$4" 2>&1' \
  "$LABEL" "$ROOT_DIR" "$PID_FILE" "$PYTHON" "$LOG_FILE"

for _ in {1..20}; do
  if command -v lsof >/dev/null 2>&1 && lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "ark-quant started: http://$HOST:$PORT"
    exit 0
  fi
  sleep 0.25
done

echo "ark-quant failed to start. Recent log:"
tail -40 "$LOG_FILE" || true
exit 1
