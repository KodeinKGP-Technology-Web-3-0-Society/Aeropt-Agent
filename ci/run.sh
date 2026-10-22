#!/usr/bin/env bash
# Local + GitHub CI for Aeropt-agent. Same script in both places.
# Does not call Groq/Gemini. Live checks stay opt-in (RUN_LIVE_GROQ_CHECKS=1).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ -x .venv/bin/python ]]; then
  PYTHON=.venv/bin/python
elif [[ -x venv/bin/python ]]; then
  PYTHON=venv/bin/python
else
  PYTHON="${PYTHON:-python3}"
fi

if [[ ! -f .env ]]; then
  export SUPABASE_URL="${SUPABASE_URL:-https://ci.invalid.supabase.co}"
  export SUPABASE_KEY="${SUPABASE_KEY:-ci-key}"
  export GROQ_API_KEY="${GROQ_API_KEY:-gsk_ci}"
  export GEMINI_API_KEY="${GEMINI_API_KEY:-ci-key}"
fi
export GEMINI_MODEL="${GEMINI_MODEL:-gemini-3-flash-preview}"

echo "==> [agent] syntax"
"$PYTHON" -m compileall -q \
  -x '(Aeropt-backend-main|Aeropt-frontend-main|scratch|\.venv|venv)' \
  agents graph mcp_tools models services utils db retrieval local_ingest \
  api_server.py config.py main.py

echo "==> [agent] tests"
"$PYTHON" -m pytest tests/

echo "==> [agent] evals (deterministic replay)"
"$PYTHON" -m evals.runner --mode replay

echo "==> [agent] CI passed"
