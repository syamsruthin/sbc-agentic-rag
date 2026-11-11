#!/usr/bin/env bash
# Run the API natively against the local stack, with reload, on :8080 (the edge serves it at :8088/api).
# Extra arguments go to uvicorn. Logs go to the console and to .local/logs/api.jsonl.
source "$(dirname "$0")/_common.sh"
load_env
cd "$REPO_ROOT"
[ -f "$FACTS_BUNDLE_PATH" ] || die "$FACTS_BUNDLE_PATH not found (set FACTS_BUNDLE_PATH)"
http_up "http://localhost:${SBC_PORT_DYNAMODB}" || die "the stack isn't up: run scripts/local/up.sh"
mkdir -p "$RUNTIME_DIR/logs"
uv run uvicorn --factory sbc_api.app:create_app --host 127.0.0.1 --port "$SBC_PORT_API" \
  --reload --reload-dir packages --reload-dir config "$@" 2>&1 | tee -a "$RUNTIME_DIR/logs/api.jsonl"
