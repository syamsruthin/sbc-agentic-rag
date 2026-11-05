"""``POST /api/public/session``: a Turnstile check, then an anonymous session token for the Plan Shopper.

The token (24 h) carries a random session ID and the visitor's state; the public bot's routes accept nothing
else. Locally, without ``TURNSTILE_SECRET_KEY``, the check is skipped.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from sbc_api.auth.turnstile import TurnstileUnavailableError
from sbc_api.deps import ApiState
from sbc_core.config import Env

router = APIRouter()


class SessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    turnstile_token: str = Field(min_length=1, max_length=2048)
    state: str = Field(pattern=r"^[A-Z]{2}$")


def client_ip(request: Request) -> str | None:
    """The viewer's IP: CloudFront's ``CloudFront-Viewer-Address`` (``ip:port``), else the socket peer."""
    viewer = request.headers.get("cloudfront-viewer-address")
    if viewer:
        return viewer.rsplit(":", 1)[0].strip("[]")
    return request.client.host if request.client else None


@router.post("/api/public/session")
def start_session(body: SessionRequest, request: Request) -> dict[str, Any]:
    api: ApiState = request.app.state.api
    if api.turnstile is not None:
        try:
            passed = api.turnstile.verify(body.turnstile_token, client_ip(request))
        except TurnstileUnavailableError:
            raise HTTPException(
                status_code=503, detail="the bot check is unavailable; please try again"
            ) from None
        if not passed:
            raise HTTPException(status_code=403, detail="the bot check failed; please reload and try again")
    elif api.env.app_env is not Env.LOCAL:
        raise HTTPException(status_code=503, detail="the bot check is not configured")
    token, session = api.session_tokens.issue(body.state)
    return {"token": token, "expires_at": session.expires_at, "state": session.state}
