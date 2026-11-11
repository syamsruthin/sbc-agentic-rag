#!/usr/bin/env bash
# Stop the local stack. State in DynamoDB Local and S3Mock is kept; --wipe drops it (and .local/runtime.env).
source "$(dirname "$0")/_common.sh"
case "${1:-}" in
  "") "${COMPOSE[@]}" --profile image down ;;
  --wipe)
    "${COMPOSE[@]}" --profile image down --volumes
    rm -f "$RUNTIME_ENV"
    say "wiped: tables, bucket and generated settings" ;;
  *) die "usage: down.sh [--wipe]" ;;
esac
