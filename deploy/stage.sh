# Sourced by the deploy/push_*.sh scripts.
#
#   stage_service agent|backend <dir>
#
# Fills <dir> with the service's last *commit* (git archive), never the working tree,
# so .env files and uncommitted edits cannot leak. For the agent it adds the query
# embedding model (gitignored); the corpus itself lives in the retrieval Postgres.
# Sets REPO to the service repo it staged.

AGENT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

stage_service() {
  local service="$1" stage="$2"
  shift 2
  case "$service" in
    agent)   REPO="$AGENT_ROOT" ;;
    backend) REPO="$(cd "$AGENT_ROOT/../Aeropt-backend" && pwd)" ;;
    *) echo "unknown service: $service (agent|backend)" >&2; return 2 ;;
  esac

  if [[ -n "$(git -C "$REPO" status --porcelain --untracked-files=no)" ]]; then
    echo "warning: $REPO has uncommitted changes; only the last commit is published." >&2
  fi

  git -C "$REPO" archive --format=tar HEAD | tar -x -C "$stage"

  if [[ "$service" == agent ]]; then
    mkdir -p "$stage/.local-run/models"
    cp -r "$REPO/.local-run/models/potion-base-8M" "$stage/.local-run/models/"
  fi
}
