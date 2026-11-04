"""The caller of a bot route, and the FastAPI dependency that resolves it.

WP4 ships only the local authenticator (``APP_ENV=local``: a fixed developer principal per bot) and one that
refuses everything, which every other environment uses until WP5 adds Cognito JWT verification for the
broker and anonymous session tokens for the public bot. A deployed API therefore can't serve a chat turn
before WP5, by construction.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from fastapi import HTTPException, Request

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
    """Until real authentication is configured (WP5), nothing is authorized outside local development."""

    def authenticate(self, request: Request, bot: BotName) -> Principal:
        raise HTTPException(status_code=401, detail="authentication is not configured")


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
