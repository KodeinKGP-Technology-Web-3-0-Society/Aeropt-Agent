#!/usr/bin/env bash
# Local CD: if the agent is not already healthy on :8001, start it.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PORT="${AGENT_PORT:-8001}"
URL="http://127.0.0.1:${PORT}/health"
RUN_DIR="$ROOT/.local-run"
mkdir -p "$RUN_DIR"

if curl -sf --max-time 2 "$URL" >/dev/null; then
  echo "==> [agent] already healthy at $URL"
  exit 0
fi

if [[ -x .venv/bin/python ]]; then
  PYTHON=.venv/bin/python
elif [[ -x venv/bin/python ]]; then
  PYTHON=venv/bin/python
else
  PYTHON="${PYTHON:-python3}"
fi

echo "==> [agent] starting uvicorn on :${PORT}"
nohup "$PYTHON" -m uvicorn api_server:app --host 127.0.0.1 --port "$PORT" \
  >"$RUN_DIR/agent.log" 2>&1 &
echo $! >"$RUN_DIR/agent.pid"

for _ in $(seq 1 30); do
  if curl -sf --max-time 1 "$URL" >/dev/null; then
    echo "==> [agent] healthy at $URL (pid $(cat "$RUN_DIR/agent.pid"))"
    exit 0
  fi
  sleep 1
done

echo "agent failed to become healthy. Last log:" >&2
tail -n 40 "$RUN_DIR/agent.log" >&2
exit 1
