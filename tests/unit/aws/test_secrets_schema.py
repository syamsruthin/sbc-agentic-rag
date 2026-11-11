"""SSM secrets loading and the table definitions."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest

from sbc_aws.ddb import Tables, from_item, to_item
from sbc_aws.schema import TABLE_SPECS, create_tables
from tests.unit.aws.conftest import TABLES

if TYPE_CHECKING:
    from mypy_boto3_dynamodb.client import DynamoDBClient
    from mypy_boto3_ssm.client import SSMClient

from sbc_aws.secrets import load_ssm_parameters


def _put(ssm: SSMClient, name: str, value: str, kind: str = "SecureString") -> None:
    ssm.put_parameter(Name=name, Value=value, Type=kind)  # type: ignore[arg-type]


def test_parameters_become_environment_variables_by_last_segment(ssm: SSMClient) -> None:
    _put(ssm, "/sbc/dev/OPENROUTER_API_KEY_BROKER", "sk-or-broker")
    _put(ssm, "/sbc/dev/SESSION_JWT_SECRET", "jwt-secret")
    _put(ssm, "/sbc/dev/LANGFUSE_HOST", "https://cloud.langfuse.com", "String")
    _put(ssm, "/sbc/prd/OPENROUTER_API_KEY_BROKER", "sk-or-prod")  # another environment: not loaded
    environ: dict[str, str] = {}
    loaded = load_ssm_parameters("/sbc/dev/", ssm, environ=environ)
    assert environ == {
        "OPENROUTER_API_KEY_BROKER": "sk-or-broker",
        "SESSION_JWT_SECRET": "jwt-secret",
        "LANGFUSE_HOST": "https://cloud.langfuse.com",
    }
    assert loaded.names == ("LANGFUSE_HOST", "OPENROUTER_API_KEY_BROKER", "SESSION_JWT_SECRET")
    assert "sk-or-broker" not in repr(loaded)


@pytest.mark.parametrize("app_env", ["local", "dev"])
def test_the_api_loads_parameter_store_whenever_a_prefix_is_set(
    ssm: SSMClient, monkeypatch: pytest.MonkeyPatch, app_env: str
) -> None:
    """Locally too: the full local stack points the SDK at a local Parameter Store."""
    from sbc_api.deps import load_secrets_into_environment

    _put(ssm, "/sbc/local/SESSION_JWT_SECRET", "from-parameter-store")
    monkeypatch.setenv("APP_ENV", app_env)
    monkeypatch.setenv("SSM_PREFIX", "/sbc/local/")
    monkeypatch.setenv("SESSION_JWT_SECRET", "")  # restored (unset) after the test, whatever the loader sets
    monkeypatch.delenv("SESSION_JWT_SECRET")
    assert load_secrets_into_environment() == ["SESSION_JWT_SECRET"]
    assert os.environ.get("SESSION_JWT_SECRET") == "from-parameter-store"


def test_the_api_skips_parameter_store_without_a_prefix(
    ssm: SSMClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sbc_api.deps import load_secrets_into_environment

    _put(ssm, "/sbc/local/SESSION_JWT_SECRET", "unused")
    monkeypatch.delenv("SSM_PREFIX", raising=False)
    monkeypatch.setenv("SESSION_JWT_SECRET", "")  # restored (unset) after the test, whatever the loader sets
    monkeypatch.delenv("SESSION_JWT_SECRET")
    assert load_secrets_into_environment() == []
    assert "SESSION_JWT_SECRET" not in os.environ


def test_every_page_is_read(ssm: SSMClient) -> None:
    for i in range(23):  # more than one page (Parameter Store returns at most 10 per call)
        _put(ssm, f"/sbc/dev/KEY_{i:02d}", f"value-{i}")
    environ: dict[str, str] = {}
    assert len(load_ssm_parameters("/sbc/dev", ssm, environ=environ).names) == 23
    assert environ["KEY_22"] == "value-22"


def test_existing_variables_are_kept_unless_overwrite(ssm: SSMClient) -> None:
    _put(ssm, "/sbc/dev/SESSION_JWT_SECRET", "from-ssm")
    environ = {"SESSION_JWT_SECRET": "from-shell"}
    loaded = load_ssm_parameters("/sbc/dev/", ssm, environ=environ)
    assert (environ["SESSION_JWT_SECRET"], loaded.kept) == ("from-shell", ("SESSION_JWT_SECRET",))
    load_ssm_parameters("/sbc/dev/", ssm, environ=environ, overwrite=True)
    assert environ["SESSION_JWT_SECRET"] == "from-ssm"  # noqa: S105 - a fake value


def test_names_that_are_not_variables_are_skipped(ssm: SSMClient) -> None:
    _put(ssm, "/sbc/dev/lower-case-name", "x")
    _put(ssm, "/sbc/dev/nested/GOOD_NAME", "y")
    environ: dict[str, str] = {}
    loaded = load_ssm_parameters("/sbc/dev/", ssm, environ=environ)
    assert environ == {"GOOD_NAME": "y"}
    assert loaded.skipped == ("/sbc/dev/lower-case-name",)


def test_prefix_must_be_absolute(ssm: SSMClient) -> None:
    with pytest.raises(ValueError, match="absolute"):
        load_ssm_parameters("sbc/dev/", ssm, environ={})


def test_tables_have_string_keys_and_the_right_ttl_attribute(ddb: DynamoDBClient) -> None:
    for spec in TABLE_SPECS:
        name = getattr(TABLES, spec.role)
        table = ddb.describe_table(TableName=name)["Table"]
        assert [k["AttributeName"] for k in table["KeySchema"]] == ["PK", "SK"]
        assert table["BillingModeSummary"]["BillingMode"] == "PAY_PER_REQUEST"
        ttl = ddb.describe_time_to_live(TableName=name)["TimeToLiveDescription"]
        assert ttl.get("AttributeName") == spec.ttl_attribute
    assert {s.role: s.ttl_attribute for s in TABLE_SPECS}["checkpoints"] == "ttl"


def test_creating_tables_twice_is_harmless(ddb: DynamoDBClient) -> None:
    create_tables(ddb, TABLES)
    assert set(ddb.list_tables()["TableNames"]) >= {TABLES.app, TABLES.audit, TABLES.checkpoints}


def test_tables_from_env_require_all_three_names() -> None:
    from sbc_core.config import EnvSettings

    env = EnvSettings.model_validate(
        {"ddb_table_app": "a", "ddb_table_audit": "b", "ddb_table_checkpoints": "c"}
    )
    assert Tables.from_env(env) == Tables("a", "b", "c")
    with pytest.raises(RuntimeError, match="DDB_TABLE_CHECKPOINTS"):
        Tables.from_env(EnvSettings.model_validate({"ddb_table_app": "a", "ddb_table_audit": "b"}))


def test_items_round_trip_with_numbers_and_lists() -> None:
    values = {"PK": "X", "n": 3, "cost": 0.0123, "tags": ["a", "b"], "nested": {"k": 1.5}, "skip": None}
    assert from_item(to_item(values)) == {k: v for k, v in values.items() if v is not None}
