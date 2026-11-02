"""Generation eval scoring, through the real graph with scripted model responses on the fixture bundle."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from evals.harness.generation import build_report, phase, run, run_item, to_markdown, ungrounded
from evals.harness.golden import GoldenItem
from sbc_agent.graph import Agent
from sbc_core.facts import SqliteFactsReader
from tests.unit.test_graph import ScriptedChat, agent, classified, reply
from tests.unit.test_tools import GOLD, SILVER

SPECIALIST_IN = "$60/visit, deductible does not apply"


def item(**fields: Any) -> GoldenItem:
    base: dict[str, Any] = {
        "id": "gs-000001", "dataset_version": "0.0.0", "bot": "broker", "category": "single_field_lookup",
        "difficulty": 1, "turns": [{"role": "user", "content": "Specialist copay on Example Gold?"}],
        "provenance": {"method": "hand", "author": "t", "verified_against": "pdf", "reviewed_by": "r",
                       "reviewed_at": "2026-11-02"},
    }  # fmt: skip
    gold = {
        "plan_keys": [GOLD], "expected_behavior": "answer",
        "expected_values": [
            {"plan_key": GOLD, "fact": "benefit:specialist_visit:in_net", "verbatim": SPECIALIST_IN}
        ],
        "expected_tools": [{"tool": "get_plan_facts", "args_subset": {"service_ids": ["specialist_visit"]}}],
    }  # fmt: skip
    gold.update(fields.pop("gold", {}))
    return GoldenItem.model_validate({**base, **fields, "gold": gold})


@pytest.fixture
def reader(tmp_path: Path) -> Iterator[SqliteFactsReader]:
    from tests.unit.test_bundle import make_bundle

    with SqliteFactsReader(make_bundle(tmp_path)) as r:
        yield r


def _fast_lookup(answer: str) -> ScriptedChat:
    return ScriptedChat(
        classified(intent="lookup", plan_mentions=["Example Gold"], service_terms=["specialist"]),
        reply(answer),
    )


def _one(reader: SqliteFactsReader, chat: ScriptedChat, golden: GoldenItem) -> Any:
    bot: Agent = agent(reader, chat)
    return run_item(golden, lambda _: bot)


def test_a_correct_answer_passes_every_check(reader: SqliteFactsReader) -> None:
    result = _one(reader, _fast_lookup("On {{P1.name}} a specialist visit is {{F1}}."), item())
    assert result.behavior == "answer"
    assert result.checks == {
        "behavior": True, "numeric_exact_match": True, "no_ungrounded_numbers": True, "disclaimer": True,
        "plan_filter": True, "confident_wrong_plan": False, "tool_selection": True,
    }  # fmt: skip
    assert result.path == "fast"
    assert result.llm_calls == 2


def test_a_missing_value_fails_exact_match(reader: SqliteFactsReader) -> None:
    result = _one(reader, _fast_lookup("Out of network a specialist visit is {{F2}}."), item())
    assert result.checks["numeric_exact_match"] is False
    assert result.problems == [f"missing value: {SPECIALIST_IN}"]


def test_answering_another_plan_is_a_confident_wrong_plan(reader: SqliteFactsReader) -> None:
    golden = item(gold={"plan_keys": [SILVER], "expected_values": [], "must_include": ["x"]})
    result = _one(reader, _fast_lookup("{{F1}}"), golden)
    assert result.checks["plan_filter"] is False
    assert result.checks["confident_wrong_plan"] is True


def test_answering_when_gold_says_clarify_is_a_confident_wrong_plan(reader: SqliteFactsReader) -> None:
    golden = item(
        gold={"expected_behavior": "clarify", "plan_keys": [], "expected_values": [], "expected_tools": []}
    )
    result = _one(reader, _fast_lookup("{{F1}}"), golden)
    assert result.checks["behavior"] is False
    assert result.checks["confident_wrong_plan"] is True


def test_clarify_and_refuse_behaviors(reader: SqliteFactsReader) -> None:
    clarify = item(
        turns=[{"role": "user", "content": "Deductible on ZZ01?"}],
        gold={"expected_behavior": "clarify", "plan_keys": [], "expected_values": [],
              "expected_tools": [{"tool": "ask_clarifying_question"}],
              "clarify_options_must_include": ["ZZ01"]},
    )  # fmt: skip
    result = _one(
        reader, ScriptedChat(classified(intent="lookup", question_ids=["overall_deductible"])), clarify
    )
    assert (result.behavior, result.checks["behavior"], result.checks["clarify_options"]) == (
        "clarify",
        True,
        True,
    )
    assert (
        result.checks["tool_selection"] is True
    )  # the graph asked in code; that counts as the clarifying tool
    refuse = item(
        gold={"expected_behavior": "refuse", "plan_keys": [], "expected_values": [], "expected_tools": [],
              "must_not_include": ["$"]},
    )  # fmt: skip
    result = _one(reader, ScriptedChat(classified(intent="out_of_scope")), refuse)
    assert (result.behavior, result.checks["behavior"], result.checks["must_not_include"]) == (
        "refuse",
        True,
        True,
    )


def test_a_clarification_about_an_amount_is_not_a_wrong_plan(reader: SqliteFactsReader) -> None:
    golden = item(gold={"expected_behavior": "clarify", "expected_values": [], "expected_tools": []})
    result = _one(reader, _fast_lookup("{{F1}}"), golden)  # gold names the plan: only the amount was unclear
    assert result.checks["behavior"] is False
    assert result.checks["confident_wrong_plan"] is False


def test_an_item_that_crashes_counts_as_an_error(reader: SqliteFactsReader) -> None:
    def broken(_: str) -> Agent:
        raise RuntimeError("boom")

    (result,) = run([item()], broken)
    assert result.error
    assert result.checks == {"behavior": False}
    report = build_report([result], "test.1", "0.0.0")
    assert {g["gate"]: g["passed"] for g in report["gates"]}["0 errors"] is False


def test_ungrounded_digits_are_found() -> None:
    trace = {"grounding": {"body": "Copay is $60/visit [1] and 30 more", "grounded": ["$60/visit"]}}
    found = ungrounded(trace)
    assert len(found) == 2  # the "3" and "0" of "30"; the value and the citation marker are accounted for
    assert all("30 more" in snippet for snippet in found)


def test_phases_and_report(reader: SqliteFactsReader) -> None:
    assert phase(item()) == 1
    assert phase(item(gold={"expected_tools": [{"tool": "rank_plans"}]})) == 2
    assert phase(item(category="multi_turn")) == 2
    bot = agent(reader, _fast_lookup("On {{P1.name}} a specialist visit is {{F1}}."))
    results = run([item()], lambda _: bot)
    report = build_report(results, "test.1", "0.0.0")
    gates = {g["gate"]: g["passed"] for g in report["gates"]}
    assert gates["numeric exact match 100%"] is True
    assert gates["0 ungrounded numbers"] is True
    md = to_markdown(report)
    assert "| Numeric exact match | 1.000 |" in md
    assert "single_field_lookup" in md
