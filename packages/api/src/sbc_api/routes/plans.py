"""``GET /api/{bot}/plans/search`` and ``GET /api/{bot}/plans/{plan_key}``: the plan picker.

Every result is restricted to the bot's plan scope in code (the public bot: active Individual plans in the
visitor's state). A plan outside the scope is reported as not found, so its existence isn't leaked.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from sbc_api.auth.principal import Principal, current_principal
from sbc_api.deps import ApiState

router = APIRouter()


@router.get("/api/{bot}/plans/search")
def search_plans(
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
    q: Annotated[str | None, Query(max_length=100)] = None,
    state: Annotated[str | None, Query(pattern=r"^[A-Z]{2}$")] = None,
    market: Annotated[str | None, Query(max_length=40)] = None,
    plan_type: Annotated[str | None, Query(max_length=20)] = None,
    metal: Annotated[str | None, Query(max_length=20)] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> dict[str, Any]:
    api: ApiState = request.app.state.api
    config = api.bots[principal.bot].settings.config
    scope = config.bot.plan_scope
    chosen = principal.user_state or state
    states = [chosen] if chosen else []
    if not config.bot.allow_cross_state and not states:
        raise HTTPException(status_code=422, detail="choose a state first")
    if market and scope.markets and market not in scope.markets:
        raise HTTPException(status_code=422, detail="that market isn't available here")
    markets = [market] if market else list(scope.markets)
    plans, total = api.reader.filter_plans(
        name=q or None, states=states, markets=markets, plan_types=[plan_type] if plan_type else [],
        metal_tiers=[metal] if metal else [], exclude_plan_statuses=scope.exclude_plan_statuses,
        exclude_exchanges=scope.exclude_exchange, limit=limit,
    )  # fmt: skip
    return {"plans": [p.model_dump() for p in plans if scope.allows(p)], "total": total}


@router.get("/api/{bot}/plans/{plan_key}")
def get_plan(
    plan_key: str, request: Request, principal: Annotated[Principal, Depends(current_principal)]
) -> dict[str, Any]:
    api: ApiState = request.app.state.api
    scope = api.bots[principal.bot].settings.config.bot.plan_scope
    plan = api.reader.plans([plan_key]).get(plan_key)
    if plan is None or not scope.allows(plan):
        raise HTTPException(status_code=404, detail="plan not found")
    if principal.user_state and plan.state != principal.user_state:
        raise HTTPException(status_code=404, detail="plan not found")
    return plan.model_dump()
