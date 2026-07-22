#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
LABEL="ark_quant_server"
HOST="127.0.0.1"
PORT="8000"
LOG_FILE="$ROOT_DIR/logs/ark_quant_server.log"
PID_FILE="$ROOT_DIR/logs/ark_quant_server.pid"

if command -v lsof >/dev/null 2>&1 && lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "ark-quant is running at http://$HOST:$PORT"
  lsof -nP -iTCP:"$PORT" -sTCP:LISTEN
else
  echo "ark-quant is not running on http://$HOST:$PORT"
fi

if [[ -f "$PID_FILE" ]]; then
  echo "pid file: $PID_FILE ($(cat "$PID_FILE"))"
fi

if [[ -f "$LOG_FILE" ]]; then
  echo "log file: $LOG_FILE"
fi

launchctl print "gui/$(id -u)/$LABEL" >/dev/null 2>&1 && echo "launchctl label: $LABEL" || true
