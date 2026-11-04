"""The DynamoDB checkpointer: a clarifying question resumes on another saver instance (spike 1, in moto)."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import boto3
import pytest

from sbc_agent.graph import Agent
from sbc_agent.respond import Deps
from sbc_aws.checkpoint import checkpoint_ttl_seconds, make_checkpointer
from sbc_core.config import load_config
from sbc_core.facts import SqliteFactsReader
from sbc_core.taxonomy import load_taxonomy
from tests.unit.aws.conftest import BUCKET, TABLES
from tests.unit.test_graph import ScriptedChat, classified, reply
from tests.unit.test_tools import GOLD, SILVER

if TYPE_CHECKING:
    from mypy_boto3_dynamodb.client import DynamoDBClient
    from mypy_boto3_s3.client import S3Client

REPO = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def bundle(tmp_path_factory: pytest.TempPathFactory) -> Path:
    from tests.unit.test_bundle import make_bundle

    return make_bundle(tmp_path_factory.mktemp("bundle"))


def _agent(bundle: Path, session: boto3.Session, chat: ScriptedChat) -> Agent:
    config = load_config("broker", "local", REPO / "config")
    saver = make_checkpointer(TABLES.checkpoints, session=session, ttl_seconds=3600, s3_bucket=BUCKET)
    deps = Deps(
        reader=SqliteFactsReader(bundle), chat=chat, config=config, taxonomy=load_taxonomy(), plan_year=2027
    )
    return Agent(deps, saver)


def test_a_clarification_resumes_on_another_instance(
    bundle: Path, session: boto3.Session, ddb: DynamoDBClient, s3: S3Client
) -> None:
    first = _agent(
        bundle, session, ScriptedChat(classified(intent="lookup", question_ids=["overall_deductible"]))
    )
    asked = first.ask("thread-1", "What's the deductible on ZZ01?")
    assert asked.kind == "clarify"
    assert asked.clarification is not None
    assert {o.plan_key for o in asked.clarification.options} == {SILVER, GOLD}

    # A second instance with its own saver and only the compose response scripted: it can finish only if the
    # classification and the pending interrupt come back from DynamoDB.
    chat = ScriptedChat(reply("The overall deductible on {{P1.name}} is {{F1}}."))
    out = _agent(bundle, session, chat).choose("thread-1", plan_key=GOLD)
    assert out.kind == "answer"
    assert out.plan_keys == (GOLD,)
    assert "The overall deductible on Example Gold is" in out.markdown
    assert len(chat.requests) == 1

    items = ddb.scan(TableName=TABLES.checkpoints)["Items"]
    assert items
    assert all("ttl" in item for item in items)


def test_threads_are_isolated(
    bundle: Path, session: boto3.Session, ddb: DynamoDBClient, s3: S3Client
) -> None:
    _agent(
        bundle, session, ScriptedChat(classified(intent="lookup", question_ids=["overall_deductible"]))
    ).ask("thread-a", "What's the deductible on ZZ01?")
    chat = ScriptedChat(
        classified(intent="lookup", plan_mentions=["Example Gold"], question_ids=["overall_deductible"]),
        reply("The overall deductible on {{P1.name}} is {{F1}}."),
    )
    out = _agent(bundle, session, chat).ask("thread-b", "What's the deductible on Example Gold?")
    assert out.kind == "answer"  # thread-a's pending question doesn't leak into thread-b


def test_ttl_follows_the_bot_or_the_demo_history(tmp_path: Path) -> None:
    broker = load_config("broker", "dev", REPO / "config")
    public = load_config("public", "dev", REPO / "config")
    assert checkpoint_ttl_seconds(broker) == 365 * 86400
    assert checkpoint_ttl_seconds(public) == 30 * 86400
    assert checkpoint_ttl_seconds(broker, demo=True) == 24 * 3600


def test_ttl_must_be_positive(session: boto3.Session) -> None:
    with pytest.raises(ValueError, match="ttl_seconds"):
        make_checkpointer(TABLES.checkpoints, session=session, ttl_seconds=0)
