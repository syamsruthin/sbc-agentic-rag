"""The API end to end on the fixture bundle (WP4): health, SSE turns, clarification across instances, auth
refusal outside local runs, validation and the plan picker's scope rules."""

from __future__ import annotations

import time
from collections.abc import Iterator

from fastapi.testclient import TestClient

from sbc_agent.graph import Progress, Reply
from sbc_api.app import create_app
from sbc_api.auth.principal import RefuseAll
from sbc_api.deps import build_state
from sbc_api.sse import stream_turn
from tests.unit.api.conftest import REPO, Harness, parse_sse, post_sse
from tests.unit.test_graph import ScriptedChat, classified, reply
from tests.unit.test_tools import GOLD, SILVER

SILVER_PROXY = "/api/sbc/2027%3A100000000001.pdf"


def _lookup(plan: str = "Example Gold") -> ScriptedChat:
    return ScriptedChat(
        classified(intent="lookup", plan_mentions=[plan], question_ids=["overall_deductible"]),
        reply("The overall deductible on {{P1.name}} is {{F1}}."),
    )


# ------------------------------------------------------------------ health


def test_health_reports_the_bundle_and_echoes_the_request_id(harness: Harness) -> None:
    client = harness.client()
    r = client.get("/api/healthz", headers={"x-amzn-request-id": "lambda-req-1"})
    assert r.status_code == 200
    assert r.json() == {"ok": True, "bundle_version": "test.1"}
    assert r.headers["x-request-id"] == "lambda-req-1"
    deep = client.get("/api/healthz?deep=1").json()["deep"]
    assert deep["env"] == "local"
    assert deep["search"] == "bm25"  # the fixture bundle has no embeddings
    assert deep["stores"] == "memory"
    assert deep["bots"] == {"broker": "ready", "public": "ready"}
    assert deep["plan_source"] == {"rows": 2, "preview_only": 1, "missing": 0}


def test_a_bot_without_its_key_is_unavailable_locally(harness: Harness) -> None:
    state = build_state(harness.env, config_dir=REPO / "config", stores=harness.stores)
    client = TestClient(create_app(state))
    assert client.get("/api/healthz?deep=1").json()["deep"]["bots"]["broker"].startswith("unavailable")
    r = client.post("/api/broker/chat", json={"message": "hi"})
    assert r.status_code == 503


# ------------------------------------------------------------------ chat


def test_a_lookup_streams_status_then_the_verified_answer(harness: Harness) -> None:
    events = post_sse(
        harness.client(broker=_lookup()), "/api/broker/chat", {"message": "Deductible on Gold?"}
    )
    names = [e for e, _ in events]
    assert names[0] == "thread"
    assert names[-2:] == ["answer", "done"]
    assert set(names[1:-2]) == {"status"}
    assert [d["stage"] for e, d in events if e == "status"] == ["classify", "resolve", "answer"]
    thread = events[0][1]
    assert len(thread["thread_id"]) >= 8
    assert thread["request_id"]
    answer = events[-2][1]
    assert answer["kind"] == "answer"
    assert answer["plan_keys"] == [GOLD]
    assert "The overall deductible on Example Gold is $" in answer["markdown"]
    assert answer["citations"]
    # nothing but the rendered answer carries text the model wrote
    assert all("deductible on Example Gold" not in str(d) for e, d in events if e != "answer")


def test_a_clarification_resumes_on_another_instance(harness: Harness) -> None:
    first = harness.client(
        broker=ScriptedChat(classified(intent="lookup", question_ids=["overall_deductible"]))
    )
    asked = post_sse(first, "/api/broker/chat", {"message": "What's the deductible on ZZ01?"})
    assert [e for e, _ in asked][-2:] == ["clarify", "done"]
    clarify = asked[-2][1]
    assert {o["plan_key"] for o in clarify["options"]} == {SILVER, GOLD}
    thread_id = asked[0][1]["thread_id"]

    # A second app instance (another Lambda), sharing only the checkpointer, with only compose scripted.
    second = harness.client(broker=ScriptedChat(reply("The overall deductible on {{P1.name}} is {{F1}}.")))
    out = post_sse(second, "/api/broker/chat/choose", {"thread_id": thread_id, "plan_key": GOLD})
    assert [e for e, _ in out][-2:] == ["answer", "done"]
    assert out[-2][1]["plan_keys"] == [GOLD]
    assert len(harness.chats[next(iter(harness.chats))].requests) == 1


def test_a_given_thread_id_is_kept(harness: Harness) -> None:
    events = post_sse(
        harness.client(broker=_lookup()),
        "/api/broker/chat",
        {"thread_id": "my-thread-0001", "message": "Gold?"},
    )
    assert events[0][1]["thread_id"] == "my-thread-0001"


def test_preview_only_plans_cite_the_pdf_proxy(harness: Harness) -> None:
    events = post_sse(
        harness.client(public=_lookup("Example Silver PPO 3000")),
        "/api/public/chat",
        {"message": "Deductible on Example Silver PPO 3000?", "user_state": "XX"},
    )
    answer = events[-2][1]
    assert answer["plan_keys"] == [SILVER]
    assert any(c["url"].startswith(SILVER_PROXY + "#page=") for c in answer["citations"])


def test_requests_are_validated(harness: Harness) -> None:
    client = harness.client()
    assert client.post("/api/broker/chat", json={"message": ""}).status_code == 422
    assert client.post("/api/broker/chat", json={"message": "x" * 4001}).status_code == 422
    assert client.post("/api/broker/chat", json={"message": "hi", "thread_id": "bad id!"}).status_code == 422
    assert client.post("/api/broker/chat", json={"message": "hi", "extra": 1}).status_code == 422
    both = {"thread_id": "t" * 10, "plan_key": GOLD, "text": "the gold one"}
    assert client.post("/api/broker/chat/choose", json=both).status_code == 422
    assert client.post("/api/broker/chat/choose", json={"thread_id": "t" * 10}).status_code == 422
    assert client.post("/api/nobody/chat", json={"message": "hi"}).status_code == 404


def test_outside_local_runs_nobody_is_authorized_until_wp5(harness: Harness) -> None:
    state = harness.state(broker=_lookup())
    state.authenticator = RefuseAll()
    client = TestClient(create_app(state))
    assert client.post("/api/broker/chat", json={"message": "hi"}).status_code == 401
    assert client.get("/api/public/plans/search?state=XX").status_code == 401
    assert client.get("/api/healthz").status_code == 200


# ------------------------------------------------------------------ plan picker


def test_plan_search_respects_the_bot_scope(harness: Harness) -> None:
    client = harness.client()
    broker = client.get("/api/broker/plans/search?q=Example").json()
    assert {p["plan_key"] for p in broker["plans"]} == {SILVER, GOLD}
    assert client.get("/api/public/plans/search?q=Example").status_code == 422  # public needs a state
    public = client.get("/api/public/plans/search?q=Gold&state=XX").json()
    assert [p["plan_key"] for p in public["plans"]] == [GOLD]
    assert client.get("/api/public/plans/search?state=XX&market=Large%20Group").status_code == 422
    assert client.get("/api/public/plans/search?state=YY").json()["plans"] == []


def test_a_single_plan_is_found_only_inside_the_scope(harness: Harness) -> None:
    client = harness.client()
    plan = client.get(f"/api/broker/plans/{GOLD}").json()
    assert (plan["plan_key"], plan["contract_code"]) == (GOLD, "ZZ01")
    assert client.get("/api/broker/plans/2027:999999999999").status_code == 404
    assert client.get(f"/api/broker/plans/{SILVER}").json()["source_url"] == SILVER_PROXY


# ------------------------------------------------------------------ the SSE relay itself


def test_failures_become_a_generic_error_event() -> None:
    def run() -> Iterator[Progress | Reply]:
        yield Progress("ingress")
        raise RuntimeError("boom: a secret detail")

    events = parse_sse("".join(stream_turn(run, thread_id="t1", request_id="r1")))
    assert [e for e, _ in events] == ["thread", "status", "error", "done"]
    assert events[2][1] == {"code": "internal", "message": "Something went wrong. Please try again."}


def test_slow_turns_get_keepalives_and_then_time_out() -> None:
    def run() -> Iterator[Progress | Reply]:
        time.sleep(0.35)
        yield Progress("ingress")

    out = "".join(
        stream_turn(run, thread_id="t1", request_id="r1", keepalive_seconds=0.1, timeout_seconds=0.25)
    )
    events = parse_sse(out)
    assert (":", {}) in events
    assert [e for e, _ in events if e != ":"] == ["thread", "error", "done"]
    assert events[-2][1]["code"] == "timeout"
