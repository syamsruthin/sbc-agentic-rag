"""Spike 1 (MIGRATION_PLAN.md section 13): LangGraph interrupt/resume through DynamoDBSaver across processes.

A clarifying question is asked in one process and answered in a second process that shares nothing with the
first except the DynamoDB table, which is what happens when two Lambda instances serve consecutive requests.
The second process gets no classify response in its script, so it can only finish if the classification and
the pending interrupt came back from DynamoDB.

    docker run -d -p 8000:8000 amazon/dynamodb-local:2.6.1 -jar DynamoDBLocal.jar -inMemory -sharedDb
    uv run --with langgraph-checkpoint-aws==1.2.6 python -m spikes.aws.checkpoint_resume run

Development spike only: not imported by the application and not part of the test suite.
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import boto3

REPO = Path(__file__).resolve().parents[2]
TABLE = "sbc-spike-checkpoints"
ENDPOINT = "http://localhost:8000"
REGION = "us-east-1"
TTL_SECONDS = 30 * 24 * 3600
CREDS = {"aws_access_key_id": "local", "aws_secret_access_key": "local"}  # DynamoDB Local accepts anything


def _client() -> Any:
    return boto3.client("dynamodb", endpoint_url=ENDPOINT, region_name=REGION, **CREDS)


def setup() -> None:
    """Create the table the way the CDK stack will: PK/SK strings, on-demand, TTL on ``ttl``."""
    ddb = _client()
    if TABLE in ddb.list_tables()["TableNames"]:
        ddb.delete_table(TableName=TABLE)
        ddb.get_waiter("table_not_exists").wait(TableName=TABLE)
    ddb.create_table(
        TableName=TABLE,
        AttributeDefinitions=[
            {"AttributeName": "PK", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"},
        ],
        KeySchema=[{"AttributeName": "PK", "KeyType": "HASH"}, {"AttributeName": "SK", "KeyType": "RANGE"}],
        BillingMode="PAY_PER_REQUEST",
    )
    ddb.get_waiter("table_exists").wait(TableName=TABLE)
    ddb.update_time_to_live(
        TableName=TABLE, TimeToLiveSpecification={"Enabled": True, "AttributeName": "ttl"}
    )


def _agent(bundle: Path, script: list[Any]) -> tuple[Any, Any]:
    from langgraph_checkpoint_aws import DynamoDBSaver

    from sbc_agent.graph import Agent
    from sbc_agent.respond import Deps
    from sbc_core.config import load_config
    from sbc_core.facts import SqliteFactsReader
    from sbc_core.taxonomy import load_taxonomy
    from tests.unit.test_graph import ScriptedChat

    session = boto3.Session(region_name=REGION, **CREDS)  # never the shell's AWS credentials
    saver = DynamoDBSaver(table_name=TABLE, session=session, endpoint_url=ENDPOINT, ttl_seconds=TTL_SECONDS)
    chat = ScriptedChat(*script)
    config = load_config("broker", "local", REPO / "config")
    reader = SqliteFactsReader(bundle)
    deps = Deps(reader=reader, chat=chat, config=config, taxonomy=load_taxonomy(), plan_year=2027)
    return Agent(deps, saver), chat


def ask(bundle: Path, thread: str) -> dict[str, Any]:
    from tests.unit.test_graph import classified

    agent, chat = _agent(bundle, [classified(intent="lookup", question_ids=["overall_deductible"])])
    started = time.perf_counter()
    out = agent.ask(thread, "What's the deductible on ZZ01?")
    options = [o.plan_key for o in out.clarification.options] if out.clarification else []
    return {"kind": out.kind, "options": options, "model_calls": len(chat.requests),
            "ms": round((time.perf_counter() - started) * 1000)}  # fmt: skip


def choose(bundle: Path, thread: str, plan_key: str) -> dict[str, Any]:
    from tests.unit.test_graph import reply

    agent, chat = _agent(bundle, [reply("The overall deductible on {{P1.name}} is {{F1}}.")])
    started = time.perf_counter()
    out = agent.choose(thread, plan_key=plan_key)
    ms = round((time.perf_counter() - started) * 1000)
    return {"kind": out.kind, "plan_keys": list(out.plan_keys), "model_calls": len(chat.requests),
            "markdown_head": out.markdown[:160], "ms": ms}  # fmt: skip


def _size(value: dict[str, Any]) -> int:
    """Approximate DynamoDB size of one attribute value (strings and binaries by length, numbers 21 bytes)."""
    ((kind, v),) = value.items()
    if kind in ("S", "B"):
        return len(v.encode() if isinstance(v, str) else v)
    if kind == "N":
        return 21
    if kind == "M":
        return 3 + sum(len(k) + _size(x) for k, x in v.items())
    if kind == "L":
        return 3 + sum(_size(x) for x in v)
    return 1


def items_report() -> dict[str, Any]:
    """Item count and sizes, to estimate write request units per turn (1 WRU per started KB)."""
    ddb = _client()
    items = ddb.scan(TableName=TABLE)["Items"]
    sizes = [sum(len(k) + _size(v) for k, v in i.items()) for i in items]
    return {
        "items": len(items),
        "max_item_bytes": max(sizes, default=0),
        "total_bytes": sum(sizes),
        "est_wru": sum(math.ceil(s / 1024) for s in sizes),
        "ttl_set_on_all": all("ttl" in i for i in items),
    }


def _child(*args: str) -> dict[str, Any]:
    cmd = [sys.executable, "-m", "spikes.aws.checkpoint_resume", *args]
    done = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True, check=False)  # noqa: S603
    if done.returncode:
        raise SystemExit(f"{args[0]} failed:\n{done.stderr[-4000:]}")
    return json.loads(done.stdout.strip().splitlines()[-1])


def run() -> None:
    from tests.unit.test_bundle import make_bundle
    from tests.unit.test_tools import GOLD

    setup()
    with tempfile.TemporaryDirectory() as tmp:
        bundle = make_bundle(Path(tmp))
        thread = "spike-thread-1"
        first = _child("ask", str(bundle), thread)
        after_ask = items_report()
        second = _child("choose", str(bundle), thread, GOLD)
        after_choose = items_report()
    result = {"ask_process": first, "items_after_ask": after_ask, "choose_process": second,
              "items_after_choose": after_choose}  # fmt: skip
    print(json.dumps(result, indent=2))
    ok = (
        first["kind"] == "clarify"
        and GOLD in first["options"]
        and second["kind"] == "answer"
        and second["plan_keys"] == [GOLD]
        and second["model_calls"] == 1
        and after_choose["ttl_set_on_all"]
    )
    print("SPIKE 1:", "PASS" if ok else "FAIL")
    raise SystemExit(0 if ok else 1)


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run")
    a = sub.add_parser("ask")
    a.add_argument("bundle", type=Path)
    a.add_argument("thread")
    c = sub.add_parser("choose")
    c.add_argument("bundle", type=Path)
    c.add_argument("thread")
    c.add_argument("plan_key")
    args = ap.parse_args()
    if args.cmd == "run":
        run()
    elif args.cmd == "ask":
        print(json.dumps(ask(args.bundle, args.thread)))
    else:
        print(json.dumps(choose(args.bundle, args.thread, args.plan_key)))


if __name__ == "__main__":
    main()
