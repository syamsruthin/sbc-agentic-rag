#!/usr/bin/env bash
# Build the three frontends (apps/) and publish them to the web bucket behind CloudFront (WP10).
#   scripts/aws/deploy-web.sh                    # build with the stack outputs, sync, invalidate
#   SKIP_BUILD=1 scripts/aws/deploy-web.sh       # publish an existing apps/site (CI builds it in its own step)
#   TURNSTILE_SITE_KEY=0x… scripts/aws/deploy-web.sh   # first time: also saves the site key for CI
#
# Build-time settings (all public by design) come from the stacks: the Cognito pool and app client from
# SbcFoundation, the bucket and distribution from SbcApp. The Turnstile site key isn't a stack output: it's
# saved in SSM under /sbc-deploy/<env>/turnstileSiteKey the first time it is given, and read from there after.
#
# The sync never touches landing/ (rotate-demo-password.sh writes landing/demo.json there). Hashed assets get
# a one-year immutable cache; index.html files are revalidated, so a deploy shows up at once.
source "$(dirname "$0")/_common.sh"
need npm
apps="${REPO_ROOT}/apps"
param="${CONTEXT_PATH}turnstileSiteKey"

# By path: the CI deploy role may read /sbc-deploy/<env> only that way (infra/cdk/sbc_infra/constructs/cicd.py).
saved=$(aws ssm get-parameters-by-path --path "${CONTEXT_PATH%/}" \
  --query "Parameters[?Name=='${param}'].Value | [0]" --output text 2>/dev/null || true)
[ "$saved" = "None" ] && saved=""
site_key="${TURNSTILE_SITE_KEY:-$saved}"
[ -n "$site_key" ] || { echo "no Turnstile site key: set TURNSTILE_SITE_KEY once (docs/aws/RUNBOOK.md section 4)" >&2; exit 1; }

if [ "${SKIP_BUILD:-}" != "1" ]; then
  export VITE_API_BASE_URL=/api
  VITE_COGNITO_USER_POOL_ID=$(out "$FOUNDATION" UserPoolId)
  VITE_COGNITO_CLIENT_ID=$(out "$FOUNDATION" UserPoolClientId)
  export VITE_COGNITO_USER_POOL_ID VITE_COGNITO_CLIENT_ID
  export VITE_TURNSTILE_SITE_KEY="$site_key"
  (cd "$apps" && npm ci --no-audit --no-fund && npm run build)
fi
[ -f "${apps}/site/index.html" ] || { echo "no apps/site build to publish" >&2; exit 1; }

bucket=$(out "$APP" WebBucket)
dist=$(out "$APP" DistributionId)
echo "publishing apps/site to s3://${bucket}"
aws s3 sync "${apps}/site/" "s3://${bucket}/" --delete --exclude 'landing/*' --exclude '*index.html' \
  --cache-control 'public, max-age=31536000, immutable' --sse AES256 --only-show-errors
for page in index.html broker/index.html shopper/index.html; do
  aws s3 cp "${apps}/site/${page}" "s3://${bucket}/${page}" --content-type 'text/html; charset=utf-8' \
    --cache-control 'no-cache' --sse AES256 --only-show-errors
done
aws cloudfront create-invalidation --distribution-id "$dist" --paths '/*' --query Invalidation.Id --output text

if [ -n "${TURNSTILE_SITE_KEY:-}" ] && [ "$TURNSTILE_SITE_KEY" != "$saved" ]; then
  aws ssm put-parameter --name "$param" --type String --overwrite --value "$TURNSTILE_SITE_KEY" >/dev/null
  echo "saved the Turnstile site key in ${param}"
fi
echo "published: $(out "$APP" SiteUrl)"
