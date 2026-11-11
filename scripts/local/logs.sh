#!/usr/bin/env bash
# Follow the stack's container logs: scripts/local/logs.sh [service...] (dynamodb, aws, s3, edge, api).
source "$(dirname "$0")/_common.sh"
exec "${COMPOSE[@]}" --profile image logs -f --tail 100 "$@"
