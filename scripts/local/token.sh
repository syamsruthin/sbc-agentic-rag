#!/usr/bin/env bash
# Print an access token for a local user (admin, broker, demo-broker), for curl and the smoke test:
#
#   curl -H "X-Sbc-Authorization: Bearer $(scripts/local/token.sh broker)" localhost:8088/api/broker/threads
#
# It signs in with ADMIN_USER_PASSWORD_AUTH, which only the local app client allows; the apps use SRP as on AWS.
source "$(dirname "$0")/_common.sh"
load_env
user="${1:?usage: token.sh <admin|broker|demo-broker>}"
cd "$REPO_ROOT"
uv run --quiet python - "$user" <<'PY'
import os, sys
import boto3
from dotenv import dotenv_values

user = sys.argv[1]
password = dotenv_values(".local/users.env").get(user)
if not password:
    sys.exit(f"no local user {user!r} (see .local/users.env)")
idp = boto3.Session(region_name=os.environ["AWS_REGION"]).client("cognito-idp")
result = idp.admin_initiate_auth(
    UserPoolId=os.environ["COGNITO_USER_POOL_ID"], ClientId=os.environ["COGNITO_APP_CLIENT_ID"],
    AuthFlow="ADMIN_USER_PASSWORD_AUTH", AuthParameters={"USERNAME": user, "PASSWORD": password},
)
if "AuthenticationResult" not in result:
    sys.exit(f"{user} needs to finish a sign-in step first ({result.get('ChallengeName')})")
print(result["AuthenticationResult"]["AccessToken"])
PY
