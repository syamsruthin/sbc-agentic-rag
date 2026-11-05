#!/usr/bin/env bash
# Smoke-test the API image the way Lambda runs it (read-only root, writable /tmp only) and print measurements.
#
#   infra/docker/smoke.sh sbc-api:local
#
# Checks: start-up to a 200 from /api/healthz, the deep health report, a plan search, the prompts and taxonomy
# inside the installed packages, and a non-root user. Prints the image size, start-up time and memory.
set -euo pipefail
image="${1:-sbc-api:local}"
port="${SMOKE_PORT:-18080}"
name="sbc-smoke-$$"
cleanup() { docker rm -f "$name" >/dev/null 2>&1 || true; }
trap cleanup EXIT

started=$(date +%s%N)
docker run -d --name "$name" --read-only --tmpfs /tmp -p "127.0.0.1:${port}:8080" \
  -e APP_ENV=local "$image" >/dev/null
for _ in $(seq 1 600); do
  curl -sf "http://127.0.0.1:${port}/api/healthz" >/dev/null && break
  if ! docker ps -q --filter "name=$name" | grep -q .; then docker logs "$name"; exit 1; fi
  sleep 0.05
done
ready_ms=$(( ($(date +%s%N) - started) / 1000000 ))
deep=$(curl -sf "http://127.0.0.1:${port}/api/healthz?deep=1")
plans=$(curl -sf "http://127.0.0.1:${port}/api/broker/plans/search?limit=1")
docker exec "$name" python -c "
from sbc_agent.prompts import load
from sbc_core.taxonomy import load_taxonomy
assert all(load(n)[0] for n in ('classify', 'agent', 'compose'))
assert load_taxonomy().version
import os; assert os.getuid() != 0, 'runs as root'
print('prompts, taxonomy and user ok')"
mem=$(docker stats --no-stream --format '{{.MemUsage}}' "$name")
size=$(docker image inspect "$image" --format '{{.Size}}')
python3 - "$deep" "$plans" <<'PY'
import json, sys
deep = json.loads(sys.argv[1])["deep"]
plans = json.loads(sys.argv[2])
assert deep["plans"] and deep["plans"] > 0, deep
assert "total" in plans and plans["total"] > 0, plans
print("deep health:", json.dumps(deep))
PY
echo "image $image: $((size / 1000000)) MB (docker image inspect), ready in ${ready_ms} ms, memory ${mem}"
echo "SMOKE: PASS"
