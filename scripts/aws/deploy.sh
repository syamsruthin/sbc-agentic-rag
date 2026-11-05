#!/usr/bin/env bash
# Deploy a stack with the CDK (context comes from infra/cdk/cdk.json plus the variables below).
#   scripts/aws/deploy.sh foundation
#   scripts/aws/deploy.sh app <api-image-tag> [<build-image-tag>]
# Optional: ALARM_EMAIL, DEMO_USER_IDS (JSON list), WARMER=true, GITHUB_OIDC_PROVIDER_ARN.
source "$(dirname "$0")/_common.sh"
ctx=(-c "envName=${SBC_ENV}")
[ -n "${ALARM_EMAIL:-}" ] && ctx+=(-c "alarmEmail=${ALARM_EMAIL}")
[ -n "${DEMO_USER_IDS:-}" ] && ctx+=(-c "demoUserIds=${DEMO_USER_IDS}")
[ -n "${WARMER:-}" ] && ctx+=(-c "warmer=${WARMER}")
[ -n "${GITHUB_OIDC_PROVIDER_ARN:-}" ] && ctx+=(-c "githubOidcProviderArn=${GITHUB_OIDC_PROVIDER_ARN}")
cd "$REPO_ROOT/infra/cdk"
case "${1:-}" in
  foundation)
    $CDK deploy "$FOUNDATION" "${ctx[@]}" --require-approval broadening ;;
  app)
    tag="${2:?usage: deploy.sh app <api-image-tag> [<build-image-tag>]}"
    ctx+=(-c "apiImageTag=${tag}")
    [ -n "${3:-}" ] && ctx+=(-c "buildImageTag=$3")
    $CDK deploy "$APP" "${ctx[@]}" --require-approval broadening
    echo "site: $(out "$APP" SiteUrl)" ;;
  *) echo "usage: deploy.sh foundation | app <api-image-tag> [<build-image-tag>]" >&2; exit 2 ;;
esac
