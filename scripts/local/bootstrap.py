"""Create the local stack's resources: what the CDK stacks and scripts/aws/put-secrets.sh create on AWS.

Idempotent; scripts/local/up.sh runs it on every start, with infra/local/stack.env exported:

- the DynamoDB tables (``sbc_aws.schema``) in DynamoDB Local;
- the data bucket in S3Mock;
- the secrets in the local Parameter Store (moto) under ``SSM_PREFIX``, as ``put-secrets.sh`` writes them on
  AWS: the OpenRouter and Langfuse keys from ``.env``, and a session secret generated once and kept in
  ``.local/secrets.env``. Parameters under the prefix that ``.env`` no longer has are removed.

It prints parameter names, never values. docs/local/RUNBOOK.md has the details.
"""

from __future__ import annotations

import argparse
import os
import secrets
import sys
import time
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


def wanted_parameters(dotenv: Mapping[str, str | None], generated: Mapping[str, str]) -> dict[str, str]:
    """Variable name -> value for every secret the local Parameter Store should hold."""
    wanted = {name: str(dotenv[name]) for name in SECRETS_FROM_DOTENV if dotenv.get(name)}
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

    dotenv = dotenv_values(args.dotenv) if args.dotenv.exists() else {}
    wanted = wanted_parameters(dotenv, generated_secrets(RUNTIME_DIR / "secrets.env"))
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


LOCAL_ENDPOINTS = ("AWS_ENDPOINT_URL_DYNAMODB", "AWS_ENDPOINT_URL_S3", "AWS_ENDPOINT_URL_SSM")

if __name__ == "__main__":
    # Never against a real account: every service this script calls must point at a local stand-in.
    if missing := [name for name in LOCAL_ENDPOINTS if not os.environ.get(name)]:
        raise SystemExit(f"refusing to run without {', '.join(missing)}: this is for the local stack only")
    main()
