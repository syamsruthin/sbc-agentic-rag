#!/usr/bin/env bash
# Build the three frontends with the local stack's settings and put them where the edge serves them
# (apps/site/, at http://localhost:8088), as scripts/aws/deploy-web.sh does for the web bucket:
#
#   scripts/local/web.sh          # build + assemble + /landing/demo.json for demo-broker
#   scripts/local/web.sh --dev    # instead: the three Vite dev servers with hot reload, at http://localhost:5173
#
# The settings are public by design (as on AWS): the local pool and client IDs from .local/runtime.env, the
# Cognito stand-in's URL (VITE_COGNITO_ENDPOINT, local only) and Cloudflare's always-pass Turnstile test site key.
source "$(dirname "$0")/_common.sh"
need npm "Node.js 20.10+, Node 22 recommended"
load_env
[ -n "${COGNITO_USER_POOL_ID:-}" ] || die "no Cognito settings in .local/runtime.env: run scripts/local/up.sh first"

export VITE_API_BASE_URL=/api
export VITE_COGNITO_USER_POOL_ID="$COGNITO_USER_POOL_ID"
export VITE_COGNITO_CLIENT_ID="$COGNITO_APP_CLIENT_ID"
export VITE_COGNITO_ENDPOINT="http://localhost:${SBC_PORT_MOTO}"
export VITE_TURNSTILE_SITE_KEY="1x00000000000000000000AA"   # Cloudflare's test site key: the widget always passes

cd "$REPO_ROOT/apps"
[ -d node_modules ] || npm ci --no-audit --no-fund

if [ "${1:-}" = "--dev" ]; then
  # Each dev server proxies /api straight to the API on :8080 (not through the edge); the landing server also
  # proxies /broker/ and /shopper/ to theirs (apps/landing/vite.config.ts). /landing/demo.json isn't served here.
  trap 'kill 0' EXIT INT TERM
  npm run dev:broker & npm run dev:shopper & npm run dev:landing &
  say "dev servers starting: open http://localhost:5173 (Ctrl-C stops them)"
  wait
  exit 0
fi
[ -z "${1:-}" ] || die "usage: web.sh [--dev]"

say "building the frontends with the local settings"
npm run build
password=$(grep '^demo-broker=' "$RUNTIME_DIR/users.env" | cut -d= -f2-)
mkdir -p site/landing
# Like scripts/aws/rotate-demo-password.sh: the landing page reads the demo login from here at runtime.
printf '{"username": "demo-broker", "password": "%s"}\n' "$password" > site/landing/demo.json
say "built: http://localhost:${SBC_PORT_EDGE} (landing), /broker/, /shopper/"
