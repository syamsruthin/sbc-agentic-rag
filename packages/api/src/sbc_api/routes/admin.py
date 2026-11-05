"""Admin endpoints (``admin`` group only, never the demo account): kill switches and plan quarantine.

Both take effect on this instance at once and on every other instance within the switches' 60 s cache.
Every change is audited.
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from sbc_api.auth.principal import Principal, admin_principal
from sbc_api.deps import ApiState
from sbc_aws.audit import AuditRecord, hash_actor
from sbc_core.config import BotName

router = APIRouter()
Admin = Annotated[Principal, Depends(admin_principal)]


class KillSwitchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bot: BotName
    llm_enabled: bool


class QuarantineRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_keys: list[str] = Field(min_length=1, max_length=50)
    action: Literal["add", "remove"]
    reason: str = Field(default="", max_length=200)


def _audit(api: ApiState, request: Request, admin: Principal, action: str, detail: dict[str, str]) -> None:
    record = AuditRecord(
        request_id=request.state.request_id, actor_hash=hash_actor(admin.subject), bot="admin",
        action=action, detail=detail,
    )  # fmt: skip
    api.stores.audit.write(record)


@router.post("/api/admin/kill-switch", status_code=204)
def kill_switch(body: KillSwitchRequest, request: Request, admin: Admin) -> Response:
    api: ApiState = request.app.state.api
    api.stores.switches.set_llm_enabled(
        body.bot.value, body.llm_enabled, actor_hash=hash_actor(admin.subject)
    )
    _audit(api, request, admin, "kill_switch", {"bot": body.bot.value, "llm_enabled": str(body.llm_enabled)})
    return Response(status_code=204)


@router.post("/api/admin/quarantine", status_code=204)
def quarantine(body: QuarantineRequest, request: Request, admin: Admin) -> Response:
    api: ApiState = request.app.state.api
    if body.action == "add":
        api.stores.switches.quarantine(
            body.plan_keys, actor_hash=hash_actor(admin.subject), reason=body.reason
        )
    else:
        api.stores.switches.release(body.plan_keys)
    _audit(api, request, admin, "quarantine", {"action": body.action, "plans": ",".join(body.plan_keys)})
    return Response(status_code=204)
