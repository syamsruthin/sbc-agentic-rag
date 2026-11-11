"""WP12: optional Langfuse tracing, one trace per turn (``sbc_api.tracing``). Spans go to an in-memory
OpenTelemetry exporter; nothing is sent over the network."""

from __future__ import annotations

import json
from typing import Any

from fastapi.testclient import TestClient
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from sbc_agent.graph import Reply
from sbc_api.app import create_app
from sbc_api.tracing import LangfuseTracer, NoTracing, TracedTurn, build_tracer, mask
from sbc_core.config import EnvSettings
from tests.unit.api.conftest import Harness, post_sse
from tests.unit.api.test_guards import QUESTION, lookups


def env(**keys: str) -> EnvSettings:
    return EnvSettings.model_validate({"app_env": "local", "facts_bundle_path": "unused", **keys})


def turn(reply: Reply, question: str = "Deductible on Gold?", **extra: Any) -> TracedTurn:
    return TracedTurn(
        request_id="req-12345678", thread_id="thread-1", bot="broker", actor_hash="a" * 32,
        question=question, reply=reply, bundle_version="2027.1", config_hash="cfg1", **extra,
    )  # fmt: skip


ANSWER = Reply(
    "answer",
    "The deductible is $500 [1]. Questions? Call 1-800-555-0100.",
    plan_keys=("2027:AB12",),
    trace={
        "path": "fast",
        "classification": {"intent": "lookup", "plan_mentions": ["Gold"]},
        "classify_usage": {"kind": "chat", "model": "small", "prompt_tokens": 10, "completion_tokens": 5,
                           "total_tokens": 15, "cost_usd": 0.0001, "latency_ms": 300},
        "usage": [{"kind": "chat", "model": "big", "prompt_tokens": 100, "completion_tokens": 50,
                   "total_tokens": 150, "cost_usd": None, "latency_ms": 900}],
        "tools": [{"name": "get_plan_facts", "ok": True, "args": {"query": "a private query"}}],
        "prompt_versions": {"answer": "v3"},
    },
)  # fmt: skip


def spans(exporter: InMemorySpanExporter) -> dict[str, dict[str, Any]]:
    return {s.name: dict(s.attributes or {}) for s in exporter.get_finished_spans()}


def test_tracing_is_off_without_both_keys() -> None:
    assert isinstance(build_tracer(env()), NoTracing)
    assert isinstance(build_tracer(env(langfuse_public_key="pk-lf-x")), NoTracing)
    both = env(langfuse_public_key="pk-lf-x", langfuse_secret_key="sk-lf-x")  # noqa: S106 - a test value
    assert isinstance(build_tracer(both), LangfuseTracer)


def test_the_mask_redacts_every_string() -> None:
    data = {"q": "mail jane@example.com", "list": ["555-123-4567", 3], "n": None}
    assert mask(data=data) == {"q": "mail [EMAIL]", "list": ["[PHONE]", 3], "n": None}


def test_a_turn_becomes_one_trace_with_its_model_calls_and_tools() -> None:
    exporter = InMemorySpanExporter()
    keys = {"langfuse_public_key": "pk-lf-turn", "langfuse_secret_key": "sk-lf-turn"}
    tracer = LangfuseTracer.from_env(env(**keys), span_exporter=exporter)
    tracer.record(turn(ANSWER, question="Is jane@example.com covered?"))
    tracer.flush()

    out = spans(exporter)
    root = out["turn"]
    assert root["session.id"] == "thread-1"
    assert root["user.id"] == "a" * 32
    assert root["langfuse.trace.name"] == "broker turn"
    assert tuple(root["langfuse.trace.tags"]) == ("broker", "answer")
    assert json.loads(root["langfuse.trace.metadata.bundle_version"]) == "2027.1"
    assert json.loads(root["langfuse.trace.metadata.request_id"]) == "req-12345678"
    # The mask runs over input and output: no email, and the SBC's phone number is masked in the trace only.
    assert root["langfuse.observation.input"] == "Is [EMAIL] covered?"
    assert "[PHONE]" in root["langfuse.observation.output"]
    assert json.loads(root["langfuse.observation.metadata.path"]) == "fast"
    assert json.loads(root["langfuse.observation.metadata.intent"]) == "lookup"

    calls = [s for s in exporter.get_finished_spans() if s.name == "chat"]
    models = sorted(str((s.attributes or {})["langfuse.observation.model.name"]) for s in calls)
    assert models == ["big", "small"]
    small = next(dict(s.attributes or {}) for s in calls
                 if (s.attributes or {})["langfuse.observation.model.name"] == "small")  # fmt: skip
    assert json.loads(str(small["langfuse.observation.usage_details"])) == {
        "input": 10,
        "output": 5,
        "total": 15,
    }
    assert json.loads(str(small["langfuse.observation.cost_details"])) == {"total": 0.0001}

    tool = out["get_plan_facts"]
    assert tool["langfuse.observation.type"] == "tool"
    assert "a private query" not in json.dumps(tool)  # tool arguments are never exported


class Recorder:
    name = "recorder"

    def __init__(self) -> None:
        self.turns: list[TracedTurn] = []
        self.flushes = 0

    def record(self, turn: TracedTurn) -> None:
        self.turns.append(turn)

    def flush(self) -> None:
        self.flushes += 1


def client_with(harness: Harness, recorder: Recorder, **chats: Any) -> TestClient:
    state = harness.state(**chats)
    state.tracer = recorder
    return TestClient(create_app(state))


def test_each_turn_is_traced_redacted_and_flushed_after_the_response(harness: Harness) -> None:
    recorder = Recorder()
    client = client_with(harness, recorder, broker=lookups(1))
    events = post_sse(client, "/api/broker/chat", {"message": QUESTION + " Email me at jane@example.com"})

    assert [t.reply.kind for t in recorder.turns] == ["answer"]
    traced = recorder.turns[0]
    assert traced.thread_id == events[0][1]["thread_id"]
    assert traced.request_id == events[0][1]["request_id"]
    assert "jane@example.com" not in traced.question
    assert traced.actor_hash != "local-broker"
    assert len(traced.actor_hash) == 32
    assert not traced.cached
    assert recorder.flushes == 1


def test_cached_answers_are_traced_as_cached(harness: Harness) -> None:
    recorder = Recorder()
    post_sse(client_with(harness, recorder, broker=lookups(1)), "/api/broker/chat", {"message": QUESTION})
    post_sse(client_with(harness, recorder), "/api/broker/chat", {"message": QUESTION})
    assert [t.cached for t in recorder.turns] == [False, True]
    assert recorder.flushes == 2


def test_a_tracing_failure_never_reaches_the_user(harness: Harness) -> None:
    class Broken(Recorder):
        def record(self, turn: TracedTurn) -> None:
            raise RuntimeError("langfuse down")

    tracer = LangfuseTracer(client=None)  # type: ignore[arg-type]
    tracer.record(turn(ANSWER))  # logged, not raised
    tracer.flush()

    # The API calls the tracer inside the turn: a tracer that raises anyway still leaves a clean stream.
    client = client_with(harness, Broken(), broker=lookups(1))
    names = [e for e, _ in post_sse(client, "/api/broker/chat", {"message": QUESTION})]
    assert names[-1] == "done"


def test_deep_health_says_whether_tracing_is_on(harness: Harness) -> None:
    body = client_with(harness, Recorder()).get("/api/healthz?deep=1").json()
    assert body["deep"]["tracing"] == "recorder"
    assert harness.client().get("/api/healthz?deep=1").json()["deep"]["tracing"] == "off"
