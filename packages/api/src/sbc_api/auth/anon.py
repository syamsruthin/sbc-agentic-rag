"""Anonymous Plan Shopper sessions: short-lived HS256 tokens the API signs after a Turnstile check.

The token carries a random session ID (the rate-limit and history key) and the visitor's state, which the
public bot treats as authoritative: it never searches across states, whatever a request body says.
"""

from __future__ import annotations

import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import jwt

from sbc_api.auth.cognito import TokenError

ISSUER = "sbc-api"
AUDIENCE = "sbc-public"
SESSION_SECONDS = 24 * 3600
MIN_SECRET_BYTES = 32


@dataclass(frozen=True)
class PublicSession:
    session_id: str
    state: str
    expires_at: int


class SessionTokens:
    def __init__(
        self, secret: str, *, ttl_seconds: int = SESSION_SECONDS, clock: Callable[[], float] = time.time
    ) -> None:
        if len(secret.encode()) < MIN_SECRET_BYTES:
            raise ValueError(f"SESSION_JWT_SECRET must be at least {MIN_SECRET_BYTES} bytes")
        self._secret = secret
        self._ttl = ttl_seconds
        self._clock = clock

    def issue(self, state: str) -> tuple[str, PublicSession]:
        now = int(self._clock())
        session = PublicSession(session_id=secrets.token_urlsafe(18), state=state, expires_at=now + self._ttl)
        claims: dict[str, Any] = {
            "iss": ISSUER, "aud": AUDIENCE, "sub": session.session_id, "st": state, "bot": "public",
            "iat": now, "exp": session.expires_at,
        }  # fmt: skip
        return jwt.encode(claims, self._secret, algorithm="HS256"), session

    def verify(self, token: str) -> PublicSession:
        try:
            claims: dict[str, Any] = jwt.decode(
                token,
                self._secret,
                algorithms=["HS256"],
                issuer=ISSUER,
                audience=AUDIENCE,
                options={"require": ["exp", "iat", "iss", "aud", "sub", "st"]},
            )
        except jwt.PyJWTError as exc:
            raise TokenError("invalid or expired session") from exc
        # The clock is injectable for tests; PyJWT checks exp against the real clock, so check it here too.
        if int(claims["exp"]) <= self._clock() or claims.get("bot") != "public":
            raise TokenError("invalid or expired session")
        return PublicSession(
            session_id=str(claims["sub"]), state=str(claims["st"]), expires_at=int(claims["exp"])
        )
