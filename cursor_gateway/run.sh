#!/usr/bin/env bash
# Launch helpers for running the tutor on Cursor models instead of Groq.
#   run.sh gateway   start the local gateway            (terminal 1)
#   run.sh agent     start the agent, wired to it       (terminal 2)
# Config comes from cursor_gateway/.env (gitignored) — see .env.example and README.md.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
ENV_FILE="$HERE/.env"

[[ -f "$ENV_FILE" ]] || { echo "Missing $ENV_FILE — copy .env.example to .env and set CURSOR_API_KEY." >&2; exit 1; }
set -a; source "$ENV_FILE"; set +a
: "${CURSOR_API_KEY:?CURSOR_API_KEY is empty in cursor_gateway/.env}"

# The token guards the gateway: anything that can reach it spends the Cursor plan.
if [[ -z "${GATEWAY_TOKEN:-}" ]]; then
  GATEWAY_TOKEN="$(python3 -c 'import secrets; print(secrets.token_hex(24))')"
  printf '\nGATEWAY_TOKEN=%s\n' "$GATEWAY_TOKEN" >> "$ENV_FILE"
  echo "Generated GATEWAY_TOKEN and saved it to cursor_gateway/.env"
fi

case "${1:-}" in
  gateway)
    cd "$HERE"
    [[ -d node_modules ]] || npm install --no-audit --no-fund
    exec npm start
    ;;
  agent)
    PORT="${PORT:-8787}"
    if ! curl -fsS -m 3 "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1; then
      echo "The gateway is not answering on 127.0.0.1:${PORT}. Start it first: make cursor-gateway" >&2
      exit 1
    fi
    # Every LLM call in the agent goes through the Groq SDK, which honours GROQ_BASE_URL.
    # GROQ_API_KEYS is set explicitly so the pool cannot pick a real Groq key out of .env
    # and send it to the gateway. The Cursor key itself is never given to the agent.
    export GROQ_BASE_URL="http://127.0.0.1:${PORT}"
    export GROQ_API_KEY="$GATEWAY_TOKEN" GROQ_API_KEYS="$GATEWAY_TOKEN"
    unset CURSOR_API_KEY
    # A Cursor tool step takes 10-25 s (Groq: ~2 s), so the default 45 s turn budget is too tight.
    export LEARNING_AGENT_TIME_BUDGET_S="${LEARNING_AGENT_TIME_BUDGET_S:-75}"
    cd "$ROOT"
    PY=python3; [[ -x .venv/bin/python ]] && PY=.venv/bin/python
    if [[ "${DRY_RUN:-}" == "1" ]]; then   # show what the agent would run with, secrets masked
      echo "GROQ_BASE_URL=$GROQ_BASE_URL"
      echo "GROQ_API_KEY=${GROQ_API_KEY:0:4}…(masked, ${#GROQ_API_KEY} chars)   GROQ_API_KEYS same value"
      echo "LEARNING_AGENT_TIME_BUDGET_S=$LEARNING_AGENT_TIME_BUDGET_S"
      echo "CURSOR_API_KEY in agent env: ${CURSOR_API_KEY:-<unset, good>}"
      echo "would run: $PY -m uvicorn api_server:app --port 8001"
      exit 0
    fi
    exec "$PY" -m uvicorn api_server:app --port 8001
    ;;
  *)
    echo "usage: run.sh gateway|agent" >&2; exit 2 ;;
esac
