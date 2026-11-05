#!/usr/bin/env bash
# Start the offline SBC build as a Fargate Spot task in the default VPC's public subnets (no NAT), and
# optionally wait for it. Mode "bundle" rebuilds from the Markdown in S3; "full" refreshes the corpus.
#   scripts/aws/run-build.sh <bundle-version> [bundle|full] [--wait]
source "$(dirname "$0")/_common.sh"
version="${1:?usage: run-build.sh <bundle-version> [bundle|full] [--wait]}"
mode="${2:-bundle}"
cluster=$(out "$APP" BuildCluster)
task_def=$(out "$APP" BuildTaskDefinition)
subnets=$(aws ec2 describe-subnets --filters Name=default-for-az,Values=true \
  --query 'Subnets[].SubnetId' --output text | tr '\t' ',')
[ -n "$subnets" ] || { echo "no default-VPC subnets in ${AWS_REGION}" >&2; exit 1; }
overrides=$(printf '{"containerOverrides":[{"name":"build","command":["%s"],"environment":[{"name":"BUNDLE_VERSION","value":"%s"}]}]}' "$mode" "$version")
arn=$(aws ecs run-task --cluster "$cluster" --task-definition "$task_def" \
  --capacity-provider-strategy capacityProvider=FARGATE_SPOT,weight=1 \
  --network-configuration "awsvpcConfiguration={subnets=[${subnets}],assignPublicIp=ENABLED}" \
  --overrides "$overrides" --query 'tasks[0].taskArn' --output text)
echo "started ${mode} build ${version}: ${arn}"
echo "logs: aws logs tail /ecs/sbc-${SBC_ENV}-build --follow"
if [ "${3:-}" = --wait ]; then
  # A full build takes hours, longer than "aws ecs wait" allows; poll once a minute instead.
  while [ "$(aws ecs describe-tasks --cluster "$cluster" --tasks "$arn" --query 'tasks[0].lastStatus' \
            --output text)" != STOPPED ]; do sleep 60; done
  code=$(aws ecs describe-tasks --cluster "$cluster" --tasks "$arn" \
    --query 'tasks[0].containers[0].exitCode' --output text)
  echo "build finished with exit code ${code}"; [ "$code" = 0 ]
fi
