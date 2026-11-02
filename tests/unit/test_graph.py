"""The turn graph end to end on the fixture bundle, with scripted model responses instead of OpenRouter."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from sbc_agent.graph import CHITCHAT, OUT_OF_SCOPE, Agent
from sbc_agent.redact import redact
from sbc_agent.respond import MEDICAL_PREFIX, RECOMMENDATION_PREFIX, Deps
from sbc_core.config import load_config
from sbc_core.facts import SqliteFactsReader
from sbc_core.llm import ChatResult, GatewayError, ToolCall, Usage
from sbc_core.taxonomy import load_taxonomy
from tests.unit.test_tools import GOLD, SILVER

REPO = Path(__file__).resolve().parents[2]
Script = ChatResult | Exception | Callable[[list[dict[str, Any]]], ChatResult]


def reply(content: str | None = None, *calls: tuple[str, dict[str, Any]]) -> ChatResult:
    return ChatResult(
        content=content,
        tool_calls=tuple(
            ToolCall(id=f"call_{i}", name=n, arguments=json.dumps(a)) for i, (n, a) in enumerate(calls)
        ),
        usage=Usage(kind="chat", model="scripted", latency_ms=1),
    )


def classified(**fields: Any) -> ChatResult:
    return reply(json.dumps(fields))


class ScriptedChat:
    """Plays back responses in order and records every request."""

    def __init__(self, *script: Script) -> None:
        self.script = list(script)
        self.requests: list[dict[str, Any]] = []

    def chat(self, model: str, messages: Any, **kwargs: Any) -> ChatResult:
        self.requests.append({"model": model, "messages": list(messages), **kwargs})
        if not self.script:
            raise AssertionError("the graph made more model calls than scripted")
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step(list(messages)) if callable(step) else step


@pytest.fixture(scope="module")
def bundle(tmp_path_factory: pytest.TempPathFactory) -> Path:
    from tests.unit.test_bundle import make_bundle

    return make_bundle(tmp_path_factory.mktemp("bundle"))


@pytest.fixture
def reader(bundle: Path) -> Iterator[SqliteFactsReader]:
    with SqliteFactsReader(bundle) as r:
        yield r


def agent(reader: SqliteFactsReader, chat: ScriptedChat, bot: str = "broker") -> Agent:
    config = load_config(bot, "local", REPO / "config")
    deps = Deps(reader=reader, chat=chat, config=config, taxonomy=load_taxonomy(), plan_year=2027)
    return Agent(deps, InMemorySaver())


# ------------------------------------------------------------------ fast path


def test_fast_path_lookup_is_classify_plus_one_compose(reader: SqliteFactsReader) -> None:
    chat = ScriptedChat(
        classified(intent="lookup", plan_mentions=["Example Gold"], service_terms=["specialist"]),
        reply("On {{P1.name}} a specialist visit is {{F1}} in network and {{F2}} out of network."),
    )
    out = agent(reader, chat).ask("t1", "What's the specialist copay on Example Gold?")
    assert out.kind == "answer"
    assert "a specialist visit is $60/visit, deductible does not apply [1] in network" in out.markdown
    assert out.markdown.startswith("**Example Gold (ZZ01) · 2027 SBC")
    assert out.plan_keys == (GOLD,)
    assert out.trace["path"] == "fast"
    assert [t["name"] for t in out.trace["tools"]] == ["get_plan_facts"]
    assert len(chat.requests) == 2
    assert chat.requests[1]["tools"] == ()  # compose can't call tools


def test_plural_service_terms_take_the_fast_path(reader: SqliteFactsReader) -> None:
    chat = ScriptedChat(
        classified(intent="lookup", plan_mentions=["Example Gold"], service_terms=["specialist visits"]),
        reply("{{F1}}"),
    )
    out = agent(reader, chat).ask("t1p", "Specialist visits on Example Gold?")
    assert out.trace["path"] == "fast"
    assert out.trace["classification"]["service_terms"] == ["specialist visits"]


def test_a_rejected_draft_is_retried_once(reader: SqliteFactsReader) -> None:
    chat = ScriptedChat(
        classified(intent="lookup", plan_mentions=["Example Gold"], service_terms=["specialist"]),
        reply("The specialist copay is $60."),
        reply("The specialist copay is {{F1}}."),
    )
    out = agent(reader, chat).ask("t1", "Specialist copay on Example Gold?")
    assert out.kind == "answer"
    assert "$60/visit, deductible does not apply [1]" in out.markdown
    assert len(out.trace["verifier"]) == 1
    feedback = chat.requests[2]["messages"][-1]["content"]
    assert feedback.startswith("Your answer was rejected:")
    assert '"$60."' in feedback or '"$60"' in feedback


def test_two_rejections_give_the_template_answer(reader: SqliteFactsReader) -> None:
    chat = ScriptedChat(
        classified(intent="lookup", plan_mentions=["Example Gold"], service_terms=["specialist"]),
        reply("It's $60."),
        reply("Sixty dollars."),
    )
    out = agent(reader, chat).ask("t1", "Specialist copay on Example Gold?")
    assert out.kind == "answer"
    assert out.trace["fallback_reason"] == "verifier_failed"
    assert "Specialist visit (In-Network Provider): $60/visit, deductible does not apply" in out.markdown


# ------------------------------------------------------------------ resolution and clarification


def test_a_shared_code_asks_then_answers_after_the_choice(reader: SqliteFactsReader) -> None:
    chat = ScriptedChat(
        classified(intent="lookup", question_ids=["overall_deductible"]),
        reply("The overall deductible on {{P1.name}} is {{F1}}."),
    )
    bot = agent(reader, chat)
    asked = bot.ask("t2", "What's the deductible on ZZ01?")
    assert asked.kind == "clarify"
    assert asked.clarification is not None
    assert {o.plan_key for o in asked.clarification.options} == {SILVER, GOLD}
    assert len(chat.requests) == 1  # nothing was answered yet
    out = bot.choose("t2", plan_key=GOLD)
    assert out.kind == "answer"
    assert out.plan_keys == (GOLD,)
    assert "The overall deductible on Example Gold is" in out.markdown


def test_a_lookup_without_a_plan_opens_the_picker(reader: SqliteFactsReader) -> None:
    chat = ScriptedChat(classified(intent="lookup", question_ids=["oop_limit"]))
    out = agent(reader, chat).ask("t3", "What's the out-of-pocket max?")
    assert out.kind == "clarify"
    assert out.clarification is not None
    assert out.clarification.open_plan_picker


def test_a_generic_plan_reference_uses_the_selection(reader: SqliteFactsReader) -> None:
    chat = ScriptedChat(
        classified(intent="lookup", plan_mentions=["this plan"], question_ids=["oop_limit"]), reply("{{F1}}")
    )
    out = agent(reader, chat).ask("t3b", "What's the OOP max on this plan?", ui_selected=(GOLD,))
    assert (out.kind, out.plan_keys) == ("answer", (GOLD,))


def test_a_service_question_without_a_plan_asks_for_one(reader: SqliteFactsReader) -> None:
    chat = ScriptedChat(classified(intent="other", service_terms=["acupuncture"]))
    out = agent(reader, chat).ask("t3c", "Is acupuncture covered?")
    assert out.kind == "clarify"


def test_a_follow_up_keeps_the_previous_single_plan(reader: SqliteFactsReader) -> None:
    chat = ScriptedChat(
        classified(intent="lookup", plan_mentions=["Example Gold"], question_ids=["overall_deductible"]),
        reply("{{F1}}"),
        classified(intent="follow_up", service_terms=["specialist"]),
        reply("{{P1.name}}: {{F1}}."),
    )
    bot = agent(reader, chat)
    bot.ask("t4", "Deductible on Example Gold?")
    out = bot.ask("t4", "And the specialist?")
    assert out.kind == "answer"
    assert out.plan_keys == (GOLD,)
    assert chat.requests[2]["messages"][-2]["role"] == "assistant"  # history reaches the classifier


# ------------------------------------------------------------------ agent loop


def test_agent_loop_calls_tools_then_answers(reader: SqliteFactsReader) -> None:
    chat = ScriptedChat(
        classified(intent="compare", service_terms=["specialist"]),
        reply(None, ("compare_plans", {"plan_keys": [SILVER, GOLD], "aspects": ["specialist_visit"]})),
        reply("| | {{P1.code}} | {{P2.code}} |\n|---|---|---|\n| Specialist | {{F1}} | {{F3}} |"),
    )
    out = agent(reader, chat).ask("t5", "Compare specialist visits", ui_selected=(SILVER, GOLD))
    assert out.kind == "answer"
    assert out.trace["path"] == "agent"
    assert [t["name"] for t in out.trace["tools"]] == ["compare_plans"]
    tool_message = chat.requests[2]["messages"][-1]
    assert tool_message["role"] == "tool"
    assert '"F1"' in tool_message["content"]
    assert out.markdown.count("$60/visit, deductible does not apply") == 2


def test_the_agent_can_ask_a_clarifying_question(reader: SqliteFactsReader) -> None:
    chat = ScriptedChat(
        classified(intent="other"),
        reply(None, ("ask_clarifying_question", {"question": "In-network or out-of-network?"})),
    )
    out = agent(reader, chat).ask("t6", "How much for a specialist?", ui_selected=(SILVER,))
    assert out.kind == "clarify"
    assert out.markdown == "In-network or out-of-network?"


def test_model_failure_falls_back_to_the_template(reader: SqliteFactsReader) -> None:
    chat = ScriptedChat(classified(intent="other"), GatewayError("down", 503))
    out = agent(reader, chat).ask("t7", "Tell me about this plan", ui_selected=(SILVER,))
    assert out.kind == "message"
    assert out.trace["fallback_reason"].startswith("model_unavailable")


def test_an_unreadable_classification_takes_the_agent_loop(reader: SqliteFactsReader) -> None:
    chat = ScriptedChat(reply("not json"), reply("{{P1.name}} is a plan I can answer about."))
    out = agent(reader, chat).ask("t8", "hello?", ui_selected=(SILVER,))
    assert out.kind == "answer"
    assert out.trace["classify_error"].startswith("classify_unreadable")
    assert out.trace["path"] == "agent"


# ------------------------------------------------------------------ refusals, prefixes, privacy


@pytest.mark.parametrize(("intent", "text"), [("out_of_scope", OUT_OF_SCOPE), ("chitchat", CHITCHAT)])
def test_out_of_scope_and_chitchat_need_one_call(reader: SqliteFactsReader, intent: str, text: str) -> None:
    chat = ScriptedChat(classified(intent=intent))
    out = agent(reader, chat).ask("t9", "What's the premium?")
    assert (out.kind, out.markdown) == ("message", text)
    assert len(chat.requests) == 1


def test_public_bot_declines_to_recommend_and_medical_advice(reader: SqliteFactsReader) -> None:
    chat = ScriptedChat(
        classified(intent="other", plan_recommendation=True),
        reply(
            "{{P1.name}} lists a specialist copay of {{F1}}.",
        ),
    )
    chat.script.insert(
        1, reply(None, ("get_plan_facts", {"plan_keys": [SILVER], "service_ids": ["specialist_visit"]}))
    )
    out = agent(reader, chat, "public").ask(
        "t10", "Which plan should I buy?", ui_selected=(SILVER,), user_state="XX"
    )
    assert RECOMMENDATION_PREFIX in out.markdown
    chat = ScriptedChat(classified(intent="other", medical_advice=True), reply("{{P1.name}} covers imaging."))
    out = agent(reader, chat).ask("t11", "Do I need an MRI?", ui_selected=(SILVER,))
    assert out.markdown.split("\n\n")[1].startswith(MEDICAL_PREFIX)


def test_pii_never_reaches_the_model(reader: SqliteFactsReader) -> None:
    chat = ScriptedChat(classified(intent="out_of_scope"))
    out = agent(reader, chat).ask(
        "t12", "My SSN is 123-45-6789 and member ID XYZ123456789, what's my deductible?"
    )
    sent = json.dumps(chat.requests[0]["messages"])
    assert "123-45-6789" not in sent
    assert "XYZ123456789" not in sent
    assert out.trace["pii_found"] == ["SSN", "MEMBER_ID"]


def test_redaction_keeps_plan_identifiers() -> None:
    text = (
        "BTCU8, 12345CA1234567-01 and 166713189246; call 555-201-3344, jane.doe@example.com, born 04/12/1980"
    )
    cleaned = redact(text)
    assert cleaned.text.startswith("BTCU8, 12345CA1234567-01 and 166713189246;")
    assert "[PHONE]" in cleaned.text
    assert "[EMAIL]" in cleaned.text
    assert "[DATE]" in cleaned.text
    assert sorted(cleaned.found) == ["DATE", "EMAIL", "PHONE"]


# ------------------------------------------------------------------ streaming (WP4)


def test_stream_yields_progress_then_the_same_reply_as_ask(reader: SqliteFactsReader) -> None:
    from sbc_agent.graph import Progress, Reply

    def script() -> ScriptedChat:
        return ScriptedChat(
            classified(intent="lookup", plan_mentions=["Example Gold"], service_terms=["specialist"]),
            reply("On {{P1.name}} a specialist visit is {{F1}} in network and {{F2}} out of network."),
        )

    asked = agent(reader, script()).ask("t1", "Specialist copay on Example Gold?")
    items = list(agent(reader, script()).stream("t1", "Specialist copay on Example Gold?"))
    assert [i.node for i in items if isinstance(i, Progress)] == ["ingress", "classify", "resolve", "answer"]
    streamed = items[-1]
    assert isinstance(streamed, Reply)
    assert (streamed.kind, streamed.markdown, streamed.plan_keys) == (
        asked.kind,
        asked.markdown,
        asked.plan_keys,
    )


def test_stream_reports_a_clarification_and_resumes_with_a_choice(reader: SqliteFactsReader) -> None:
    from sbc_agent.graph import Reply

    chat = ScriptedChat(
        classified(intent="lookup", question_ids=["overall_deductible"]),
        reply("The overall deductible on {{P1.name}} is {{F1}}."),
    )
    bot = agent(reader, chat)
    asked = list(bot.stream("t2", "What's the deductible on ZZ01?"))[-1]
    assert isinstance(asked, Reply)
    assert asked.kind == "clarify"
    out = list(bot.stream("t2", choice={"plan_key": GOLD}))[-1]
    assert isinstance(out, Reply)
    assert (out.kind, out.plan_keys) == ("answer", (GOLD,))
    with pytest.raises(ValueError, match="exactly one"):
        list(bot.stream("t3"))
