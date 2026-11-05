#!/usr/bin/env bash
# Write the API's secrets to SSM Parameter Store as SecureStrings under /sbc/<env>/ (never into git, templates
# or Lambda environment variables). Prompts without echo; an empty answer keeps the current value.
#   scripts/aws/put-secrets.sh            # SESSION_JWT_SECRET is generated once, kept afterwards
#   scripts/aws/put-secrets.sh --rotate-session-secret
source "$(dirname "$0")/_common.sh"
need openssl

put() { aws ssm put-parameter --name "${SSM_PREFIX}$1" --type SecureString --value "$2" --overwrite >/dev/null
        echo "  set ${SSM_PREFIX}$1"; }
exists() { aws ssm get-parameter --name "${SSM_PREFIX}$1" --query Parameter.Name --output text >/dev/null 2>&1; }

for name in OPENROUTER_API_KEY_BROKER OPENROUTER_API_KEY_PUBLIC TURNSTILE_SECRET_KEY \
            LANGFUSE_PUBLIC_KEY LANGFUSE_SECRET_KEY; do
  read -r -s -p "${name} (empty = keep/skip): " value; echo
  [ -n "$value" ] && put "$name" "$value"
done
if [ "${1:-}" = --rotate-session-secret ] || ! exists SESSION_JWT_SECRET; then
  put SESSION_JWT_SECRET "$(openssl rand -base64 48 | tr -d '\n')"   # signs public sessions; >= 32 bytes
fi
echo "Required for dev: OPENROUTER_API_KEY_BROKER, OPENROUTER_API_KEY_PUBLIC, TURNSTILE_SECRET_KEY,"
echo "SESSION_JWT_SECRET. Set each OpenRouter key's credit limit at openrouter.ai first (\$10 public, \$15 broker)."
