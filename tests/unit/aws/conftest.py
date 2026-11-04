"""Fixtures for the AWS adapter tests: everything runs against moto's in-memory AWS, never a real account."""

from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING

import boto3
import pytest
from moto import mock_aws

from sbc_aws.ddb import Tables
from sbc_aws.schema import create_tables

if TYPE_CHECKING:
    from mypy_boto3_dynamodb.client import DynamoDBClient
    from mypy_boto3_s3.client import S3Client
    from mypy_boto3_ssm.client import SSMClient

REGION = "us-east-1"
TABLES = Tables(app="test-app", audit="test-audit", checkpoints="test-checkpoints")
BUCKET = "test-data-bucket"


class FakeClock:
    """A settable clock (seconds since the epoch)."""

    def __init__(self, start: float = 1_793_750_400.0) -> None:  # 2026-11-04T00:00:00Z
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture(autouse=True)
def _fake_aws(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    # Never the shell's credentials or profile: moto intercepts every call made inside this fixture.
    for name in ("AWS_PROFILE", "AWS_SESSION_TOKEN", "AWS_SECURITY_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)
    with mock_aws():
        yield


@pytest.fixture
def session() -> boto3.Session:
    return boto3.Session(region_name=REGION)


@pytest.fixture
def ddb(session: boto3.Session) -> DynamoDBClient:
    client = session.client("dynamodb")
    create_tables(client, TABLES)
    return client


@pytest.fixture
def s3(session: boto3.Session) -> S3Client:
    client = session.client("s3")
    client.create_bucket(Bucket=BUCKET)
    return client


@pytest.fixture
def ssm(session: boto3.Session) -> SSMClient:
    return session.client("ssm")


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()
