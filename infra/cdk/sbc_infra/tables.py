"""The DynamoDB tables, as plain data: role, partition key, sort key and TTL attribute.

This must equal ``sbc_aws.schema.TABLE_SPECS`` (what the API, DynamoDB Local and the tests use). The
CDK project can't import the app's packages, so the root test ``tests/unit/test_infra_tables.py`` compares
the two.
"""

from __future__ import annotations

TABLES: tuple[tuple[str, str, str, str], ...] = (
    ("app", "PK", "SK", "expires_at"),
    ("audit", "PK", "SK", "expires_at"),
    ("checkpoints", "PK", "SK", "ttl"),
)
