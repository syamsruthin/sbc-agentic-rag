#!/usr/bin/env bash
# Smoke-test a deployed API through CloudFront (run by .github/workflows/deploy-aws.yml after every deploy).
#   scripts/aws/smoke-deployed.sh [<bundle-version>]      # the site URL comes from the SbcApp stack outputs
#   SITE_URL=https://dxxxx.cloudfront.net scripts/aws/smoke-deployed.sh 2027.2
#
# Checks, with no credentials and no model call:
# 1. /api/healthz?deep=1: DynamoDB stores, both bots ready, the bundle has plans and the plan_source table,
#    and (if given) the expected bundle version. The first request may meet a cold start, so it retries.
# 2. A POST through origin access control: /api/public/session with a fake Turnstile token must reach the API
#    and be refused by the bot check (403 with the API's JSON). A missing or wrong x-amz-content-sha256 would
#    be refused by the Function URL itself, with a different body.
# Asking a question needs a broker or public session token, so that check stays with WP13.
source "$(dirname "$0")/_common.sh"
need curl
site="${SITE_URL:-$(out "$APP" SiteUrl)}"
expect="${1:-}"
echo "smoke test: ${site}"

deep=""
for attempt in 1 2 3 4 5; do
  deep=$(curl -sf --max-time 30 "${site}/api/healthz?deep=1") && break
  echo "  healthz attempt ${attempt} failed; retrying" >&2
  sleep 5
done
[ -n "$deep" ] || { echo "FAIL: /api/healthz?deep=1 never answered" >&2; exit 1; }

body='{"turnstile_token":"smoke-test-not-a-real-token","state":"CA"}'
hash=$(printf '%s' "$body" | python3 -c "import hashlib,sys;print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest())")
session=$(curl -s --max-time 30 -o - -w '\n%{http_code}' -X POST "${site}/api/public/session" \
  -H 'content-type: application/json' -H "x-amz-content-sha256: ${hash}" --data "$body")

python3 - "$deep" "$session" "$expect" <<'PY'
import json, sys

deep_raw, session_raw, expect = sys.argv[1:4]
health = json.loads(deep_raw)
deep = health["deep"]
problems = []
if deep.get("stores") != "dynamodb":
    problems.append(f"stores is {deep.get('stores')!r}, not 'dynamodb'")
for bot, state in deep.get("bots", {}).items():
    if state != "ready":
        problems.append(f"bot {bot}: {state}")
if set(deep.get("bots", {})) != {"broker", "public"}:
    problems.append(f"bots: {sorted(deep.get('bots', {}))}")
if not deep.get("plans"):
    problems.append("the bundle reports no plans")
if not deep.get("plan_source"):
    problems.append("the bundle has no plan_source table (the PDF proxy needs it)")
if expect and health.get("bundle_version") != expect:
    problems.append(f"bundle {health.get('bundle_version')!r}, expected {expect!r}")

text, _, code = session_raw.rpartition("\n")
try:
    detail = json.loads(text).get("detail", "")
except ValueError:
    detail = ""
if code != "403" or "bot check" not in str(detail):
    problems.append(f"POST /api/public/session: HTTP {code}, body {text[:200]!r} (expected the API's 403)")

print(f"  bundle {health.get('bundle_version')}, {deep.get('plans')} plans, search {deep.get('search')}, "
      f"init {deep.get('init_ms')} ms")
if problems:
    print("FAIL:\n  " + "\n  ".join(problems), file=sys.stderr)
    sys.exit(1)
print("  deep health ok; a POST reaches the API through origin access control")
print("SMOKE: PASS")
PY
