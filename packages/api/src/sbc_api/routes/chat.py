"""``POST /api/{bot}/chat`` and ``POST /api/{bot}/chat/choose``: one turn, streamed as server-sent events.

WP4 scope: the turn itself, with its checkpoint, so a clarifying question resumes on any instance. The guards
(rate limits, the global cap, the answer cache, kill switches, thread ownership, audit) arrive in WP6; until
then a deployed API refuses every caller anyway (``RefuseAll``, until WP5).
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterator
from typing import Annotated, Any, Self

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

from sbc_agent.graph import Agent, Progress, Reply
from sbc_api.auth.principal import Principal, current_principal
from sbc_api.deps import ApiState
from sbc_api.sse import stream_turn

log = logging.getLogger(__name__)
router = APIRouter()

THREAD_ID = r"^[A-Za-z0-9_-]{8,64}$"
MAX_HTTP_MESSAGE = 4000
"""The HTTP layer's cap; the graph's own cap (2,000 characters) answers politely above that."""


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thread_id: str | None = Field(default=None, pattern=THREAD_ID)
    message: str = Field(min_length=1, max_length=MAX_HTTP_MESSAGE)
    ui_selected: list[str] = Field(default_factory=list, max_length=6)
    user_state: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")


class ChooseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thread_id: str = Field(pattern=THREAD_ID)
    plan_key: str | None = Field(default=None, max_length=40)
    text: str | None = Field(default=None, min_length=1, max_length=500)

    @model_validator(mode="after")
    def _one_answer(self) -> Self:
        if (self.plan_key is None) == (self.text is None):
            raise ValueError("send exactly one of plan_key or text")
        return self


def _agent(state: ApiState, principal: Principal) -> Agent:
    runtime = state.bots[principal.bot]
    if runtime.agent is None:
        raise HTTPException(status_code=503, detail=f"the {principal.bot.value} assistant isn't configured")
    return runtime.agent


def _log_turn(principal: Principal, request_id: str, reply: Reply) -> None:
    trace = reply.trace
    usage = list(trace.get("usage", [])) + ([trace["classify_usage"]] if trace.get("classify_usage") else [])
    log.info(
        "turn",
        extra={
            "event": "turn",
            "request_id": request_id,
            "bot": principal.bot.value,
            "kind": reply.kind,
            "path": trace.get("path"),
            "llm_calls": len(usage),
            "cost_usd": round(sum(u.get("cost_usd") or 0 for u in usage), 6),
            "tools": [t.get("name") for t in trace.get("tools", [])],
            "plans": len(reply.plan_keys),
            "fallback": trace.get("fallback_reason"),
            "pii_found": len(trace.get("pii_found", [])),
        },
    )


def _response(request: Request, principal: Principal, thread_id: str, run: Any) -> StreamingResponse:
    request_id: str = request.state.request_id
    events: Iterator[str] = stream_turn(
        run, thread_id=thread_id, request_id=request_id,
        on_reply=lambda reply: _log_turn(principal, request_id, reply),
    )  # fmt: skip
    return StreamingResponse(
        events,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )


@router.post("/api/{bot}/chat")
def chat(
    body: ChatRequest, request: Request, principal: Annotated[Principal, Depends(current_principal)]
) -> StreamingResponse:
    state: ApiState = request.app.state.api
    agent = _agent(state, principal)
    thread_id = body.thread_id or uuid.uuid4().hex
    user_state = principal.user_state or body.user_state

    def run() -> Iterator[Progress | Reply]:
        return agent.stream(
            thread_id, body.message, ui_selected=tuple(body.ui_selected), user_state=user_state
        )

    return _response(request, principal, thread_id, run)


@router.post("/api/{bot}/chat/choose")
def choose(
    body: ChooseRequest, request: Request, principal: Annotated[Principal, Depends(current_principal)]
) -> StreamingResponse:
    state: ApiState = request.app.state.api
    agent = _agent(state, principal)

    def run() -> Iterator[Progress | Reply]:
        return agent.stream(body.thread_id, choice={"plan_key": body.plan_key, "text": body.text})

    return _response(request, principal, body.thread_id, run)
