#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
LABEL="ark_quant_server"
LEGACY_LABEL="codex.arkquant"
HOST="127.0.0.1"
PORT="8000"
LOG_FILE="$ROOT_DIR/logs/ark_quant_server.log"
PID_FILE="$ROOT_DIR/logs/ark_quant_server.pid"

LISTENER_PIDS=""
if command -v lsof >/dev/null 2>&1; then
  LISTENER_PIDS="$(lsof -tiTCP:"$PORT" -sTCP:LISTEN || true)"
fi

if [[ -n "$LISTENER_PIDS" ]]; then
  echo "ark-quant is running at http://$HOST:$PORT"
  lsof -nP -iTCP:"$PORT" -sTCP:LISTEN
else
  echo "ark-quant is not running on http://$HOST:$PORT"
fi

if [[ -f "$PID_FILE" ]]; then
  PID="$(cat "$PID_FILE")"
  if echo "$LISTENER_PIDS" | grep -qx "$PID"; then
    echo "pid file: $PID_FILE ($PID)"
  elif kill -0 "$PID" >/dev/null 2>&1; then
    echo "pid file: $PID_FILE ($PID, not listening on $PORT)"
  else
    echo "pid file: $PID_FILE ($PID, stale)"
  fi
fi

if [[ -f "$LOG_FILE" ]]; then
  echo "log file: $LOG_FILE"
fi

launchctl print "gui/$(id -u)/$LABEL" >/dev/null 2>&1 && echo "launchctl label: $LABEL" || true
launchctl print "gui/$(id -u)/$LEGACY_LABEL" >/dev/null 2>&1 && echo "legacy launchctl label: $LEGACY_LABEL" || true
