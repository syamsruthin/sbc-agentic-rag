"""WP5: broker tokens (Cognito, RS256 via JWKS), public session tokens (HS256 after Turnstile), the route and
group rules, and the demo account. Keys are generated per run; no network calls."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from sbc_api.app import create_app
from sbc_api.auth.anon import SessionTokens
from sbc_api.auth.cognito import CognitoVerifier, TokenError, issuer
from sbc_api.auth.principal import BearerAuthenticator, Principal, admin_principal
from sbc_api.auth.turnstile import SITEVERIFY_URL, TurnstileVerifier
from sbc_api.deps import ApiState
from sbc_core.config import BotName
from tests.unit.api.conftest import Harness
from tests.unit.test_graph import ScriptedChat, classified, reply
from tests.unit.test_tools import GOLD

REGION, POOL, CLIENT = "us-east-1", "us-east-1_TestPool", "test-app-client"
SESSION_SECRET = "s" * 40
DEMO_SUB = "11111111-2222-3333-4444-555555555555"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


class FakeKeys:
    """Serves KEY's public half, like the pool's JWKS endpoint would (by ``kid``)."""

    def __init__(self) -> None:
        self.calls = 0

    def get_signing_key_from_jwt(self, token: str) -> Any:
        self.calls += 1
        if jwt.get_unverified_header(token).get("kid") != "k1":
            raise jwt.PyJWKClientError("unknown kid")
        return jwt.PyJWK(json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(KEY.public_key())) | {"kid": "k1"})


def cognito_token(
    *,
    key: Any = KEY,
    kid: str = "k1",
    ttl: int = 3600,
    groups: object = ("broker",),
    **overrides: Any,
) -> str:
    """A Cognito-shaped access token; ``overrides`` replace claims (None removes one)."""
    now = int(time.time())
    claims: dict[str, Any] = {
        "sub": "broker-sub-1", "iss": issuer(REGION, POOL), "client_id": CLIENT, "token_use": "access",
        "scope": "openid", "auth_time": now, "iat": now, "exp": now + ttl, "jti": "j1", "username": "broker1",
        "cognito:groups": list(groups) if isinstance(groups, tuple) else groups,
    }  # fmt: skip
    claims.update(overrides)
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": kid})


def verifier(keys: FakeKeys | None = None) -> CognitoVerifier:
    return CognitoVerifier(region=REGION, user_pool_id=POOL, app_client_id=CLIENT, keys=keys or FakeKeys())


def turnstile(success: bool | None) -> TurnstileVerifier:
    """``success=None`` simulates Cloudflare being unreachable."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == SITEVERIFY_URL
        form = dict(x.split("=", 1) for x in request.content.decode().split("&"))
        assert form["secret"] == "turnstile-secret"  # noqa: S105 - a fake value
        if success is None:
            raise httpx.ConnectError("down")
        return httpx.Response(
            200, json={"success": success, "error-codes": [] if success else ["invalid-input"]}
        )

    return TurnstileVerifier("turnstile-secret", client=httpx.Client(transport=httpx.MockTransport(handler)))


def secured(harness: Harness, *, ts: TurnstileVerifier | None = None, **chats: ScriptedChat) -> TestClient:
    """An app with the deployed authenticator (not the local developer one)."""
    state: ApiState = harness.state(**chats)
    state.session_tokens = SessionTokens(SESSION_SECRET)
    state.authenticator = BearerAuthenticator(
        cognito=verifier(), sessions=state.session_tokens, demo_user_ids=frozenset({DEMO_SUB})
    )
    state.turnstile = ts if ts is not None else turnstile(True)
    return TestClient(create_app(state))


def bearer(token: str) -> dict[str, str]:
    return {"authorization": f"Bearer {token}"}


def _lookup() -> ScriptedChat:
    return ScriptedChat(
        classified(intent="lookup", plan_mentions=["Example Gold"], question_ids=["overall_deductible"]),
        reply("The overall deductible on {{P1.name}} is {{F1}}."),
    )


# ------------------------------------------------------------------ the Cognito verifier


def test_a_valid_access_token_gives_the_subject_and_groups() -> None:
    claims = verifier().verify(cognito_token(groups=("broker", "admin")))
    assert (claims.sub, claims.username, claims.groups) == (
        "broker-sub-1",
        "broker1",
        frozenset({"broker", "admin"}),
    )


@pytest.mark.parametrize(
    "make",
    [
        lambda: cognito_token(ttl=-120),  # expired (beyond the 30 s leeway)
        lambda: cognito_token(iss="https://cognito-idp.us-east-1.amazonaws.com/other-pool"),
        lambda: cognito_token(client_id="another-client"),
        lambda: cognito_token(token_use="id"),  # noqa: S106 - an ID token is not an access token
        lambda: cognito_token(key=OTHER_KEY),  # signed by a key that isn't the pool's
        lambda: cognito_token(kid="unknown"),
        lambda: cognito_token(client_id=None),
        lambda: cognito_token(groups="broker"),  # groups must be a list
        lambda: jwt.encode({"sub": "x", "token_use": "access"}, "a-shared-secret-of-32-or-more-bytes!",
                           algorithm="HS256",
                           headers={"kid": "k1"}),  # algorithm confusion
        lambda: "not.a.jwt",
    ],
)  # fmt: skip
def test_bad_tokens_are_refused(make: Callable[[], str]) -> None:
    with pytest.raises(TokenError, match="invalid or expired sign-in"):
        verifier().verify(make())


# ------------------------------------------------------------------ broker routes


def test_a_broker_with_a_valid_token_can_chat(harness: Harness) -> None:
    client = secured(harness, broker=_lookup())
    r = client.post("/api/broker/chat", json={"message": "Gold deductible?"}, headers=bearer(cognito_token()))
    assert r.status_code == 200
    assert '"plan_keys":["' + GOLD + '"]' in r.text


def test_broker_routes_need_a_broker_token(harness: Harness) -> None:
    client = secured(harness)
    r = client.get("/api/broker/plans/search?q=Gold")
    assert r.status_code == 401
    assert r.headers["www-authenticate"] == "Bearer"
    assert (
        client.get("/api/broker/plans/search?q=Gold", headers={"authorization": "Basic abc"}).status_code
        == 401
    )
    expired = bearer(cognito_token(ttl=-120))
    assert client.get("/api/broker/plans/search?q=Gold", headers=expired).status_code == 401
    no_group = bearer(cognito_token(groups=("someone-else",)))
    assert client.get("/api/broker/plans/search?q=Gold", headers=no_group).status_code == 403
    assert client.get("/api/broker/plans/search?q=Gold", headers=bearer(cognito_token())).status_code == 200


def test_tokens_only_work_on_their_own_bot(harness: Harness) -> None:
    client = secured(harness)
    public = client.post("/api/public/session", json={"turnstile_token": "t", "state": "XX"}).json()["token"]
    assert client.get("/api/broker/plans/search?q=Gold", headers=bearer(public)).status_code == 401
    assert client.get("/api/public/plans/search?q=Gold", headers=bearer(cognito_token())).status_code == 401
    assert client.get("/api/public/plans/search?q=Gold", headers=bearer(public)).status_code == 200


# ------------------------------------------------------------------ public sessions


def test_a_public_session_starts_after_turnstile_and_fixes_the_state(harness: Harness) -> None:
    client = secured(harness, public=_lookup())
    started = client.post("/api/public/session", json={"turnstile_token": "tok", "state": "XX"})
    assert started.status_code == 200
    body = started.json()
    assert body["state"] == "XX"
    assert body["expires_at"] > time.time() + 23 * 3600
    token = body["token"]
    # The token's state wins over the query string: the public bot never looks across states.
    found = client.get("/api/public/plans/search?q=Gold&state=YY", headers=bearer(token)).json()
    assert [p["plan_key"] for p in found["plans"]] == [GOLD]
    r = client.post("/api/public/chat", json={"message": "Deductible on Gold?", "user_state": "YY"},
                    headers=bearer(token))  # fmt: skip
    assert r.status_code == 200
    assert GOLD in r.text


def test_turnstile_failures_are_reported(harness: Harness) -> None:
    body = {"turnstile_token": "tok", "state": "XX"}
    assert secured(harness, ts=turnstile(False)).post("/api/public/session", json=body).status_code == 403
    assert secured(harness, ts=turnstile(None)).post("/api/public/session", json=body).status_code == 503
    bad = secured(harness)
    assert bad.post("/api/public/session", json={"turnstile_token": "tok", "state": "xx"}).status_code == 422
    assert bad.post("/api/public/session", json={"turnstile_token": "", "state": "XX"}).status_code == 422


def test_the_viewer_ip_from_cloudfront_goes_to_turnstile(harness: Harness) -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.content.decode())
        return httpx.Response(200, json={"success": True})

    ts = TurnstileVerifier("turnstile-secret", client=httpx.Client(transport=httpx.MockTransport(handler)))
    client = secured(harness, ts=ts)
    headers = {"cloudfront-viewer-address": "203.0.113.7:51234"}
    assert client.post(
        "/api/public/session", json={"turnstile_token": "t", "state": "XX"}, headers=headers
    ).is_success
    assert "remoteip=203.0.113.7" in seen[0]


def test_session_tokens_reject_tampering_expiry_and_other_audiences() -> None:
    tokens = SessionTokens(SESSION_SECRET)
    token, session = tokens.issue("CA")
    assert tokens.verify(token) == session
    with pytest.raises(TokenError):
        SessionTokens("t" * 40).verify(token)  # another secret
    with pytest.raises(TokenError):
        tokens.verify(token[:-2] + ("AA" if token[-2:] != "AA" else "BB"))
    clock = [time.time()]
    timed = SessionTokens(SESSION_SECRET, ttl_seconds=60, clock=lambda: clock[0])
    short, _ = timed.issue("CA")
    clock[0] += 61
    with pytest.raises(TokenError):
        timed.verify(short)
    claims = {"iss": "sbc-api", "aud": "elsewhere", "sub": "x", "st": "CA", "iat": 1, "exp": 2**40}
    foreign = jwt.encode(claims, SESSION_SECRET, algorithm="HS256")
    with pytest.raises(TokenError):
        tokens.verify(foreign)
    with pytest.raises(ValueError, match="at least 32 bytes"):
        SessionTokens("short")


def test_locally_the_session_route_skips_turnstile(harness: Harness) -> None:
    client = harness.client()  # APP_ENV=local, no TURNSTILE_SECRET_KEY
    r = client.post("/api/public/session", json={"turnstile_token": "anything", "state": "XX"})
    assert r.status_code == 200
    assert r.json()["state"] == "XX"


# ------------------------------------------------------------------ admins and the demo account


class _Req:
    def __init__(self, app: Any, token: str) -> None:
        self.app = app
        self.headers = {"authorization": f"Bearer {token}"}


def _admin(harness: Harness, token: str) -> Principal:
    app = secured(harness).app
    return admin_principal(_Req(app, token))  # type: ignore[arg-type]


def test_only_admins_pass_the_admin_check(harness: Harness) -> None:
    assert _admin(harness, cognito_token(groups=("broker", "admin"))).is_admin
    with pytest.raises(Exception, match="admins only"):
        _admin(harness, cognito_token())


def test_the_demo_account_is_a_broker_but_never_an_admin(harness: Harness) -> None:
    token = cognito_token(sub=DEMO_SUB, groups=("broker", "admin"))
    with pytest.raises(Exception, match="admins only"):
        _admin(harness, token)
    client = secured(harness)
    assert client.get("/api/broker/plans/search?q=Gold", headers=bearer(token)).status_code == 200
    authenticator = client.app.state.api.authenticator  # type: ignore[attr-defined]
    principal = authenticator.authenticate(_Req(client.app, token), BotName.BROKER)
    assert principal.is_demo
    assert principal.groups == {"broker"}
