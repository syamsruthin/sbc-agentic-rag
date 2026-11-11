"""WP6: the checks around a chat turn (rate limits, thread ownership, kill switch, exact-answer cache, global
cap, quarantine, audit), feedback, the thread list and the admin endpoints."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from sbc_api import guards
from sbc_api.app import create_app
from sbc_api.auth.principal import Principal
from sbc_aws.sessions import ThreadSummary
from sbc_core.config import BotName, Portfolio, RateLimits
from tests.unit.api.conftest import Harness, parse_sse, post_sse
from tests.unit.test_graph import ScriptedChat, classified, reply
from tests.unit.test_tools import GOLD

QUESTION = "What's the deductible on Example Gold?"


def lookups(n: int) -> ScriptedChat:
    steps: list[Any] = []
    for _ in range(n):
        steps += [
            classified(intent="lookup", plan_mentions=["Example Gold"], question_ids=["overall_deductible"]),
            reply("The overall deductible on {{P1.name}} is {{F1}}."),
        ]
    return ScriptedChat(*steps)


class Fixed:
    """An authenticator that returns one principal (the bot follows the route)."""

    def __init__(
        self, subject: str = "u1", *, demo: bool = False, groups: frozenset[str] = frozenset({"broker"})
    ):
        self.subject, self.demo, self.groups = subject, demo, groups

    def authenticate(self, request: Any, bot: BotName) -> Principal:
        return Principal(bot=bot, subject=self.subject, groups=self.groups, is_demo=self.demo, user_state="XX"
                         if bot is BotName.PUBLIC else None)  # fmt: skip


def app(harness: Harness, *, auth: Fixed | None = None, portfolio: Portfolio | None = None,
        limits: RateLimits | None = None, **chats: ScriptedChat) -> TestClient:  # fmt: skip
    state = harness.state(**chats)
    if auth is not None:
        state.authenticator = auth
    for runtime in state.bots.values():
        config = runtime.settings.config
        if portfolio is not None:
            object.__setattr__(config, "portfolio", portfolio)
        if limits is not None:
            object.__setattr__(config, "bot", config.bot.model_copy(update={"rate_limits": limits}))
    return TestClient(create_app(state))


def last(events: list[tuple[str, dict[str, Any]]]) -> tuple[str, dict[str, Any]]:
    return events[-2]


# ------------------------------------------------------------------ rate limits and the global cap


def test_a_caller_over_the_minute_limit_gets_a_limit_event(harness: Harness) -> None:
    client = app(harness, auth=Fixed(), limits=RateLimits(per_minute=2, per_day=100), broker=lookups(2))
    for _ in range(2):
        assert last(post_sse(client, "/api/broker/chat", {"message": QUESTION}))[0] in ("answer", "limit")
    name, body = last(post_sse(client, "/api/broker/chat", {"message": "Another question about Gold?"}))
    assert name == "limit"
    assert body["scope"] == "user"
    assert 1 <= body["retry_after_s"] <= 60


def test_limits_are_per_caller(harness: Harness) -> None:
    shared = RateLimits(per_minute=1, per_day=100)
    first = app(harness, auth=Fixed("a"), limits=shared, broker=lookups(1))
    assert last(post_sse(first, "/api/broker/chat", {"message": QUESTION}))[0] == "answer"
    assert last(post_sse(first, "/api/broker/chat", {"message": "More?"}))[0] == "limit"
    second = app(harness, auth=Fixed("b"), limits=shared, broker=lookups(1))
    assert last(post_sse(second, "/api/broker/chat", {"message": "A different question?"}))[0] == "answer"


def test_the_demo_account_has_its_own_tighter_limits(harness: Harness, caplog: Any) -> None:
    portfolio = Portfolio(demo_rate_limits=RateLimits(per_minute=1, per_day=5))
    client = app(harness, auth=Fixed("demo", demo=True), portfolio=portfolio, broker=lookups(1))
    assert last(post_sse(client, "/api/broker/chat", {"message": QUESTION}))[0] == "answer"
    with caplog.at_level("INFO", logger="sbc_api.guards"):
        assert last(post_sse(client, "/api/broker/chat", {"message": "Again?"}))[0] == "limit"
    # Its own event, for the DemoAccountLimited alarm (WP12); other callers log "rate_limited".
    events = [getattr(r, "event", None) for r in caplog.records if r.name == "sbc_api.guards"]
    assert events == ["demo_user_limit_reached"]


def test_the_public_bot_is_also_limited_per_ip(harness: Harness) -> None:
    limits = RateLimits(per_minute=100, per_day=100, per_ip_per_day=1)
    headers = {"cloudfront-viewer-address": "203.0.113.9:4444"}
    client = app(harness, auth=Fixed("s1"), limits=limits, public=lookups(1))
    events = parse_sse(client.post("/api/public/chat", json={"message": QUESTION}, headers=headers).text)
    assert last(events)[0] == "answer"
    other_session = app(harness, auth=Fixed("s2"), limits=limits, public=lookups(1))
    events = parse_sse(other_session.post("/api/public/chat", json={"message": "Q?"}, headers=headers).text)
    assert last(events) == ("limit", {"scope": "ip", "retry_after_s": last(events)[1]["retry_after_s"],
                                      "message": guards.TOO_MANY})  # fmt: skip


def test_the_global_daily_cap_counts_model_turns_across_bots(harness: Harness) -> None:
    portfolio = Portfolio(global_daily_llm_turns=2)
    client = app(harness, auth=Fixed(), portfolio=portfolio, broker=lookups(2), public=lookups(1))
    assert last(post_sse(client, "/api/broker/chat", {"message": QUESTION}))[0] == "answer"
    assert last(post_sse(client, "/api/public/chat", {"message": "Deductible on Gold?"}))[0] == "answer"
    name, body = last(post_sse(client, "/api/broker/chat", {"message": "Something new?"}))
    assert (name, body["scope"], body["message"]) == ("limit", "global", guards.GLOBAL_CAP)


# ------------------------------------------------------------------ the exact-answer cache


def test_a_repeated_first_question_is_served_from_the_cache_without_a_model_call(harness: Harness) -> None:
    portfolio = Portfolio(global_daily_llm_turns=1)
    first = app(harness, auth=Fixed("a"), portfolio=portfolio, broker=lookups(1))
    original = last(post_sse(first, "/api/broker/chat", {"message": QUESTION}))
    assert original[0] == "answer"
    # Another caller, another instance, and the cap already used: the cache still answers.
    chat = ScriptedChat()
    second = app(harness, auth=Fixed("b"), portfolio=portfolio, broker=chat)
    events = post_sse(second, "/api/broker/chat", {"message": "  what's the DEDUCTIBLE on example gold "})
    name, body = last(events)
    assert name == "answer"
    assert body["cached"] is True
    assert body["markdown"] == original[1]["markdown"]
    # The first turn's details (path, model calls, cost) aren't replayed: the cache hit made no model call.
    assert original[1]["details"]["llm_calls"] == 2
    assert "details" not in body
    assert chat.requests == []
    assert harness.stores.audit.records[-1].detail == {"cached": "true"}  # type: ignore[attr-defined]


def test_a_follow_up_after_a_cached_answer_keeps_the_context(harness: Harness) -> None:
    first = app(harness, auth=Fixed("a"), broker=lookups(1))
    post_sse(first, "/api/broker/chat", {"message": QUESTION})
    follow = ScriptedChat(
        classified(intent="follow_up", question_ids=["oop_limit"]),
        reply("The out-of-pocket limit on {{P1.name}} is {{F1}}."),
    )
    second = app(harness, auth=Fixed("b"), broker=follow)
    cached = post_sse(second, "/api/broker/chat", {"message": QUESTION})
    thread_id = cached[0][1]["thread_id"]
    out = post_sse(
        second, "/api/broker/chat", {"thread_id": thread_id, "message": "And the out-of-pocket limit?"}
    )
    assert last(out)[1]["plan_keys"] == [GOLD]
    assert "out-of-pocket limit on Example Gold" in last(out)[1]["markdown"]


def test_follow_ups_and_clarifications_are_never_cached(harness: Harness) -> None:
    clarifying = ScriptedChat(classified(intent="lookup", question_ids=["overall_deductible"]))
    client = app(harness, auth=Fixed("a"), broker=clarifying)
    assert last(post_sse(client, "/api/broker/chat", {"message": "Deductible on ZZ01?"}))[0] == "clarify"
    again = ScriptedChat(classified(intent="lookup", question_ids=["overall_deductible"]))
    client = app(harness, auth=Fixed("a"), broker=again)
    assert last(post_sse(client, "/api/broker/chat", {"message": "Deductible on ZZ01?"}))[0] == "clarify"
    assert len(again.requests) == 1  # asked the model again: the clarification wasn't cached


# ------------------------------------------------------------------ kill switch, quarantine, ownership


def test_the_kill_switch_answers_without_a_model(harness: Harness) -> None:
    harness.stores.switches.set_llm_enabled("broker", False, actor_hash="admin")
    chat = ScriptedChat()
    name, body = last(post_sse(app(harness, broker=chat), "/api/broker/chat", {"message": QUESTION}))
    assert (name, body["kind"], body["markdown"]) == ("answer", "message", guards.LLM_OFF)
    assert chat.requests == []


def test_answers_about_quarantined_plans_are_withheld(harness: Harness) -> None:
    harness.stores.switches.quarantine([GOLD], actor_hash="admin", reason="wrong number report")
    name, body = last(post_sse(app(harness, broker=lookups(1)), "/api/broker/chat", {"message": QUESTION}))
    assert (name, body["kind"], body["markdown"]) == ("answer", "message", guards.QUARANTINED)


def test_a_thread_belongs_to_whoever_started_it(harness: Harness) -> None:
    owner = app(harness, auth=Fixed("a"), broker=ScriptedChat(
        classified(intent="lookup", question_ids=["overall_deductible"])))  # fmt: skip
    thread_id = post_sse(owner, "/api/broker/chat", {"message": "Deductible on ZZ01?"})[0][1]["thread_id"]
    intruder = app(harness, auth=Fixed("b"))
    assert (
        intruder.post("/api/broker/chat/choose", json={"thread_id": thread_id, "plan_key": GOLD}).status_code
        == 404
    )
    assert intruder.post("/api/broker/chat", json={"thread_id": thread_id, "message": "x"}).status_code == 404
    feedback = {"thread_id": thread_id, "request_id": "req-12345678", "rating": "down"}
    assert intruder.post("/api/broker/feedback", json=feedback).status_code == 404


def test_every_turn_is_audited_without_user_text(harness: Harness) -> None:
    post_sse(app(harness, auth=Fixed("a"), broker=lookups(1)), "/api/broker/chat", {"message": QUESTION})
    (record,) = harness.stores.audit.records  # type: ignore[attr-defined]
    assert (record.bot, record.action, record.plan_keys, record.verifier) == (
        "broker",
        "turn",
        (GOLD,),
        "pass",
    )
    assert record.actor_hash != "a"
    assert "deductible" not in str(record).lower().replace("overall_deductible", "")


# ------------------------------------------------------------------ feedback, threads, admin


def test_feedback_on_an_own_thread(harness: Harness) -> None:
    client = app(harness, auth=Fixed("a"), broker=lookups(1))
    events = post_sse(client, "/api/broker/chat", {"message": QUESTION})
    thread_id, request_id = events[0][1]["thread_id"], events[0][1]["request_id"]
    body = {"thread_id": thread_id, "request_id": request_id, "rating": "down", "reasons": ["wrong_number"]}
    assert client.post("/api/broker/feedback", json=body).status_code == 204
    assert harness.stores.sessions.feedback[-1]["rating"] == "down"  # type: ignore[attr-defined]
    assert client.post("/api/broker/feedback", json={**body, "reasons": ["free text"]}).status_code == 422
    assert client.post("/api/broker/feedback", json={**body, "rating": "meh"}).status_code == 422


def test_brokers_list_their_own_threads_but_the_demo_account_lists_none(harness: Harness) -> None:
    client = app(harness, auth=Fixed("a"), broker=lookups(1))
    post_sse(client, "/api/broker/chat", {"message": QUESTION})
    threads = client.get("/api/broker/threads").json()["threads"]
    assert [t["title"] for t in threads] == [QUESTION]
    demo = app(harness, auth=Fixed("demo", demo=True))
    assert demo.get("/api/broker/threads").json() == {"threads": [], "shared_account": True}
    assert app(harness, auth=Fixed("s")).get("/api/public/threads").status_code == 404
    assert isinstance(harness.stores.sessions.list_threads("a")[0], ThreadSummary)


def test_admins_can_switch_off_answers_and_quarantine_plans(harness: Harness) -> None:
    admin = app(harness, auth=Fixed("root", groups=frozenset({"broker", "admin"})))
    assert (
        admin.post("/api/admin/kill-switch", json={"bot": "public", "llm_enabled": False}).status_code == 204
    )
    assert not harness.stores.switches.llm_enabled("public")
    assert admin.post("/api/admin/quarantine", json={"plan_keys": [GOLD], "action": "add"}).status_code == 204
    assert harness.stores.switches.quarantined() == {GOLD}
    assert (
        admin.post("/api/admin/quarantine", json={"plan_keys": [GOLD], "action": "remove"}).status_code == 204
    )
    assert harness.stores.switches.quarantined() == set()
    actions = [r.action for r in harness.stores.audit.records]  # type: ignore[attr-defined]
    assert actions == ["kill_switch", "quarantine", "quarantine"]


def test_non_admins_and_the_demo_account_cannot_use_admin_endpoints(harness: Harness) -> None:
    body = {"bot": "public", "llm_enabled": False}
    assert app(harness, auth=Fixed("u")).post("/api/admin/kill-switch", json=body).status_code == 403
    demo = Fixed("demo", demo=True, groups=frozenset({"broker", "admin"}))
    assert app(harness, auth=demo).post("/api/admin/kill-switch", json=body).status_code == 403
    assert harness.stores.switches.llm_enabled("public")
