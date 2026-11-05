"""``POST /api/{bot}/chat`` and ``POST /api/{bot}/chat/choose``: one turn, streamed as server-sent events,
behind the guards in :mod:`sbc_api.guards`."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable, Iterator
from typing import Annotated, Self

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

from sbc_agent.graph import Agent, Progress, Reply
from sbc_agent.redact import redact
from sbc_api import guards
from sbc_api.auth.principal import Principal, current_principal
from sbc_api.deps import ApiState
from sbc_api.routes.session import client_ip
from sbc_api.sse import reply_payload, single, stream_turn

log = logging.getLogger(__name__)
router = APIRouter()

THREAD_ID = r"^[A-Za-z0-9_-]{8,64}$"
MAX_HTTP_MESSAGE = 4000
"""The HTTP layer's cap; the graph's own cap (2,000 characters) answers politely above that."""
SSE_HEADERS = {"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"}


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thread_id: str | None = Field(default=None, pattern=THREAD_ID)
    """An existing thread of this caller; omit it to start a new one (the server picks the ID)."""
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


def _sse(events: Iterator[str]) -> StreamingResponse:
    return StreamingResponse(events, media_type="text/event-stream", headers=SSE_HEADERS)


def _agent(api: ApiState, principal: Principal) -> Agent:
    runtime = api.bots[principal.bot]
    agent = runtime.agent_for(principal.is_demo)
    if agent is None:
        raise HTTPException(status_code=503, detail=f"the {principal.bot.value} assistant isn't configured")
    return agent


def _log_turn(principal: Principal, request_id: str, reply: Reply) -> None:
    trace = reply.trace
    usage = list(trace.get("usage", [])) + ([trace["classify_usage"]] if trace.get("classify_usage") else [])
    log.info(
        "turn",
        extra={
            "event": "turn", "request_id": request_id, "bot": principal.bot.value, "kind": reply.kind,
            "demo": principal.is_demo, "path": trace.get("path"), "llm_calls": len(usage),
            "cost_usd": round(sum(u.get("cost_usd") or 0 for u in usage), 6),
            "tools": [t.get("name") for t in trace.get("tools", [])], "plans": len(reply.plan_keys),
            "fallback": trace.get("fallback_reason"), "pii_found": len(trace.get("pii_found", [])),
        },
    )  # fmt: skip


def _precheck(
    api: ApiState, principal: Principal, request: Request, thread_id: str | None
) -> Iterator[str] | None:
    """Rate limits, thread ownership and the kill switch; a stream to send instead of a turn, or None."""
    limited = guards.rate_limited(api, principal, client_ip(request))
    if limited is not None:
        return single("limit", limited)
    if thread_id is not None and not api.stores.sessions.owns(principal.subject, thread_id):
        raise HTTPException(status_code=404, detail="conversation not found")
    if not api.stores.switches.llm_enabled(principal.bot.value):
        return single(
            "answer", {"kind": "message", "markdown": guards.LLM_OFF, "plan_keys": [], "citations": []}
        )
    return None


def _run(
    api: ApiState,
    principal: Principal,
    request: Request,
    thread_id: str,
    run: Callable[[], Iterator[Progress | Reply]],
    cache_key: str | None = None,
) -> StreamingResponse:
    request_id: str = request.state.request_id

    def finish(reply: Reply) -> Reply:
        final = guards.apply_quarantine(api, reply)
        _log_turn(principal, request_id, final)
        guards.audit_turn(api, principal, request_id, final)
        if cache_key is not None and final.kind == "answer":
            api.stores.answers.put(cache_key, reply_payload(final)[1])
        return final

    return _sse(stream_turn(run, thread_id=thread_id, request_id=request_id, on_reply=finish))


@router.post("/api/{bot}/chat")
def chat(
    body: ChatRequest, request: Request, principal: Annotated[Principal, Depends(current_principal)]
) -> StreamingResponse:
    api: ApiState = request.app.state.api
    agent = _agent(api, principal)
    refused = _precheck(api, principal, request, body.thread_id)
    if refused is not None:
        return _sse(refused)
    user_state = principal.user_state or body.user_state
    request_id: str = request.state.request_id
    ttl = guards.thread_ttl_seconds(api, principal)
    title = redact(body.message).text

    cache_key = None
    if body.thread_id is None:
        cache_key = guards.first_turn_cache_key(api, principal, body.message, body.ui_selected, user_state)
        cached = api.stores.answers.get(cache_key)
        if cached is not None:
            thread_id = uuid.uuid4().hex
            api.stores.sessions.touch_thread(
                principal.subject, thread_id, bot=principal.bot.value, title=title, ttl_seconds=ttl
            )
            reply = Reply("answer", cached["markdown"], tuple(cached.get("citations", [])),
                          tuple(cached.get("plan_keys", [])))  # fmt: skip
            agent.remember(thread_id, message=body.message, reply=reply)
            guards.audit_turn(api, principal, request_id, reply, cached=True)
            log.info(
                "cache hit",
                extra={"event": "cache_hit", "request_id": request_id, "bot": principal.bot.value},
            )
            return _sse(
                single("answer", {**cached, "cached": True}, thread_id=thread_id, request_id=request_id)
            )

    capped = guards.global_cap_reached(api)
    if capped is not None:
        return _sse(single("limit", capped))
    thread_id = body.thread_id or uuid.uuid4().hex
    api.stores.sessions.touch_thread(
        principal.subject, thread_id, bot=principal.bot.value, title=title, ttl_seconds=ttl
    )

    def run() -> Iterator[Progress | Reply]:
        return agent.stream(
            thread_id, body.message, ui_selected=tuple(body.ui_selected), user_state=user_state
        )

    return _run(api, principal, request, thread_id, run, cache_key)


@router.post("/api/{bot}/chat/choose")
def choose(
    body: ChooseRequest, request: Request, principal: Annotated[Principal, Depends(current_principal)]
) -> StreamingResponse:
    api: ApiState = request.app.state.api
    agent = _agent(api, principal)
    refused = _precheck(api, principal, request, body.thread_id)
    if refused is not None:
        return _sse(refused)
    capped = guards.global_cap_reached(api)
    if capped is not None:
        return _sse(single("limit", capped))

    def run() -> Iterator[Progress | Reply]:
        return agent.stream(body.thread_id, choice={"plan_key": body.plan_key, "text": body.text})

    return _run(api, principal, request, body.thread_id, run)
