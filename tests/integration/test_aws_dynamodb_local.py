"""Rate-limit atomicity against DynamoDB Local (moto doesn't model concurrent conditional writes).

Start DynamoDB Local, then run with its endpoint:

    docker run -d -p 8000:8000 amazon/dynamodb-local:2.6.1 -jar DynamoDBLocal.jar -inMemory -sharedDb
    DDB_LOCAL_ENDPOINT=http://localhost:8000 uv run pytest -m integration tests/integration
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

import boto3
import pytest

from sbc_aws.ddb import Tables
from sbc_aws.ratelimit import DynamoRateLimiter, GlobalCap, Window
from sbc_aws.schema import create_tables

if TYPE_CHECKING:
    from mypy_boto3_dynamodb.client import DynamoDBClient

Local = tuple["DynamoDBClient", Tables]

pytestmark = pytest.mark.integration
ENDPOINT = os.environ.get("DDB_LOCAL_ENDPOINT")


@pytest.fixture
def client() -> Iterator[Local]:
    if not ENDPOINT:
        pytest.skip("set DDB_LOCAL_ENDPOINT to run against DynamoDB Local")
    # Fixed local credentials: never the shell's AWS credentials.
    session = boto3.Session(region_name="us-east-1", aws_access_key_id="local", aws_secret_access_key="local")  # noqa: S106
    ddb = session.client("dynamodb", endpoint_url=ENDPOINT)
    suffix = uuid.uuid4().hex[:8]
    tables = Tables(app=f"it-app-{suffix}", audit=f"it-audit-{suffix}", checkpoints=f"it-cp-{suffix}")
    create_tables(ddb, tables)
    yield ddb, tables
    for name in (tables.app, tables.audit, tables.checkpoints):
        ddb.delete_table(TableName=name)


def test_concurrent_hits_never_pass_the_limit(client: Local) -> None:
    ddb, tables = client
    limiter = DynamoRateLimiter(ddb, tables.app)
    windows = [Window("day", 86400, 25)]
    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(lambda _: limiter.hit("burst", windows).allowed, range(100)))
    assert sum(results) == 25


def test_concurrent_turns_never_pass_the_global_cap(client: Local) -> None:
    ddb, tables = client
    cap = GlobalCap(ddb, tables.app)
    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(lambda _: cap.consume(40), range(120)))
    assert sum(results) == 40
    assert cap.used_today() == 40
