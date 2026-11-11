"""Create the local stack's resources: what the CDK stacks and scripts/aws/put-secrets.sh create on AWS.

Idempotent; scripts/local/up.sh runs it on every start, with infra/local/stack.env exported:

- the DynamoDB tables (``sbc_aws.schema``) in DynamoDB Local;
- the data bucket in S3Mock;
- the secrets in the local Parameter Store (moto) under ``SSM_PREFIX``, as ``put-secrets.sh`` writes them on
  AWS: the OpenRouter and Langfuse keys from ``.env``, and a session secret generated once and kept in
  ``.local/secrets.env``. Parameters under the prefix that ``.env`` no longer has are removed;
- Turnstile: Cloudflare's always-pass test secret (or, with ``--turnstile block``, the always-fail one) unless
  ``.env`` sets a real one;
- the broker sign-in, as the CDK ``Auth`` construct and ``scripts/aws/seed-users.sh`` create it on AWS: the
  user pool, its SPA client, the ``broker`` and ``admin`` groups and three users. moto keeps them in memory,
  so they are recreated after every moto restart; moto's random generator is seeded first, so the pool ID,
  client ID and the users' ``sub``s come out the same each time (threads stay owned by the same user). The
  passwords are generated once and kept in ``.local/users.env``;
- ``.local/runtime.env``: the generated IDs for the API (and, from L5, the frontends).

It prints names and IDs, never secret values. docs/local/RUNBOOK.md has the details.
"""

from __future__ import annotations

import argparse
import os
import secrets
import sys
import time
import urllib.request
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from dotenv import dotenv_values

from sbc_aws.ddb import Tables
from sbc_aws.schema import create_tables
from sbc_core.config import EnvSettings

if TYPE_CHECKING:
    from mypy_boto3_cognito_idp.client import CognitoIdentityProviderClient
    from mypy_boto3_s3.client import S3Client
    from mypy_boto3_ssm.client import SSMClient

REPO = Path(__file__).resolve().parents[2]
RUNTIME_DIR = REPO / ".local"

# The secrets put-secrets.sh stores on AWS, copied from .env when set there.
SECRETS_FROM_DOTENV = (
    "OPENROUTER_API_KEY_BROKER",
    "OPENROUTER_API_KEY_PUBLIC",
    "TURNSTILE_SECRET_KEY",
    "LANGFUSE_PUBLIC_KEY",
    "LANGFUSE_SECRET_KEY",
    "LANGFUSE_HOST",
)
# Generated once, kept in .local/secrets.env so sessions survive restarts.
GENERATED_SECRETS = ("SESSION_JWT_SECRET",)

# Cloudflare's published Turnstile test secrets (https://developers.cloudflare.com/turnstile/troubleshooting/testing/):
# every token passes, or every token fails. They pair with the test site key 1x00000000000000000000AA.
TURNSTILE_TEST_SECRETS = {
    "pass": "1x0000000000000000000000000000000AA",
    "block": "2x0000000000000000000000000000000AA",
}

# The broker sign-in, as infra/cdk/sbc_infra/constructs/auth.py defines it (names use the local prefix).
POOL_NAME = "sbc-local-brokers"
CLIENT_NAME = "sbc-local-spa"
GROUPS = ("broker", "admin")
MOTO_SEED = 20271
# username -> (groups, email, permanent password?); ``demo-broker`` is the shared demo account (portfolio
# limits). No user with a temporary password: moto's NEW_PASSWORD_REQUIRED challenge leaves out the
# ``userAttributes`` that amazon-cognito-identity-js parses, so that first-sign-in step only works on AWS.
USERS: dict[str, tuple[tuple[str, ...], str, bool]] = {
    "admin": (("broker", "admin"), "admin@local.test", True),
    "broker": (("broker",), "broker@local.test", True),
    "demo-broker": (("broker",), "demo@local.test", True),
}
DEMO_USER = "demo-broker"


def generated_secrets(path: Path) -> dict[str, str]:
    """The generated secrets, creating any that are missing (the file is readable by you only)."""
    values = {k: v for k, v in dotenv_values(path).items() if v} if path.exists() else {}
    missing = [name for name in GENERATED_SECRETS if name not in values]
    if missing:
        for name in missing:
            values[name] = secrets.token_urlsafe(48)  # >= 32 bytes, like `openssl rand -base64 48`
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(f"{k}={v}\n" for k, v in sorted(values.items())))
        path.chmod(0o600)
    return values


def wanted_parameters(
    dotenv: Mapping[str, str | None], generated: Mapping[str, str], turnstile: str = "pass"
) -> dict[str, str]:
    """Variable name -> value for every secret the local Parameter Store should hold."""
    wanted = {name: str(dotenv[name]) for name in SECRETS_FROM_DOTENV if dotenv.get(name)}
    wanted.setdefault("TURNSTILE_SECRET_KEY", TURNSTILE_TEST_SECRETS[turnstile])
    wanted.update(generated)
    return wanted


def sync_parameters(ssm: SSMClient, prefix: str, wanted: Mapping[str, str]) -> tuple[list[str], list[str]]:
    """Make the parameters under ``prefix`` exactly ``wanted``. Returns (set, removed) names."""
    existing: list[str] = []
    for page in ssm.get_paginator("get_parameters_by_path").paginate(Path=prefix.rstrip("/"), Recursive=True):
        existing += [p["Name"] for p in page["Parameters"] if "Name" in p]
    removed = [name for name in existing if name.rsplit("/", 1)[-1] not in wanted]
    for name in removed:
        ssm.delete_parameter(Name=name)
    for name, value in wanted.items():
        ssm.put_parameter(Name=f"{prefix}{name}", Value=value, Type="SecureString", Overwrite=True)
    return sorted(wanted), sorted(n.rsplit("/", 1)[-1] for n in removed)


def ensure_bucket(s3: S3Client, bucket: str) -> None:
    try:
        s3.head_bucket(Bucket=bucket)
    except ClientError:
        s3.create_bucket(Bucket=bucket)


def seed_moto(endpoint: str, seed: int = MOTO_SEED) -> None:
    """Make moto's generated IDs repeatable (its ``/moto-api/seed`` endpoint)."""
    url = f"{endpoint.rstrip('/')}/moto-api/seed?a={seed}"
    if not url.startswith(("http://", "https://")):
        raise ValueError(f"not an HTTP endpoint: {endpoint}")
    request = urllib.request.Request(url, method="POST")  # noqa: S310 - checked above
    with urllib.request.urlopen(request, timeout=10):  # noqa: S310 - checked above
        pass


def user_passwords(path: Path) -> dict[str, str]:
    """A password per local user, generated once (12+ characters with upper, lower and digits, as the pool's
    policy requires) and kept in ``path``, readable by you only."""
    values = {k: v for k, v in dotenv_values(path).items() if v} if path.exists() else {}
    if missing := [user for user in USERS if user not in values]:
        for user in missing:
            values[user] = f"Local{secrets.token_urlsafe(12)}9a"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(f"{k}={v}\n" for k, v in values.items()))
        path.chmod(0o600)
    return values


def ensure_user_pool(idp: CognitoIdentityProviderClient) -> tuple[str, str]:
    """The pool and its SPA client as the CDK creates them, created if missing: (pool ID, client ID)."""
    pools = [p for p in idp.list_user_pools(MaxResults=60)["UserPools"] if p.get("Name") == POOL_NAME]
    if pools:
        pool_id = pools[0]["Id"]
    else:
        pool_id = idp.create_user_pool(
            PoolName=POOL_NAME,
            AliasAttributes=["email"],
            MfaConfiguration="OPTIONAL",
            Policies={
                "PasswordPolicy": {
                    "MinimumLength": 12,
                    "RequireLowercase": True,
                    "RequireUppercase": True,
                    "RequireNumbers": True,
                    "RequireSymbols": False,
                }
            },
            AdminCreateUserConfig={"AllowAdminCreateUserOnly": True},
        )["UserPool"]["Id"]
        idp.set_user_pool_mfa_config(
            UserPoolId=pool_id, SoftwareTokenMfaConfiguration={"Enabled": True}, MfaConfiguration="OPTIONAL"
        )
    clients = idp.list_user_pool_clients(UserPoolId=pool_id, MaxResults=60)["UserPoolClients"]
    found = [c for c in clients if c.get("ClientName") == CLIENT_NAME]
    if found:
        return pool_id, found[0]["ClientId"]
    client_id = idp.create_user_pool_client(
        UserPoolId=pool_id,
        ClientName=CLIENT_NAME,
        GenerateSecret=False,
        # SRP as on AWS, plus two local-only flows: USER_PASSWORD_AUTH for the apps (moto's SRP can't finish
        # a sign-in: it looks the user up by the sub that amazon-cognito-identity-js sends back as USERNAME),
        # and ADMIN_USER_PASSWORD_AUTH for scripts' tokens (scripts/local/token.sh).
        ExplicitAuthFlows=[
            "ALLOW_USER_SRP_AUTH",
            "ALLOW_USER_PASSWORD_AUTH",
            "ALLOW_REFRESH_TOKEN_AUTH",
            "ALLOW_ADMIN_USER_PASSWORD_AUTH",
        ],
        PreventUserExistenceErrors="ENABLED",
        AccessTokenValidity=1,
        IdTokenValidity=1,
        RefreshTokenValidity=12,
        TokenValidityUnits={"AccessToken": "hours", "IdToken": "hours", "RefreshToken": "hours"},
        EnableTokenRevocation=True,
    )["UserPoolClient"]["ClientId"]
    return pool_id, client_id


def ensure_users(
    idp: CognitoIdentityProviderClient, pool_id: str, passwords: Mapping[str, str]
) -> dict[str, str]:
    """The groups and users (created if missing, in a fixed order). Returns username -> sub."""
    existing_groups = {g["GroupName"] for g in idp.list_groups(UserPoolId=pool_id)["Groups"]}
    for group in GROUPS:
        if group not in existing_groups:
            idp.create_group(UserPoolId=pool_id, GroupName=group)
    subs: dict[str, str] = {}
    for user, (groups, email, permanent) in USERS.items():
        try:
            attrs = idp.admin_get_user(UserPoolId=pool_id, Username=user)["UserAttributes"]
        except idp.exceptions.UserNotFoundException:
            attrs = idp.admin_create_user(
                UserPoolId=pool_id,
                Username=user,
                TemporaryPassword=passwords[user],
                MessageAction="SUPPRESS",
                UserAttributes=[
                    {"Name": "email", "Value": email},
                    {"Name": "email_verified", "Value": "true"},
                ],
            )["User"]["Attributes"]
            if permanent:
                idp.admin_set_user_password(
                    UserPoolId=pool_id, Username=user, Password=passwords[user], Permanent=True
                )
            for group in groups:
                idp.admin_add_user_to_group(UserPoolId=pool_id, Username=user, GroupName=group)
        subs[user] = next(a["Value"] for a in attrs if a["Name"] == "sub")
    return subs


def write_runtime_env(path: Path, values: Mapping[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "# Written by scripts/local/bootstrap.py on every start; don't edit.\n"
        + "".join(f"{k}='{v}'\n" for k, v in values.items())
    )


def _retry[T](what: str, call: Callable[[], T], seconds: float) -> T:
    """Call ``call()`` until it stops raising AWS errors (a stand-in still starting) or ``seconds`` pass."""
    deadline = time.monotonic() + seconds
    while True:
        try:
            return call()
        except (BotoCoreError, ClientError) as exc:
            if time.monotonic() > deadline:
                raise SystemExit(f"{what} failed: {type(exc).__name__}: {exc}") from None
            time.sleep(1.0)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Create the local stack's resources (docs/local/RUNBOOK.md).")
    ap.add_argument("--dotenv", type=Path, default=REPO / ".env", help="where the secrets come from")
    ap.add_argument("--wait-seconds", type=float, default=60.0, help="how long to wait for the stand-ins")
    ap.add_argument(
        "--turnstile",
        choices=sorted(TURNSTILE_TEST_SECRETS),
        help="the Turnstile test secret: every token passes, or every token fails (remembered; default pass)",
    )
    args = ap.parse_args(argv)

    env = EnvSettings(_env_file=None)  # the exported stack settings only, not .env
    if not env.ssm_prefix or not env.s3_data_bucket:
        raise SystemExit("export infra/local/stack.env first (scripts/local/bootstrap.sh does)")
    session = boto3.Session(region_name=env.aws_region)

    tables = Tables.from_env(env)
    ddb = session.client("dynamodb")
    _retry("creating the tables", lambda: create_tables(ddb, tables), args.wait_seconds)
    print(f"tables: {tables.app}, {tables.audit}, {tables.checkpoints}")

    _retry("creating the bucket", lambda: ensure_bucket(session.client("s3"), env.s3_data_bucket or ""), 30)
    print(f"bucket: {env.s3_data_bucket}")

    turnstile_file = RUNTIME_DIR / "turnstile"
    turnstile = args.turnstile or (turnstile_file.read_text().strip() if turnstile_file.exists() else "pass")
    turnstile_file.write_text(turnstile + "\n")
    dotenv = dotenv_values(args.dotenv) if args.dotenv.exists() else {}
    wanted = wanted_parameters(dotenv, generated_secrets(RUNTIME_DIR / "secrets.env"), turnstile)
    put, removed = _retry(
        "writing the parameters",
        lambda: sync_parameters(session.client("ssm"), env.ssm_prefix or "", wanted),
        args.wait_seconds,
    )
    print(f"parameters under {env.ssm_prefix}: {', '.join(put)}")
    if removed:
        print(f"removed (no longer in {args.dotenv.name}): {', '.join(removed)}")
    for name in ("OPENROUTER_API_KEY_BROKER", "OPENROUTER_API_KEY_PUBLIC"):
        if name not in wanted:
            print(f"note: {name} isn't in {args.dotenv.name}, so that bot can't answer", file=sys.stderr)
    if dotenv.get("TURNSTILE_SECRET_KEY"):
        print(f"turnstile: the secret from {args.dotenv.name}")
    else:
        print(
            f"turnstile: Cloudflare test secret, every token {'passes' if turnstile == 'pass' else 'fails'}"
        )

    cognito_endpoint = os.environ["AWS_ENDPOINT_URL_COGNITO_IDENTITY_PROVIDER"]
    idp = session.client("cognito-idp")
    passwords = user_passwords(RUNTIME_DIR / "users.env")

    def sign_in_setup() -> tuple[str, str, dict[str, str]]:
        if not [p for p in idp.list_user_pools(MaxResults=60)["UserPools"] if p.get("Name") == POOL_NAME]:
            seed_moto(cognito_endpoint)  # a fresh moto: the same IDs as last time
        pool_id, client_id = ensure_user_pool(idp)
        return pool_id, client_id, ensure_users(idp, pool_id, passwords)

    pool_id, client_id, subs = _retry("creating the user pool", sign_in_setup, args.wait_seconds)
    print(
        f"user pool: {pool_id}, client {client_id}; users: {', '.join(subs)} (passwords in .local/users.env)"
    )

    write_runtime_env(
        RUNTIME_DIR / "runtime.env",
        {
            "COGNITO_USER_POOL_ID": pool_id,
            "COGNITO_APP_CLIENT_ID": client_id,
            "COGNITO_JWKS_URL": f"{cognito_endpoint.rstrip('/')}/{pool_id}/.well-known/jwks.json",
            "SBC_PORTFOLIO__DEMO_USER_IDS": f'["{subs[DEMO_USER]}"]',
        },
    )
    print(f"demo account: {DEMO_USER} (sub {subs[DEMO_USER]})")


LOCAL_ENDPOINTS = (
    "AWS_ENDPOINT_URL_DYNAMODB",
    "AWS_ENDPOINT_URL_S3",
    "AWS_ENDPOINT_URL_SSM",
    "AWS_ENDPOINT_URL_COGNITO_IDENTITY_PROVIDER",
)

if __name__ == "__main__":
    # Never against a real account: every service this script calls must point at a local stand-in.
    if missing := [name for name in LOCAL_ENDPOINTS if not os.environ.get(name)]:
        raise SystemExit(f"refusing to run without {', '.join(missing)}: this is for the local stack only")
    main()
