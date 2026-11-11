# Shared settings for scripts/local (sourced, not run). docs/local/RUNBOOK.md explains each script.
# shellcheck disable=SC2034  # variables used by the scripts that source this file
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
STACK_ENV="${REPO_ROOT}/infra/local/stack.env"
RUNTIME_DIR="${REPO_ROOT}/.local"
RUNTIME_ENV="${RUNTIME_DIR}/runtime.env"
COMPOSE=(docker compose -f "${REPO_ROOT}/infra/local/compose.yaml")

export SBC_PORT_EDGE="${SBC_PORT_EDGE:-8088}" SBC_PORT_API="${SBC_PORT_API:-8080}"
export SBC_PORT_DYNAMODB="${SBC_PORT_DYNAMODB:-8000}" SBC_PORT_MOTO="${SBC_PORT_MOTO:-5050}"
export SBC_PORT_S3="${SBC_PORT_S3:-9090}"

say() { printf '\033[1m==> %s\033[0m\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }
need() { command -v "$1" >/dev/null || die "missing: $1 ($2)"; }

# load_env: the local stack's settings, then the generated ones, exported for the commands that follow.
# .env (secrets) is read by the API itself; values set here win over it.
load_env() {
  set -a
  # shellcheck source=/dev/null
  . "$STACK_ENV"
  # shellcheck source=/dev/null
  [ -f "$RUNTIME_ENV" ] && . "$RUNTIME_ENV"
  set +a
}

# http_up <url>: true when anything answers over HTTP (any status code).
http_up() { curl -s -o /dev/null -m 2 "$1"; }

# wait_for <name> <url> [seconds]
wait_for() {
  local name="$1" url="$2" deadline=$((SECONDS + ${3:-60}))
  until http_up "$url"; do
    [ "$SECONDS" -lt "$deadline" ] || die "$name didn't come up at $url"
    sleep 1
  done
}
