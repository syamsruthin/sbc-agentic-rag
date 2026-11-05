"""Table definitions, matching what the CDK stack creates (docs/aws/MIGRATION_PLAN.md section 6.3).

Used to create the tables in DynamoDB Local and in tests; the CDK stack (WP9) must define the same keys and
TTL attributes, and its template tests compare against :data:`TABLE_SPECS`.

    sbc-create-tables   # DDB_TABLE_APP/AUDIT/CHECKPOINTS; AWS_ENDPOINT_URL_DYNAMODB for DynamoDB Local
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from sbc_aws.ddb import APP_TTL_ATTRIBUTE, Tables
from sbc_core.config import EnvSettings

if TYPE_CHECKING:
    from mypy_boto3_dynamodb.client import DynamoDBClient

CHECKPOINT_TTL_ATTRIBUTE = "ttl"
"""The attribute ``langgraph_checkpoint_aws.DynamoDBSaver`` writes (spike 1, docs/aws/SPIKES.md)."""


@dataclass(frozen=True)
class TableSpec:
    role: str
    """``app``, ``audit`` or ``checkpoints``."""
    ttl_attribute: str
    partition_key: str = "PK"
    sort_key: str = "SK"


TABLE_SPECS: tuple[TableSpec, ...] = (
    TableSpec("app", APP_TTL_ATTRIBUTE),
    TableSpec("audit", APP_TTL_ATTRIBUTE),
    TableSpec("checkpoints", CHECKPOINT_TTL_ATTRIBUTE),
)


def create_tables(client: DynamoDBClient, tables: Tables) -> None:
    """Create any missing table (on-demand, string PK/SK) and enable its TTL. Safe to call repeatedly."""
    existing = set(client.list_tables()["TableNames"])
    for spec in TABLE_SPECS:
        name = getattr(tables, spec.role)
        if name in existing:
            continue
        client.create_table(
            TableName=name,
            AttributeDefinitions=[
                {"AttributeName": spec.partition_key, "AttributeType": "S"},
                {"AttributeName": spec.sort_key, "AttributeType": "S"},
            ],
            KeySchema=[
                {"AttributeName": spec.partition_key, "KeyType": "HASH"},
                {"AttributeName": spec.sort_key, "KeyType": "RANGE"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        client.get_waiter("table_exists").wait(TableName=name)
        client.update_time_to_live(
            TableName=name, TimeToLiveSpecification={"Enabled": True, "AttributeName": spec.ttl_attribute}
        )


def main(argv: list[str] | None = None) -> None:
    """Create the tables named in the environment, waiting for the endpoint (e.g. DynamoDB Local) to start."""
    ap = argparse.ArgumentParser(description="Create the API's DynamoDB tables (local development).")
    ap.add_argument("--wait-seconds", type=float, default=30.0, help="how long to wait for the endpoint")
    args = ap.parse_args(argv)
    env = EnvSettings()
    tables = Tables.from_env(env)
    client = boto3.Session(region_name=env.aws_region).client("dynamodb")
    deadline = time.monotonic() + args.wait_seconds
    while True:
        try:
            create_tables(client, tables)
            break
        except (BotoCoreError, ClientError) as exc:
            if time.monotonic() > deadline:
                raise SystemExit(f"could not create the tables: {type(exc).__name__}: {exc}") from None
            time.sleep(1.0)
    print(f"tables ready: {tables.app}, {tables.audit}, {tables.checkpoints}", file=sys.stderr)


if __name__ == "__main__":
    main()
