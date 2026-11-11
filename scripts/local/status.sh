#!/usr/bin/env bash
# One line per local service: up or down, and where.
source "$(dirname "$0")/_common.sh"
row() {
  if http_up "$2"; then printf '  %-10s up    %s\n' "$1" "$3"; else printf '  %-10s DOWN  %s\n' "$1" "$3"; fi
}
say "local stack"
row dynamodb "http://localhost:${SBC_PORT_DYNAMODB}" "http://localhost:${SBC_PORT_DYNAMODB}"
row moto "http://localhost:${SBC_PORT_MOTO}/moto-api/" "http://localhost:${SBC_PORT_MOTO}/moto-api/ (Cognito, SSM)"
row s3 "http://localhost:${SBC_PORT_S3}" "http://localhost:${SBC_PORT_S3} (bucket sbc-local-data)"
row edge "http://localhost:${SBC_PORT_EDGE}" "http://localhost:${SBC_PORT_EDGE} (open this in a browser)"
row api "http://localhost:${SBC_PORT_API}/api/healthz" "http://localhost:${SBC_PORT_API}/api/healthz?deep=1"
