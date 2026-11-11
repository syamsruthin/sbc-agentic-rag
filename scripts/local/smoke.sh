#!/usr/bin/env bash
# The local stack's end-to-end smoke test (scripts/local/smoke.py): every route and guard, through the edge.
#
#   LLM_MODE=record scripts/local/api.sh  &&  scripts/local/smoke.sh --record    # once (about $0.06)
#   LLM_MODE=replay scripts/local/api.sh  &&  scripts/local/smoke.sh             # every run after ($0)
#
# Works the same against the API image: LLM_MODE=replay scripts/local/up.sh --image.
source "$(dirname "$0")/_common.sh"
load_env
cd "$REPO_ROOT"
exec uv run --quiet python scripts/local/smoke.py "$@"
