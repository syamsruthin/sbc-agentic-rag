# Shared settings for the scripts in scripts/aws (sourced, not run). Needs the AWS CLI v2 and credentials for
# shellcheck disable=SC2034  # variables used by the scripts that source this file
# the target account in your shell (e.g. an IAM Identity Center profile: export AWS_PROFILE=...).
set -euo pipefail
SBC_ENV="${SBC_ENV:-dev}"
export AWS_REGION="${AWS_REGION:-us-east-1}"
FOUNDATION="SbcFoundation-${SBC_ENV}"
APP="SbcApp-${SBC_ENV}"
SSM_PREFIX="/sbc/${SBC_ENV}/"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CDK="npx -y aws-cdk@2.1147.0"

# out <stack> <OutputKey>: one CloudFormation output value.
out() {
  aws cloudformation describe-stacks --stack-name "$1" \
    --query "Stacks[0].Outputs[?OutputKey=='$2'].OutputValue" --output text
}

need() { command -v "$1" >/dev/null || { echo "missing: $1" >&2; exit 1; }; }
need aws
