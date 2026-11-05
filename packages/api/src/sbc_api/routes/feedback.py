"""``POST /api/{bot}/feedback`` (thumbs up or down, reason codes only) and ``GET /api/broker/threads``."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from sbc_api import guards
from sbc_api.auth.principal import Principal, current_principal
from sbc_api.deps import ApiState
from sbc_api.routes.chat import THREAD_ID
from sbc_aws.sessions import FEEDBACK_REASONS
from sbc_core.config import BotName

router = APIRouter()


class FeedbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thread_id: str = Field(pattern=THREAD_ID)
    request_id: str = Field(pattern=r"^[A-Za-z0-9-]{8,64}$")
    rating: Literal["up", "down"]
    reasons: list[str] = Field(default_factory=list, max_length=len(FEEDBACK_REASONS))


@router.post("/api/{bot}/feedback", status_code=204)
def feedback(
    body: FeedbackRequest, request: Request, principal: Annotated[Principal, Depends(current_principal)]
) -> Response:
    api: ApiState = request.app.state.api
    if not api.stores.sessions.owns(principal.subject, body.thread_id):
        raise HTTPException(status_code=404, detail="conversation not found")
    unknown = set(body.reasons) - FEEDBACK_REASONS
    if unknown:
        raise HTTPException(status_code=422, detail=f"unknown reasons: {sorted(unknown)}")
    api.stores.sessions.add_feedback(
        body.thread_id, body.request_id, rating=body.rating, reasons=tuple(body.reasons),
        ttl_seconds=guards.thread_ttl_seconds(api, principal),
    )  # fmt: skip
    return Response(status_code=204)


@router.get("/api/{bot}/threads")
def threads(request: Request, principal: Annotated[Principal, Depends(current_principal)]) -> dict[str, Any]:
    """A broker's own conversations, newest first. The shared demo account lists none here: every interviewer
    shares it, so the app keeps that browser's own thread IDs instead (DEPLOYMENT_ANALYSIS.md 17.5)."""
    if principal.bot is not BotName.BROKER:
        raise HTTPException(status_code=404, detail="not available")
    if principal.is_demo:
        return {"threads": [], "shared_account": True}
    api: ApiState = request.app.state.api
    found = api.stores.sessions.list_threads(principal.subject)
    return {
        "threads": [{"thread_id": t.thread_id, "title": t.title, "updated_at": t.updated_at} for t in found]
    }
