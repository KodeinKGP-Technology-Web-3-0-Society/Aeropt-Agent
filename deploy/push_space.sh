#!/usr/bin/env bash
# Publish a service to a Hugging Face Docker Space.
#
#   deploy/push_space.sh agent   <hf-user>/aeropt-agent [--include ncert] [--include hcverma]
#   deploy/push_space.sh backend <hf-user>/aeropt-api
#
# Ships the last *commit* (git archive), never the working tree, so .env files and
# uncommitted edits cannot leak. For the agent it adds the licence-filtered
# retrieval index (deploy/public_index.py) and the embedding model, which are
# gitignored. Needs `hf auth login` with a write token first.
set -euo pipefail

AGENT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SERVICE="${1:-}"
SPACE="${2:-}"
shift 2 || true

case "$SERVICE" in
  agent)   REPO="$AGENT_ROOT";                TITLE="Aeropt tutor agent"; PORT=7860 ;;
  backend) REPO="$AGENT_ROOT/../Aeropt-backend"; TITLE="Aeropt API";      PORT=8000 ;;
  *) echo "usage: $0 agent|backend <hf-user>/<space> [--include ncert|hcverma ...]" >&2; exit 2 ;;
esac
[[ "$SPACE" == */* ]] || { echo "Space id must look like <hf-user>/<space-name>" >&2; exit 2; }
REPO="$(cd "$REPO" && pwd)"

if [[ -n "$(git -C "$REPO" status --porcelain --untracked-files=no)" ]]; then
  echo "warning: $REPO has uncommitted changes; only the last commit is published." >&2
fi

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
git -C "$REPO" archive --format=tar HEAD | tar -x -C "$STAGE"

if [[ "$SERVICE" == agent ]]; then
  PY="$REPO/.venv/bin/python"; [[ -x "$PY" ]] || PY=python3
  "$PY" "$REPO/deploy/public_index.py" "$REPO/.local-run/index" "$STAGE/.local-run/index" "$@"
  mkdir -p "$STAGE/.local-run/models"
  cp -r "$REPO/.local-run/models/potion-base-8M" "$STAGE/.local-run/models/"
fi

# A Space reads its settings from README front matter; the service README is not that.
cat > "$STAGE/README.md" <<EOF
---
title: $TITLE
emoji: 📘
colorFrom: indigo
colorTo: blue
sdk: docker
app_port: $PORT
pinned: false
---

Deployed from \`$(basename "$REPO")\` @ $(git -C "$REPO" rev-parse --short HEAD) by deploy/push_space.sh.
Configuration lives in the Space settings (Variables and secrets), never in this repo.
EOF

hf repos create "$SPACE" --type space --sdk docker --public --exist-ok >/dev/null
hf upload "$SPACE" "$STAGE" . --repo-type space --delete "*" \
  --commit-message "Deploy $(basename "$REPO") $(git -C "$REPO" rev-parse --short HEAD)"
echo "Pushed. Build log: https://huggingface.co/spaces/$SPACE?logs=build"
