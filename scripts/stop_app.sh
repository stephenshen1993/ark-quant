#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
LABEL="ark_quant_server"
PORT="8000"
PID_FILE="$ROOT_DIR/logs/ark_quant_server.pid"

launchctl remove "$LABEL" >/dev/null 2>&1 || true

if command -v lsof >/dev/null 2>&1; then
  PIDS="$(lsof -tiTCP:"$PORT" -sTCP:LISTEN || true)"
  if [[ -n "$PIDS" ]]; then
    echo "$PIDS" | xargs kill
  fi
fi

rm -f "$PID_FILE"
echo "ark-quant stopped"
