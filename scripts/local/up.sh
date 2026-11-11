#!/usr/bin/env bash
# Start the local stack: DynamoDB Local, the moto server (Cognito, SSM), S3Mock and the Caddy edge; then create
# the tables and the other local resources. The API runs separately: scripts/local/api.sh (natively, with
# reload), or pass --image to run the API image Lambda runs, inside the stack.
#
#   scripts/local/up.sh            # stand-ins + edge; then run scripts/local/api.sh in another terminal
#   scripts/local/up.sh --image    # also build and start the API image (bundle from SBC_BUNDLE_FILE)
source "$(dirname "$0")/_common.sh"
need docker "Docker Desktop"; need uv "https://docs.astral.sh/uv/"; need curl "curl"
docker info >/dev/null 2>&1 || die "Docker isn't running: start Docker Desktop"

image=0
for arg in "$@"; do
  case "$arg" in
    --image) image=1 ;;
    *) die "usage: up.sh [--image]" ;;
  esac
done

mkdir -p "$RUNTIME_DIR" "$RUNTIME_DIR/logs" "$REPO_ROOT/apps/site"
touch "$RUNTIME_ENV"
if [ ! -f "$REPO_ROOT/apps/site/index.html" ]; then
  # A placeholder until scripts/local/web.sh builds the frontends.
  printf '<!doctype html><title>sbc local</title><p>The frontends are not built yet: run <code>scripts/local/web.sh</code>.</p>\n' \
    > "$REPO_ROOT/apps/site/index.html"
fi

say "starting the stand-ins and the edge"
if [ "$image" = 1 ]; then export SBC_API_UPSTREAM=api:8080; fi
"${COMPOSE[@]}" up -d dynamodb aws s3 edge
wait_for "DynamoDB Local" "http://localhost:${SBC_PORT_DYNAMODB}"
wait_for "moto" "http://localhost:${SBC_PORT_MOTO}/moto-api/"
wait_for "S3Mock" "http://localhost:${SBC_PORT_S3}"
wait_for "edge" "http://localhost:${SBC_PORT_EDGE}"

say "bootstrapping the local resources"
"$(dirname "$0")/bootstrap.sh"

if [ "$image" = 1 ]; then
  bundle="${SBC_BUNDLE_FILE:-$(basename "$(load_env; echo "$FACTS_BUNDLE_PATH")")}"
  [ -f "$REPO_ROOT/bundles/$bundle" ] || die "bundles/$bundle not found (set SBC_BUNDLE_FILE)"
  say "building and starting the API image with bundles/$bundle"
  SBC_BUNDLE_FILE="$bundle" "${COMPOSE[@]}" --env-file "$RUNTIME_ENV" --profile image up -d --build api
  wait_for "API" "http://localhost:${SBC_PORT_API}/api/healthz" 120
fi

"$(dirname "$0")/status.sh"
if [ "$image" = 0 ]; then
  say "next: scripts/local/api.sh   (the API on :${SBC_PORT_API}, served at http://localhost:${SBC_PORT_EDGE}/api)"
fi
