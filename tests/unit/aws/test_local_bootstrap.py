"""scripts/local/bootstrap.py: the local stack's resources, here against moto's in-memory AWS."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING

import boto3
import pytest

from tests.unit.aws.conftest import BUCKET

if TYPE_CHECKING:
    from mypy_boto3_s3.client import S3Client
    from mypy_boto3_ssm.client import SSMClient

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "local" / "bootstrap.py"


@pytest.fixture(scope="module")
def bootstrap() -> ModuleType:
    spec = importlib.util.spec_from_file_location("local_bootstrap", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _names(ssm: SSMClient) -> dict[str, str]:
    params = ssm.get_parameters_by_path(Path="/sbc/local", Recursive=True, WithDecryption=True)["Parameters"]
    return {p["Name"]: p["Value"] for p in params}


def test_generated_secrets_are_created_once_and_kept_private(bootstrap: ModuleType, tmp_path: Path) -> None:
    path = tmp_path / ".local" / "secrets.env"
    first = bootstrap.generated_secrets(path)
    assert set(first) == {"SESSION_JWT_SECRET"}
    assert len(first["SESSION_JWT_SECRET"]) >= 32
    assert path.stat().st_mode & 0o777 == 0o600
    assert bootstrap.generated_secrets(path) == first


def test_only_secrets_that_are_set_are_wanted(bootstrap: ModuleType) -> None:
    dotenv = {"OPENROUTER_API_KEY_BROKER": "k1", "OPENROUTER_API_KEY_PUBLIC": "", "UNRELATED": "x"}
    wanted = bootstrap.wanted_parameters(dotenv, {"SESSION_JWT_SECRET": "s"})
    assert wanted == {
        "OPENROUTER_API_KEY_BROKER": "k1",
        "TURNSTILE_SECRET_KEY": bootstrap.TURNSTILE_TEST_SECRETS["pass"],
        "SESSION_JWT_SECRET": "s",
    }


def test_turnstile_uses_a_test_secret_unless_dotenv_has_one(bootstrap: ModuleType) -> None:
    blocked = bootstrap.wanted_parameters({}, {}, "block")
    assert blocked["TURNSTILE_SECRET_KEY"] == bootstrap.TURNSTILE_TEST_SECRETS["block"]
    real = bootstrap.wanted_parameters({"TURNSTILE_SECRET_KEY": "0x-real"}, {}, "block")
    assert real["TURNSTILE_SECRET_KEY"] == "0x-real"  # noqa: S105 - a fake value


def test_parameters_mirror_what_is_wanted(bootstrap: ModuleType, ssm: SSMClient) -> None:
    bootstrap.sync_parameters(ssm, "/sbc/local/", {"A_KEY": "1", "B_KEY": "2"})
    put, removed = bootstrap.sync_parameters(ssm, "/sbc/local/", {"B_KEY": "3"})
    assert (put, removed) == (["B_KEY"], ["A_KEY"])
    assert _names(ssm) == {"/sbc/local/B_KEY": "3"}


def test_the_bucket_is_created_when_missing(bootstrap: ModuleType, s3: S3Client) -> None:
    bootstrap.ensure_bucket(s3, "another-bucket")
    bootstrap.ensure_bucket(s3, BUCKET)  # already there: harmless
    assert {b["Name"] for b in s3.list_buckets()["Buckets"]} >= {"another-bucket", BUCKET}


def test_the_user_pool_and_users_are_created_once(bootstrap: ModuleType, tmp_path: Path) -> None:
    idp = boto3.Session(region_name="us-east-1").client("cognito-idp")
    passwords = bootstrap.user_passwords(tmp_path / "users.env")
    assert set(passwords) == set(bootstrap.USERS)
    assert all(len(p) >= 12 and p.lower() != p and p.upper() != p for p in passwords.values())
    pool_id, client_id = bootstrap.ensure_user_pool(idp)
    subs = bootstrap.ensure_users(idp, pool_id, passwords)
    assert (pool_id, client_id) == bootstrap.ensure_user_pool(idp)
    assert subs == bootstrap.ensure_users(idp, pool_id, passwords)
    client = idp.describe_user_pool_client(UserPoolId=pool_id, ClientId=client_id)["UserPoolClient"]
    assert "ALLOW_USER_SRP_AUTH" in client["ExplicitAuthFlows"]
    assert "ClientSecret" not in client
    groups = {
        user: {
            g["GroupName"]
            for g in idp.admin_list_groups_for_user(UserPoolId=pool_id, Username=user)["Groups"]
        }
        for user in bootstrap.USERS
    }
    assert groups == {"admin": {"admin", "broker"}, "broker": {"broker"}, "demo-broker": {"broker"}}
    token = idp.admin_initiate_auth(
        UserPoolId=pool_id,
        ClientId=client_id,
        AuthFlow="ADMIN_USER_PASSWORD_AUTH",
        AuthParameters={"USERNAME": "broker", "PASSWORD": passwords["broker"]},
    )
    assert "AccessToken" in token["AuthenticationResult"]
