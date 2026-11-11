"""``GET /api/healthz``: liveness; with ``?deep=1``, what the process loaded (no model or network calls)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request, Response

from sbc_api.deps import ApiState

router = APIRouter()


@router.get("/api/healthz")
def healthz(request: Request, deep: bool = False) -> dict[str, Any]:
    state: ApiState = request.app.state.api
    out: dict[str, Any] = {"ok": True, "bundle_version": state.reader.bundle_version}
    if deep:
        meta = state.reader.meta
        out["deep"] = {
            "env": state.env.app_env.value,
            "plans": meta.get("plans"),
            "plan_source": meta.get("plan_source"),
            "search": state.search_mode,
            "stores": state.stores.backend,
            "tracing": state.tracer.name,
            "bots": {
                bot.value: ("ready" if rt.agent is not None else f"unavailable: {rt.unavailable}")
                for bot, rt in state.bots.items()
            },
            "init_ms": state.init.get("ms"),
        }
    return out


@router.post("/events", status_code=204, include_in_schema=False)
def warm(request: Request) -> Response:
    """Non-HTTP Lambda invocations (the optional EventBridge warmer, infra/cdk ops.py) arrive here through the
    Lambda Web Adapter's pass-through path; the instance is now warm, and there is nothing else to do."""
    return Response(status_code=204)
