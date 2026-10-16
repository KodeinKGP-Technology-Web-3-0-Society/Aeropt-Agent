#!/usr/bin/env bash
# Publish a service to a Hugging Face Docker Space.
#
#   deploy/push_space.sh agent   <hf-user>/aeropt-agent
#   deploy/push_space.sh backend <hf-user>/aeropt-api
#
# Docker Spaces need a PRO subscription on HF (402 otherwise); deploy/push_render.sh
# is the free path. Needs `hf auth login` with a write token first.
set -euo pipefail
source "$(dirname "$0")/stage.sh"

SERVICE="${1:-}"
SPACE="${2:-}"
shift 2 || true

case "$SERVICE" in
  agent)   TITLE="Aeropt tutor agent"; PORT=7860 ;;
  backend) TITLE="Aeropt API";         PORT=8000 ;;
  *) echo "usage: $0 agent|backend <hf-user>/<space>" >&2; exit 2 ;;
esac
[[ "$SPACE" == */* ]] || { echo "Space id must look like <hf-user>/<space-name>" >&2; exit 2; }

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
stage_service "$SERVICE" "$STAGE"

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
