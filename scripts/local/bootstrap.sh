#!/usr/bin/env bash
# Create the local resources the API needs (idempotent; up.sh runs it on every start): the DynamoDB tables.
source "$(dirname "$0")/_common.sh"
load_env
cd "$REPO_ROOT"
uv run --quiet sbc-create-tables --wait-seconds 60
