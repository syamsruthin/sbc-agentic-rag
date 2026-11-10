#!/usr/bin/env bash
# Deploy a stack with the CDK (context comes from infra/cdk/cdk.json, the saved deploy context, and the
# variables below).
#   scripts/aws/deploy.sh foundation
#   scripts/aws/deploy.sh app <api-image-tag> [<build-image-tag>]
# Optional: ALARM_EMAIL, DEMO_USER_IDS (JSON list), WARMER=true|false, GITHUB_OIDC_PROVIDER_ARN, BUILD_IMAGE_TAG
# (or the third argument), CDK_APPROVAL (default "broadening": changes that widen IAM or security groups ask
# first; with no terminal, as in CI, they fail instead).
#
# Each value you set is saved under /sbc-deploy/<env>/ in SSM after a successful deploy, and later deploys,
# by hand or from CI (.github/workflows/deploy-aws.yml), reuse it. Without this, a deploy that left a value out
# would quietly remove it: the alarm email, or the demo account's limits. To clear a saved value, delete its
# parameter: aws ssm delete-parameter --name /sbc-deploy/<env>/<key>.
source "$(dirname "$0")/_common.sh"

# shellcheck disable=SC2034  # read through ${!var} below
[ -n "${3:-}" ] && BUILD_IMAGE_TAG="$3"
# context key : variable that overrides it
KEYS="alarmEmail:ALARM_EMAIL demoUserIds:DEMO_USER_IDS warmer:WARMER githubOidcProviderArn:GITHUB_OIDC_PROVIDER_ARN buildImageTag:BUILD_IMAGE_TAG"

saved=$(aws ssm get-parameters-by-path --path "${CONTEXT_PATH%/}" \
  --query 'Parameters[].[Name,Value]' --output text)
saved_value() { printf '%s\n' "$saved" | awk -F'\t' -v n="${CONTEXT_PATH}$1" '$1 == n { print $2 }'; }

ctx=(-c "envName=${SBC_ENV}")
to_save=()
for pair in $KEYS; do
  key="${pair%%:*}"; var="${pair#*:}"
  value="${!var:-}"
  if [ -n "$value" ]; then
    to_save+=("${key}=${value}")
  else
    value=$(saved_value "$key")
  fi
  [ -n "$value" ] && ctx+=(-c "${key}=${value}")
done
echo "context: ${ctx[*]}"

cd "$REPO_ROOT/infra/cdk"
approval="${CDK_APPROVAL:-broadening}"
case "${1:-}" in
  foundation)
    $CDK deploy "$FOUNDATION" "${ctx[@]}" --require-approval "$approval" ;;
  app)
    tag="${2:?usage: deploy.sh app <api-image-tag> [<build-image-tag>]}"
    # --exclusively: the foundation (data, users, registries) is deployed only on purpose, never as a side effect.
    $CDK deploy "$APP" "${ctx[@]}" -c "apiImageTag=${tag}" --exclusively --require-approval "$approval"
    echo "site: $(out "$APP" SiteUrl)" ;;
  *) echo "usage: deploy.sh foundation | app <api-image-tag> [<build-image-tag>]" >&2; exit 2 ;;
esac

for item in ${to_save[@]+"${to_save[@]}"}; do
  aws ssm put-parameter --name "${CONTEXT_PATH}${item%%=*}" --value "${item#*=}" --type String --overwrite \
    >/dev/null
  echo "saved ${item%%=*} for later deploys"
done
