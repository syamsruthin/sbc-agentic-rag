#!/usr/bin/env bash
# Print every API log line of one request, by its request ID: the ID that the apps show under "Report a problem",
# that every API response carries in x-request-id, and that the SSE `thread` event sends. The local equivalent of
# a CloudWatch Logs Insights query on AWS (docs/aws/RUNBOOK.md):
#
#   scripts/local/find-request.sh 5d7d0faa55db483288aa39d3d7f0ab99
#
# It reads .local/logs/api.jsonl (the native API) and the api container's output (up.sh --image).
source "$(dirname "$0")/_common.sh"
rid="${1:?usage: find-request.sh <request-id>}"
[[ "$rid" =~ ^[A-Za-z0-9-]+$ ]] || die "not a request ID: $rid"
{
  [ -f "$RUNTIME_DIR/logs/api.jsonl" ] && cat "$RUNTIME_DIR/logs/api.jsonl"
  docker ps -q --filter name=sbc-local-api-1 | grep -q . && docker logs sbc-local-api-1 2>&1
} | grep -F "\"$rid\"" | { if command -v jq >/dev/null; then jq -C . 2>/dev/null || cat; else cat; fi; } \
  | { out=$(cat); [ -n "$out" ] || die "no log lines for $rid"; printf '%s\n' "$out"; }
