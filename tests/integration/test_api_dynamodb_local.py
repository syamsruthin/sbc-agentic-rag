"""The API wired to DynamoDB (stores and checkpointer) against DynamoDB Local: a clarifying question asked
through one app instance is answered through another, sharing nothing but the tables.

Start DynamoDB Local, then run with its endpoint:

    docker run -d -p 8000:8000 amazon/dynamodb-local:2.6.1 -jar DynamoDBLocal.jar -inMemory -sharedDb
    DDB_LOCAL_ENDPOINT=http://localhost:8000 uv run pytest -m integration tests/integration
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import boto3
import pytest
from fastapi.testclient import TestClient

from sbc_api.app import create_app
from sbc_api.deps import build_state
from sbc_aws.ddb import Tables
from sbc_aws.schema import create_tables
from sbc_core.config import EnvSettings
from tests.unit.api.conftest import REPO, post_sse
from tests.unit.test_graph import ScriptedChat, classified, reply
from tests.unit.test_tools import GOLD

pytestmark = pytest.mark.integration
ENDPOINT = os.environ.get("DDB_LOCAL_ENDPOINT")


def test_clarification_resumes_across_instances_on_dynamodb(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if not ENDPOINT:
        pytest.skip("set DDB_LOCAL_ENDPOINT to run against DynamoDB Local")
    from tests.unit.test_bundle import make_bundle

    # Every boto3 client in the app goes to DynamoDB Local, with fixed local credentials.
    monkeypatch.setenv("AWS_ENDPOINT_URL_DYNAMODB", ENDPOINT)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "local")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "local")
    monkeypatch.delenv("AWS_SESSION_TOKEN", raising=False)
    suffix = uuid.uuid4().hex[:8]
    tables = Tables(app=f"api-app-{suffix}", audit=f"api-audit-{suffix}", checkpoints=f"api-cp-{suffix}")
    ddb = boto3.Session(region_name="us-east-1").client("dynamodb")
    create_tables(ddb, tables)
    env = EnvSettings.model_validate({
        "app_env": "local", "facts_bundle_path": str(make_bundle(tmp_path)), "ddb_table_app": tables.app,
        "ddb_table_audit": tables.audit, "ddb_table_checkpoints": tables.checkpoints,
    })  # fmt: skip

    def client(chat: ScriptedChat) -> TestClient:
        state = build_state(env, config_dir=REPO / "config", chat_factory=lambda bot, s: chat)
        assert state.stores.backend == "dynamodb"
        return TestClient(create_app(state))

    try:
        asked = post_sse(
            client(ScriptedChat(classified(intent="lookup", question_ids=["overall_deductible"]))),
            "/api/broker/chat", {"message": "What's the deductible on ZZ01?"},
        )  # fmt: skip
        assert asked[-2][0] == "clarify"
        out = post_sse(
            client(ScriptedChat(reply("The overall deductible on {{P1.name}} is {{F1}}."))),
            "/api/broker/chat/choose", {"thread_id": asked[0][1]["thread_id"], "plan_key": GOLD},
        )  # fmt: skip
        assert out[-2][0] == "answer"
        assert out[-2][1]["plan_keys"] == [GOLD]
        assert ddb.scan(TableName=tables.checkpoints, Select="COUNT")["Count"] > 0
    finally:
        for name in (tables.app, tables.audit, tables.checkpoints):
            ddb.delete_table(TableName=name)
