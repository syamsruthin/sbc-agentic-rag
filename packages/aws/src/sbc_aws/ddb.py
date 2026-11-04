"""DynamoDB basics shared by the adapters: table names, the clock, TTL values and item (de)serialization.

The low-level client is used everywhere (typed by ``boto3-stubs``); items are converted with boto3's own
``TypeSerializer``/``TypeDeserializer`` so numbers round-trip as ``Decimal`` and are converted back here.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import boto3
from boto3.dynamodb.types import TypeDeserializer, TypeSerializer

from sbc_core.config import EnvSettings

if TYPE_CHECKING:
    from mypy_boto3_dynamodb.client import DynamoDBClient
    from mypy_boto3_dynamodb.type_defs import AttributeValueTypeDef

Clock = Callable[[], float]
"""Seconds since the epoch; injectable so tests control time."""

APP_TTL_ATTRIBUTE = "expires_at"
"""TTL attribute of the ``app`` and ``audit`` tables (the checkpoints table uses ``ttl``, see schema.py)."""

_SERIALIZER = TypeSerializer()
_DESERIALIZER = TypeDeserializer()


class TablesNotConfiguredError(RuntimeError):
    """The DynamoDB table names are missing from the settings."""


@dataclass(frozen=True)
class Tables:
    app: str
    audit: str
    checkpoints: str

    @classmethod
    def from_env(cls, env: EnvSettings) -> Tables:
        names = (env.ddb_table_app, env.ddb_table_audit, env.ddb_table_checkpoints)
        if not all(names):
            raise TablesNotConfiguredError(
                "DDB_TABLE_APP, DDB_TABLE_AUDIT and DDB_TABLE_CHECKPOINTS must be set"
            )
        app, audit, checkpoints = (str(n) for n in names)
        return cls(app=app, audit=audit, checkpoints=checkpoints)


def session_for(env: EnvSettings) -> boto3.Session:
    """A boto3 session in the configured region, using the default credential chain (the Lambda role)."""
    return boto3.Session(region_name=env.aws_region)


def dynamodb_client(session: boto3.Session, endpoint_url: str | None = None) -> DynamoDBClient:
    return session.client("dynamodb", endpoint_url=endpoint_url)


def ttl_in(seconds: float, clock: Clock = time.time) -> int:
    """An epoch-seconds TTL value ``seconds`` from now."""
    return int(clock() + seconds)


def to_item(values: Mapping[str, Any]) -> dict[str, AttributeValueTypeDef]:
    """Plain Python values to DynamoDB attribute values (floats become Decimal; ``None`` values dropped)."""
    return {k: _SERIALIZER.serialize(_decimals(v)) for k, v in values.items() if v is not None}


def from_item(item: Mapping[str, Any]) -> dict[str, Any]:
    """DynamoDB attribute values to plain Python values (Decimal becomes int or float)."""
    return {k: _plain(_DESERIALIZER.deserialize(v)) for k, v in item.items()}


def _decimals(value: Any) -> Any:
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, Mapping):
        return {k: _decimals(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_decimals(v) for v in value]
    return value


def _plain(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_plain(v) for v in value]
    if isinstance(value, set):
        return {_plain(v) for v in value}
    return value
