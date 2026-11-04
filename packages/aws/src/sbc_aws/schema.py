"""Table definitions, matching what the CDK stack creates (docs/aws/MIGRATION_PLAN.md section 6.3).

Used to create the tables in DynamoDB Local and in tests; the CDK stack (WP9) must define the same keys and
TTL attributes, and its template tests compare against :data:`TABLE_SPECS`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sbc_aws.ddb import APP_TTL_ATTRIBUTE, Tables

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
