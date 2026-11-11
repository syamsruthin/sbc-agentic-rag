#!/usr/bin/env bash
# Run the API natively against the local stack, with reload, on :8080 (the edge serves it at :8088/api).
# Extra arguments go to uvicorn. The JSON log lines go to the console and to .local/logs/api.jsonl
# (scripts/local/find-request.sh reads them).
source "$(dirname "$0")/_common.sh"
load_env
cd "$REPO_ROOT"
[ -f "$FACTS_BUNDLE_PATH" ] || die "$FACTS_BUNDLE_PATH not found (set FACTS_BUNDLE_PATH)"
http_up "http://localhost:${SBC_PORT_DYNAMODB}" || die "the stack isn't up: run scripts/local/up.sh"
mkdir -p "$RUNTIME_DIR/logs"
export LOG_FILE="$RUNTIME_DIR/logs/api.jsonl"
# --no-access-log as in the API image: the API logs one JSON line per request itself.
exec uv run uvicorn --factory sbc_api.app:create_app --host 127.0.0.1 --port "$SBC_PORT_API" --no-access-log \
  --reload --reload-dir packages --reload-dir config "$@"
