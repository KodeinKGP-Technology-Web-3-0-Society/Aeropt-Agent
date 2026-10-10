#!/usr/bin/env bash
# Publish a service to a deploy-only git repo that a Render web service builds from.
#
#   deploy/push_render.sh agent   <git-url-of-empty-private-repo> [--include ncert] [--include hcverma]
#   deploy/push_render.sh backend <git-url-of-empty-private-repo>
#
# Render builds from git, and the agent's index and model are gitignored, so the
# service repo itself cannot be built there. Each push is one orphan commit,
# force-pushed to `main`: the deploy repo keeps no history, and Render redeploys
# on every push. Keep the deploy repo private.
set -euo pipefail
source "$(dirname "$0")/stage.sh"

SERVICE="${1:-}"
REMOTE="${2:-}"
shift 2 || true
[[ "$SERVICE" == agent || "$SERVICE" == backend ]] && [[ -n "$REMOTE" ]] \
  || { echo "usage: $0 agent|backend <git-url> [--include ncert|hcverma ...]" >&2; exit 2; }

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
stage_service "$SERVICE" "$STAGE" "$@"

SHA="$(git -C "$REPO" rev-parse --short HEAD)"
git -C "$STAGE" init -q -b main
# --force: the service's .gitignore excludes .local-run, which is exactly what ships.
git -C "$STAGE" add -A --force
git -C "$STAGE" commit -q -m "Deploy $(basename "$REPO") $SHA"
git -C "$STAGE" push -q --force "$REMOTE" main
echo "Pushed $(basename "$REPO") $SHA to $REMOTE; Render rebuilds on push."
