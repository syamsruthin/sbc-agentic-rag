"""The caller of a bot route, and the FastAPI dependencies that resolve it.

- ``BearerAuthenticator`` (dev and prd): the broker sends a Cognito access token, the public bot a session
  token from ``POST /api/public/session``; each is accepted only on its own bot's routes.
- ``LocalAuthenticator`` (``APP_ENV=local``): a fixed developer principal per bot.
- ``RefuseAll``: refuses everything (used when nothing else is configured).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from fastapi import HTTPException, Request

from sbc_api.auth.anon import SessionTokens
from sbc_api.auth.cognito import BROKER_GROUPS, CognitoVerifier, TokenError
from sbc_core.config import BotName


@dataclass(frozen=True)
class Principal:
    bot: BotName
    subject: str
    """Cognito ``sub`` (broker), the anonymous session ID (public) or ``local-<bot>``."""
    groups: frozenset[str] = field(default_factory=frozenset)
    is_demo: bool = False
    user_state: str | None = None
    """The public visitor's state, from their session (the public bot doesn't search across states)."""

    @property
    def is_admin(self) -> bool:
        return "admin" in self.groups


class Authenticator(Protocol):
    def authenticate(self, request: Request, bot: BotName) -> Principal: ...


class LocalAuthenticator:
    """Development only: every request is the local developer (an admin), for whichever bot it calls."""

    def authenticate(self, request: Request, bot: BotName) -> Principal:
        return Principal(bot=bot, subject=f"local-{bot.value}", groups=frozenset({"broker", "admin"}))


class RefuseAll:
    """Nothing is authorized (an environment with no authentication configured)."""

    def authenticate(self, request: Request, bot: BotName) -> Principal:
        raise HTTPException(status_code=401, detail="authentication is not configured")


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(status_code=401, detail=detail, headers={"WWW-Authenticate": "Bearer"})


def bearer_token(request: Request) -> str:
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise _unauthorized("sign in first")
    return token.strip()


class BearerAuthenticator:
    """Cognito access tokens for the broker bot, API-signed session tokens for the public bot.

    A token is only ever checked by its own bot's verifier, so a public session can't reach broker routes and
    a broker token can't open a public session. The shared demo account (``demo_user_ids``) is a broker
    that can never be an admin, whatever groups its token claims."""

    def __init__(
        self, *, cognito: CognitoVerifier | None, sessions: SessionTokens, demo_user_ids: frozenset[str]
    ) -> None:
        self._cognito = cognito
        self._sessions = sessions
        self._demo = demo_user_ids

    def authenticate(self, request: Request, bot: BotName) -> Principal:
        token = bearer_token(request)
        try:
            if bot is BotName.PUBLIC:
                session = self._sessions.verify(token)
                return Principal(bot=bot, subject=session.session_id, user_state=session.state)
            if self._cognito is None:
                raise _unauthorized("broker sign-in is not configured")
            claims = self._cognito.verify(token)
        except TokenError as exc:
            raise _unauthorized(str(exc)) from None
        if not claims.groups & BROKER_GROUPS:
            raise HTTPException(status_code=403, detail="this account can't use the Broker Assistant")
        demo = claims.sub in self._demo
        groups = claims.groups - {"admin"} if demo else claims.groups
        return Principal(bot=bot, subject=claims.sub, groups=groups, is_demo=demo)


def bot_from_path(bot: str) -> BotName:
    try:
        return BotName(bot)
    except ValueError:
        raise HTTPException(status_code=404, detail="no such bot") from None


def current_principal(request: Request, bot: str) -> Principal:
    """FastAPI dependency for ``/api/{bot}/...`` routes."""
    name = bot_from_path(bot)
    authenticator: Authenticator = request.app.state.api.authenticator
    principal = authenticator.authenticate(request, name)
    if principal.bot is not name:
        raise HTTPException(status_code=403, detail="this session belongs to another assistant")
    return principal


def admin_principal(request: Request) -> Principal:
    """FastAPI dependency for ``/api/admin/...``: an ``admin`` broker token, never the demo account."""
    principal = request.app.state.api.authenticator.authenticate(request, BotName.BROKER)
    if not principal.is_admin or principal.is_demo:
        raise HTTPException(status_code=403, detail="admins only")
    return principal
