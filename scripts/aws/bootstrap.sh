#!/usr/bin/env bash
# One-time account checks and CDK bootstrap (docs/aws/RUNBOOK.md section 4).
#   scripts/aws/bootstrap.sh
source "$(dirname "$0")/_common.sh"
account=$(aws sts get-caller-identity --query Account --output text)
echo "account ${account}, region ${AWS_REGION}, env ${SBC_ENV}"

quota=$(aws service-quotas get-service-quota --service-code lambda --quota-code L-B99A9384 \
  --query 'Quota.Value' --output text 2>/dev/null || echo unknown)
echo "Lambda concurrent executions quota: ${quota}"
if [ "$quota" != unknown ] && [ "${quota%.*}" -lt 100 ]; then
  echo "  low: request 1000 in Service Quotas (Lambda > Concurrent executions) before going public" >&2
fi

provider=$(aws iam list-open-id-connect-providers --query \
  "OpenIDConnectProviderList[?contains(Arn, 'token.actions.githubusercontent.com')].Arn" --output text)
if [ -n "$provider" ]; then
  echo "GitHub OIDC provider exists; deploy SbcApp with: -c githubOidcProviderArn=${provider}"
fi

(cd "$REPO_ROOT/infra/cdk" && $CDK bootstrap "aws://${account}/${AWS_REGION}")
echo "bootstrapped. Next: scripts/aws/deploy.sh foundation"
