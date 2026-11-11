#!/usr/bin/env bash
# The generation eval through the local API over HTTP (evals/harness/http_api.py), with a local broker token and
# a Turnstile session from the test secret. Extra arguments go to the harness, e.g. --sample 30 or --id gs-000012.
#
# Costs nothing with the API in replay mode once the items are recorded (LLM_MODE=record for the first run;
# about $0.01-0.04 per item). The harness stops at --max-cost (default $2).
source "$(dirname "$0")/_common.sh"
load_env
cd "$REPO_ROOT"
SBC_EVAL_BROKER_TOKEN=$("$(dirname "$0")/token.sh" broker)
export SBC_EVAL_BROKER_TOKEN
exec uv run --quiet python -m evals.harness.http_api --base-url "http://localhost:${SBC_PORT_EDGE}" \
  --golden evals/golden/0.2.0 --out "$RUNTIME_DIR/evals" "$@"
