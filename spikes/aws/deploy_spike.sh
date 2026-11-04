#!/usr/bin/env bash
# Spikes 2 and 3 on real AWS: deploy the throwaway stack, measure streaming and cold start, then tear down.
# Needs: AWS credentials for us-east-1 (cdk bootstrap done once), Docker with buildx (arm64), uv, npx.
# Cost: cents (Lambda free tier, CloudFront free tier, ECR ~$0.03). Run "destroy" when finished.
#
#   spikes/aws/deploy_spike.sh up        # build + push the image, deploy, run the checks
#   spikes/aws/deploy_spike.sh check     # re-run the checks (wait 10+ minutes first for a cold start)
#   spikes/aws/deploy_spike.sh destroy   # delete the stack, the repository and its images
set -euo pipefail
cd "$(dirname "$0")/../.."
CDK="npx -y aws-cdk@2.1147.0"
STACK=SbcSseSpike
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

out() { aws cloudformation describe-stacks --region us-east-1 --stack-name "$STACK" \
          --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text; }

check() {
  local cf fn
  cf=$(out CloudFrontUrl); fn=$(out FunctionName)
  echo "== first request (cold if idle for 10+ min): $cf/api/healthz"
  curl -s -o /dev/null -w "healthz %{http_code} in %{time_total}s\n" "$cf/api/healthz"
  curl -s "$cf/api/healthz"; echo
  echo "== streaming through CloudFront -> Function URL (OAC) -> Lambda Web Adapter"
  uv run python -m spikes.aws.sse_client "$cf"
  echo "== Lambda REPORT lines (Init Duration = cold start)"
  sleep 5
  aws logs filter-log-events --region us-east-1 --log-group-name "/aws/lambda/$fn" \
    --filter-pattern REPORT --query 'events[-5:].message' --output text || true
}

case "${1:-up}" in
  up)
    # A real-sized bundle stand-in: the fixture bundle plus 217 MB of incompressible padding.
    uv run python -c "
from pathlib import Path; import shutil
from tests.unit.test_bundle import make_bundle
shutil.copy(make_bundle(Path('$WORK')), '$WORK/facts.sqlite')"
    mkdir -p "$WORK/bundle" && mv "$WORK/facts.sqlite" "$WORK/bundle/"
    head -c 217000000 /dev/urandom > "$WORK/bundle/pad-217mb.bin"
    (cd spikes/aws/cdk_sse && $CDK deploy --require-approval never -c deployFunction=false)
    repo=$(out RepoUri); tag="spike-$(date +%Y%m%d%H%M%S)"
    aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin "${repo%%/*}"
    docker buildx build --platform linux/arm64 --provenance=false --push \
      -f spikes/aws/sse_app/Dockerfile --build-context bundle="$WORK/bundle" -t "$repo:$tag" .
    (cd spikes/aws/cdk_sse && $CDK deploy --require-approval never -c imageTag="$tag")
    check
    ;;
  check) check ;;
  destroy) (cd spikes/aws/cdk_sse && $CDK destroy --force -c imageTag=unused) ;;
  *) echo "usage: $0 up|check|destroy" >&2; exit 2 ;;
esac
