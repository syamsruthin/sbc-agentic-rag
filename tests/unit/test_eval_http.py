"""evals/harness/http_api.py: golden items through the API over HTTP, scored like the in-process eval."""

from __future__ import annotations

import json
from typing import Any

import httpx

from evals.harness.generation import ItemResult, build_report
from evals.harness.http_api import (
    HttpAgent,
    reply_from_events,
    sse_events,
    unscored_in_report,
    unscored_over_http,
)


def _sse(*events: tuple[str, dict[str, Any]]) -> str:
    return "".join(f"event: {n}\ndata: {json.dumps(d)}\n\n" for n, d in events) + ": keepalive\n\n"


ANSWER = {
    "kind": "answer", "markdown": "The deductible is $500.", "plan_keys": ["2027:1"],
    "citations": [{"number": 1}], "details": {"path": "fast", "llm_calls": 3, "cost_usd": 0.0085,
                                              "tools": ["get_plan_facts"]},
}  # fmt: skip


def test_an_answer_becomes_a_reply_with_its_details_as_the_trace() -> None:
    events = list(
        sse_events(
            _sse(("thread", {"thread_id": "t1", "request_id": "r1"}), ("answer", ANSWER), ("done", {}))
        )
    )
    reply, thread = reply_from_events(events)
    assert thread == "t1"
    assert (reply.kind, reply.markdown, reply.plan_keys) == ("answer", "The deductible is $500.", ("2027:1",))
    assert reply.trace["path"] == "fast"
    assert [t["name"] for t in reply.trace["tools"]] == ["get_plan_facts"]
    assert len(reply.trace["usage"]) == 3
    assert sum(u["cost_usd"] for u in reply.trace["usage"]) == 0.0085


def test_a_clarifying_question_keeps_its_options() -> None:
    options = [{"label": "BTCU8", "plan_key": "2027:1"}, {"label": "GG4XQ", "plan_key": "2027:2"}]
    clarify = {"question": "Which plan?", "open_plan_picker": False, "options": options}
    reply, _ = reply_from_events([("thread", {"thread_id": "t"}), ("clarify", clarify), ("done", {})])
    assert reply.kind == "clarify"
    assert reply.clarification is not None
    assert [o.plan_key for o in reply.clarification.options] == ["2027:1", "2027:2"]


def test_turns_share_the_servers_thread_and_wait_out_rate_limits() -> None:
    sent: list[dict[str, Any]] = []
    limited = {"n": 1}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-sbc-authorization"] == "Bearer tok"
        sent.append(json.loads(request.content))
        if limited["n"]:
            limited["n"] -= 1
            return httpx.Response(200, text=_sse(("limit", {"retry_after_s": 5}), ("done", {})))
        return httpx.Response(
            200, text=_sse(("thread", {"thread_id": "srv-1"}), ("answer", ANSWER), ("done", {}))
        )

    slept: list[float] = []
    client = httpx.Client(base_url="http://api", transport=httpx.MockTransport(handler))
    agent = HttpAgent(client, "broker", lambda _state: "tok", slept.append)
    agent.ask("gs-1-ab", "Deductible?", ui_selected=("2027:1",))
    agent.ask("gs-1-ab", "And the out-of-pocket limit?")
    assert slept == [6.0]
    assert sent[0] == {"message": "Deductible?", "ui_selected": ["2027:1"]}
    assert sent[-1]["thread_id"] == "srv-1"


def test_checks_that_need_the_trace_are_not_scored_over_http() -> None:
    checks: dict[str, bool | None] = {
        "behavior": True,
        "no_ungrounded_numbers": True,
        "tool_selection": False,
    }
    result = ItemResult("gs-1", "single_field_lookup", "broker", 1, True, "answer", "answer", checks=checks)
    (out,) = unscored_over_http([result])
    assert out.checks == {"behavior": True, "no_ungrounded_numbers": None, "tool_selection": None}


def test_the_ungrounded_number_gate_is_not_reported_as_a_pass() -> None:
    result = ItemResult(
        "gs-1", "single_field_lookup", "broker", 1, True, "answer", "answer", checks={"behavior": True}
    )
    report = unscored_in_report(build_report(unscored_over_http([result]), "2027.2", "0.2.0"))
    gate = next(g for g in report["gates"] if "ungrounded" in g["gate"])
    assert (gate["value"], gate["passed"]) == (None, None)
    assert all(g.get("ungrounded_numbers") is None for g in report["groups"].values() if isinstance(g, dict))
