#!/usr/bin/env bash
# Create the admin (TOTP enrolled at first sign-in) and the shared demo-broker account (no MFA, broker group,
# password shown on the landing page). Prints the demo account's sub for -c demoUserIds.
#   scripts/aws/seed-users.sh <admin-username> <admin-email>
source "$(dirname "$0")/_common.sh"
admin="${1:?usage: seed-users.sh <admin-username> <admin-email>}"
email="${2:?usage: seed-users.sh <admin-username> <admin-email>}"
pool=$(out "$FOUNDATION" UserPoolId)

aws cognito-idp admin-create-user --user-pool-id "$pool" --username "$admin" \
  --user-attributes "Name=email,Value=${email}" "Name=email_verified,Value=true" \
  --desired-delivery-mediums EMAIL >/dev/null 2>&1 || echo "  ${admin} exists"
for group in broker admin; do
  aws cognito-idp admin-add-user-to-group --user-pool-id "$pool" --username "$admin" --group-name "$group"
done
echo "admin ${admin}: temporary password emailed; set a new one and enroll TOTP at first sign-in"

aws cognito-idp admin-create-user --user-pool-id "$pool" --username demo-broker \
  --message-action SUPPRESS >/dev/null 2>&1 || echo "  demo-broker exists"
aws cognito-idp admin-add-user-to-group --user-pool-id "$pool" --username demo-broker --group-name broker
sub=$(aws cognito-idp admin-get-user --user-pool-id "$pool" --username demo-broker \
  --query "UserAttributes[?Name=='sub'].Value" --output text)
echo "demo-broker sub: ${sub}"
echo "Next: DEMO_USER_IDS='[\"${sub}\"]' scripts/aws/deploy.sh app <api-image-tag>"
echo "Then: scripts/aws/rotate-demo-password.sh   (sets its password and publishes it to the landing page)"
