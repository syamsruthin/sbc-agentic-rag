#!/usr/bin/env bash
# Set a new password for the shared demo-broker account and publish it to /landing/demo.json, which the
# landing page reads at runtime (decision D2: the credentials are public; no rebuild is needed to rotate).
#   scripts/aws/rotate-demo-password.sh
source "$(dirname "$0")/_common.sh"
pool=$(out "$FOUNDATION" UserPoolId)
web=$(out "$APP" WebBucket)
dist=$(out "$APP" DistributionId)
password=$(python3 -c "import secrets,string;a=string.ascii_letters+string.digits
while True:
    p=''.join(secrets.choice(a) for _ in range(16))
    if any(c.islower() for c in p) and any(c.isupper() for c in p) and any(c.isdigit() for c in p): print(p); break")
aws cognito-idp admin-set-user-password --user-pool-id "$pool" --username demo-broker \
  --password "$password" --permanent
printf '{"username": "demo-broker", "password": "%s"}\n' "$password" | aws s3 cp - "s3://${web}/landing/demo.json" \
  --content-type application/json --cache-control "no-store" --sse AES256
aws cloudfront create-invalidation --distribution-id "$dist" --paths /landing/demo.json >/dev/null
echo "demo-broker password rotated and published to /landing/demo.json"
