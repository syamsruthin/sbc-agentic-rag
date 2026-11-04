"""Spikes 2 and 3 (MIGRATION_PLAN.md section 13): an SSE endpoint behind the Lambda Web Adapter, with the real
runtime stack loaded at import so the container's init time and size are representative.

- ``GET /api/healthz``: reports how long the module took to wire the stack (``init_ms``).
- ``POST /api/spike/chat``: streams ``thread``, five ``status`` events one second apart (with a keepalive
  comment), then ``answer`` and ``done``. The answer says whether ``x-amz-content-sha256`` matched the body,
  which is what CloudFront's origin access control for Lambda URLs requires on POSTs.

Development spike only: not part of the application packages.
"""

from __future__ import annotations

import time

_T0 = time.perf_counter()

import hashlib  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
from collections.abc import Iterator  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any  # noqa: E402

import boto3  # noqa: E402
from fastapi import FastAPI, Request  # noqa: E402
from fastapi.responses import StreamingResponse  # noqa: E402
from langgraph_checkpoint_aws import DynamoDBSaver  # noqa: E402

from sbc_agent.graph import Agent  # noqa: E402
from sbc_agent.respond import Deps  # noqa: E402
from sbc_core.config import load_config  # noqa: E402
from sbc_core.facts import SqliteFactsReader  # noqa: E402
from sbc_core.taxonomy import load_taxonomy  # noqa: E402

_T_IMPORTS = time.perf_counter()


class _NoChat:
    """The spike never calls a model; the agent only needs something with a ``chat`` method."""

    def chat(self, model: str, messages: Any, **kwargs: Any) -> Any:
        raise RuntimeError("the spike makes no model calls")


def _wire() -> dict[str, Any]:
    bundle = Path(os.environ.get("FACTS_BUNDLE_PATH", "/opt/bundle/facts.sqlite"))
    reader = SqliteFactsReader(bundle)
    session = boto3.Session(region_name=os.environ.get("AWS_REGION", "us-east-1"))
    taxonomy = load_taxonomy()
    agents = {}
    for bot in ("broker", "public"):
        config = load_config(bot, "local", Path(os.environ.get("SBC_CONFIG_DIR", "config")))
        saver = DynamoDBSaver(table_name=os.environ.get("DDB_TABLE_CHECKPOINTS", "unused"), session=session)
        deps = Deps(reader=reader, chat=_NoChat(), config=config, taxonomy=taxonomy, plan_year=2027)
        agents[bot] = Agent(deps, saver)
    return {"bundle_version": reader.bundle_version, "agents": agents}


STACK = _wire()
INIT = {
    "imports_ms": round((_T_IMPORTS - _T0) * 1000),
    "wire_ms": round((time.perf_counter() - _T_IMPORTS) * 1000),
}
app = FastAPI()


@app.get("/api/healthz")
def healthz() -> dict[str, Any]:
    return {"ok": True, "bundle_version": STACK["bundle_version"], "init": INIT}


def _event(name: str, data: dict[str, Any]) -> str:
    return f"event: {name}\ndata: {json.dumps(data)}\n\n"


@app.post("/api/spike/chat")
async def chat(request: Request) -> StreamingResponse:
    body = await request.body()
    sent = request.headers.get("x-amz-content-sha256", "")
    sha_ok = sent == hashlib.sha256(body).hexdigest()

    def stream() -> Iterator[str]:
        started = time.perf_counter()
        yield _event("thread", {"thread_id": "spike", "t_ms": 0})
        for i, stage in enumerate(("classify", "resolve", "answer", "verify", "render"), 1):
            time.sleep(1.0)
            if i == 3:
                yield ": keepalive\n\n"
            yield _event("status", {"stage": stage, "t_ms": round((time.perf_counter() - started) * 1000)})
        yield _event(
            "answer", {"markdown": "Spike answer.", "sha256_header_ok": sha_ok, "body_bytes": len(body)}
        )
        yield _event("done", {})

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})
