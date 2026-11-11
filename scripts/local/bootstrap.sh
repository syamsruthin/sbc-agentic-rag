#!/usr/bin/env bash
# Create the local resources the API needs (idempotent; up.sh runs it on every start): tables, bucket and the
# secrets in the local Parameter Store (scripts/local/bootstrap.py).
source "$(dirname "$0")/_common.sh"
load_env
cd "$REPO_ROOT"
uv run --quiet python scripts/local/bootstrap.py "$@"
