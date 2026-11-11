"""Broker sign-in: verify Amazon Cognito access tokens against the user pool's public keys (JWKS).

Checked on every request: RS256 signature by a key in the pool's JWKS, issuer, ``token_use == "access"``,
``client_id`` (access tokens carry no ``aud``), expiry, and a ``broker`` or ``admin`` group. No shared
secret is involved, which replaces the Supabase JWT secret (TODO.md: "JWT verification through the
project's JWKS URL").
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import jwt

ALGORITHMS = ["RS256"]
BROKER_GROUPS = frozenset({"broker", "admin"})
JWKS_TIMEOUT_SECONDS = 5
JWKS_CACHE_SECONDS = 3600


class TokenError(Exception):
    """The token is missing, malformed, expired or not for this app. The message is safe to show."""


class SigningKeys(Protocol):
    """The part of ``jwt.PyJWKClient`` used here; tests pass a fake that serves a generated key."""

    def get_signing_key_from_jwt(self, token: str) -> Any: ...


@dataclass(frozen=True)
class CognitoClaims:
    sub: str
    username: str
    groups: frozenset[str]


def issuer(region: str, user_pool_id: str) -> str:
    return f"https://cognito-idp.{region}.amazonaws.com/{user_pool_id}"


def jwks_client(region: str, user_pool_id: str, url: str | None = None) -> jwt.PyJWKClient:
    """Fetches the pool's keys once and caches them (a new key ID triggers one refetch). ``url`` replaces the
    pool's own JWKS URL, for a local Cognito stand-in only."""
    return jwt.PyJWKClient(
        url or f"{issuer(region, user_pool_id)}/.well-known/jwks.json",
        cache_keys=True,
        lifespan=JWKS_CACHE_SECONDS,
        timeout=JWKS_TIMEOUT_SECONDS,
    )


class CognitoVerifier:
    def __init__(
        self,
        *,
        region: str,
        user_pool_id: str,
        app_client_id: str,
        keys: SigningKeys | None = None,
        jwks_url: str | None = None,
        leeway_seconds: int = 30,
    ) -> None:
        self._issuer = issuer(region, user_pool_id)
        self._client_id = app_client_id
        self._keys = keys if keys is not None else jwks_client(region, user_pool_id, jwks_url)
        self._leeway = leeway_seconds

    def verify(self, token: str) -> CognitoClaims:
        try:
            key = self._keys.get_signing_key_from_jwt(token).key
            claims: dict[str, Any] = jwt.decode(
                token,
                key,
                algorithms=ALGORITHMS,
                issuer=self._issuer,
                leeway=self._leeway,
                options={
                    "require": ["exp", "iat", "iss", "sub", "token_use", "client_id"],
                    "verify_aud": False,
                },
            )
        except (jwt.PyJWTError, KeyError, ValueError) as exc:
            raise TokenError("invalid or expired sign-in") from exc
        if claims.get("token_use") != "access":
            raise TokenError("invalid or expired sign-in")
        if claims.get("client_id") != self._client_id:
            raise TokenError("invalid or expired sign-in")
        groups = claims.get("cognito:groups") or []
        if not isinstance(groups, list):
            raise TokenError("invalid or expired sign-in")
        return CognitoClaims(
            sub=str(claims["sub"]),
            username=str(claims.get("username", "")),
            groups=frozenset(map(str, groups)),
        )
