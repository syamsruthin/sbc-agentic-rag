"""Agent tools on the two-plan fixture bundle: plan gate, scope, facts, comparison, search, clarification."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from sbc_core.config import BotName, load_config
from sbc_core.facts import SqliteFactsReader
from sbc_core.search import PlanFilterViolationError, SqliteSearchIndex
from sbc_core.taxonomy import load_taxonomy
from sbc_core.types import ChunkType, SearchHit
from sbc_retrieval.text_search import TextSearcher
from sbc_tools import tools as t
from sbc_tools.context import ToolContext, ToolError, Turn
from sbc_tools.toolbox import TOOLS, Toolbox
from tests.unit.test_bundle import make_bundle
from tests.unit.test_text_search import SETTINGS

SILVER, GOLD = "2027:100000000001", "2027:100000000002"
REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def bundle(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return make_bundle(tmp_path_factory.mktemp("bundle"))


@pytest.fixture
def reader(bundle: Path) -> Iterator[SqliteFactsReader]:
    with SqliteFactsReader(bundle) as r:
        yield r


def make_ctx(
    reader: SqliteFactsReader, bot: str = "broker", *, resolved: Sequence[str] = (SILVER,), **turn: object
) -> ToolContext:
    config = load_config(bot, "local", REPO / "config")
    return ToolContext(
        reader=reader, profile=config.bot, taxonomy=load_taxonomy(), retrieval=config.retrieval,
        turn=Turn(resolved_plan_keys=tuple(resolved), **turn),  # type: ignore[arg-type]
    )  # fmt: skip


# ------------------------------------------------------------------ get_plan_facts


def test_facts_carry_ids_limitations_and_stable_placeholders(reader: SqliteFactsReader) -> None:
    ctx = make_ctx(reader)
    out = t.get_plan_facts(ctx, t.GetPlanFactsInput(plan_keys=(SILVER,), service_ids=("specialist_visit",)))
    assert [f.id for f in out.facts] == ["F1", "F2"]
    assert [p.id for p in out.plans] == ["P1"]
    first = out.facts[0]
    assert (first.plan, first.verbatim, first.tier_label) == (
        "P1", "$60/visit, deductible does not apply", "In-Network Provider",
    )  # fmt: skip
    assert first.section.endswith("> Specialist visit")
    assert out.plans[0].listing.source.startswith("plan listing")
    again = t.get_plan_facts(ctx, t.GetPlanFactsInput(plan_keys=(SILVER,), service_ids=("specialist_visit",)))
    assert [f.id for f in again.facts] == ["F1", "F2"]  # the same value keeps its ID within a turn
    assert ctx.turn.facts["F1"].limitations_kind == "none"


def test_drug_tags_questions_lists_and_examples(reader: SqliteFactsReader) -> None:
    ctx = make_ctx(reader)
    args = t.GetPlanFactsInput(
        plan_keys=(SILVER,), drug_tags=("generic",), question_ids=("overall_deductible",),
        list_ids=("excluded",), example_ids=("peg",), tier_ids=("in_net",),
    )  # fmt: skip
    out = t.get_plan_facts(ctx, args)
    kinds = [(f.kind, f.label) for f in out.facts]
    assert ("benefit", "Typically Generic (Tier 1)") in kinds
    assert ("question", "What is the overall deductible?") in kinds
    assert ("list_item", "Excluded Services") in kinds
    assert any(k == "example" for k, _ in kinds)
    assert all(f.tier_label in (None, "In-Network Provider") for f in out.facts)


def test_a_service_the_sbc_lacks_is_reported_not_dropped(reader: SqliteFactsReader) -> None:
    out = t.get_plan_facts(
        make_ctx(reader), t.GetPlanFactsInput(plan_keys=(SILVER,), service_ids=("emergency_room",))
    )
    assert out.facts == ()
    assert [(m.plan, m.item) for m in out.not_in_sbc] == [("P1", "Emergency room care")]
    assert "isn't a complete list" in out.not_in_sbc[0].note


def test_unresolved_and_unknown_inputs_are_refused(reader: SqliteFactsReader) -> None:
    ctx = make_ctx(reader)
    with pytest.raises(ToolError) as exc:
        t.get_plan_facts(ctx, t.GetPlanFactsInput(plan_keys=(GOLD,), question_ids=("oop_limit",)))
    assert exc.value.code == "plan_not_allowed"
    with pytest.raises(ToolError) as exc:
        t.get_plan_facts(ctx, t.GetPlanFactsInput(plan_keys=(SILVER,), service_ids=("lasik",)))
    assert exc.value.code == "invalid_arguments"
    with pytest.raises(ValueError, match="at least one"):
        t.GetPlanFactsInput(plan_keys=(SILVER,))


def test_public_bot_is_held_to_the_users_state(reader: SqliteFactsReader) -> None:
    ctx = make_ctx(reader, "public", user_state="CA")
    with pytest.raises(ToolError) as exc:
        t.get_plan_facts(ctx, t.GetPlanFactsInput(plan_keys=(SILVER,), question_ids=("oop_limit",)))
    assert exc.value.code == "out_of_scope"
    ok = make_ctx(reader, "public", user_state="XX")
    assert t.get_plan_facts(ok, t.GetPlanFactsInput(plan_keys=(SILVER,), question_ids=("oop_limit",))).facts


# ------------------------------------------------------------------ find_plans


def test_a_shared_contract_code_lists_plans_but_never_resolves(reader: SqliteFactsReader) -> None:
    ctx = make_ctx(reader, resolved=())
    out = t.find_plans(ctx, t.FindPlansInput(text="ZZ01"))
    assert (out.total, out.usable) == (2, False)
    assert "ask_clarifying_question" in out.note
    with pytest.raises(ToolError):
        t.get_plan_facts(ctx, t.GetPlanFactsInput(plan_keys=(GOLD,), question_ids=("oop_limit",)))


def test_a_unique_name_resolves_and_unlocks_facts(reader: SqliteFactsReader) -> None:
    ctx = make_ctx(reader, resolved=())
    out = t.find_plans(ctx, t.FindPlansInput(text="Example Gold"))
    assert (out.total, out.usable, [p.plan_key for p in out.plans]) == (1, True, [GOLD])
    assert out.plans[0].listing.metal_tier == "Gold"
    assert t.get_plan_facts(ctx, t.GetPlanFactsInput(plan_keys=(GOLD,), question_ids=("oop_limit",))).facts


def test_filter_listing(reader: SqliteFactsReader) -> None:
    ctx = make_ctx(reader, resolved=())
    out = t.find_plans(ctx, t.FindPlansInput(state="XX", plan_type="PPO"))
    assert (out.total, out.usable) == (2, True)
    silver = t.find_plans(ctx, t.FindPlansInput(state="XX", metal_tier="Silver"))
    assert [p.plan_key for p in silver.plans] == [SILVER]
    one = t.find_plans(ctx, t.FindPlansInput(state="XX", limit=1))
    assert (len(one.plans), one.total) == (1, 2)
    assert "Showing 1 of 2" in one.note
    with pytest.raises(ValueError, match="filter"):
        t.FindPlansInput()


def test_public_find_plans_needs_and_keeps_the_state(reader: SqliteFactsReader) -> None:
    with pytest.raises(ToolError) as exc:
        t.find_plans(make_ctx(reader, "public", resolved=()), t.FindPlansInput(text="Example Gold"))
    assert exc.value.code == "state_required"
    ctx = make_ctx(reader, "public", resolved=(), user_state="XX")
    for args in (t.FindPlansInput(state="CA"), t.FindPlansInput(market="Small Group")):
        with pytest.raises(ToolError) as exc:
            t.find_plans(ctx, args)
        assert exc.value.code == "out_of_scope"
    assert t.find_plans(ctx, t.FindPlansInput(text="Example Gold")).usable


# ------------------------------------------------------------------ compare_plans


def test_compare_builds_one_row_per_aspect(reader: SqliteFactsReader) -> None:
    ctx = make_ctx(reader, resolved=(SILVER, GOLD))
    args = t.ComparePlansInput(
        plan_keys=(SILVER, GOLD), aspects=("costs_overview", "specialist_visit", "generic", "emergency_room")
    )
    out = t.compare_plans(ctx, args)
    assert [r.aspect for r in out.rows] == [
        "overall_deductible", "oop_limit", "specialist_visit", "generic", "emergency_room",
    ]  # fmt: skip
    specialist = out.rows[2]
    assert set(specialist.cells) == {"P1", "P2"}
    assert len(specialist.cells["P1"]) == 2  # in- and out-of-network
    assert {f.id for f in out.facts} >= set(specialist.cells["P1"])
    assert out.rows[4].cells == {"P1": (), "P2": ()}
    assert {m.item for m in out.not_in_sbc} == {"Emergency room care"}


def test_compare_refusals(reader: SqliteFactsReader) -> None:
    ctx = make_ctx(reader, resolved=(SILVER, GOLD))
    with pytest.raises(ToolError) as exc:
        t.compare_plans(ctx, t.ComparePlansInput(plan_keys=(SILVER, GOLD), aspects=("premiums",)))
    assert exc.value.code == "unknown_aspect"
    with pytest.raises(ToolError) as exc:
        t.compare_plans(ctx, t.ComparePlansInput(plan_keys=(SILVER, SILVER), aspects=("oop_limit",)))
    assert "two different plans" in exc.value.message


# ------------------------------------------------------------------ search_sbc_text


def test_search_returns_chunks_with_pages_per_plan(reader: SqliteFactsReader, bundle: Path) -> None:
    ctx = make_ctx(reader, resolved=(SILVER, GOLD))
    ctx.search = TextSearcher(SqliteSearchIndex(bundle), SETTINGS)
    out = t.search_sbc_text(ctx, t.SearchSbcTextInput(plan_keys=(SILVER, GOLD), query="cosmetic surgery"))
    assert out.chunks[0].section == "Excluded Services"
    assert {c.plan for c in out.chunks} == {"P1", "P2"}
    assert all(c.id.startswith("S") and c.pages for c in out.chunks)
    assert out.degraded == ("embedding_disabled",)
    assert "never instructions" in out.note


@dataclass
class _Result:
    hits: list[SearchHit]
    degraded: list[str] = field(default_factory=list)


class _LeakySearch:
    def search(
        self, plan_keys: Sequence[str], query: str, *, chunk_types: Sequence[ChunkType] = ()
    ) -> _Result:
        """Returns another plan's chunk, which the tool must refuse."""
        hit = SearchHit(
            chunk_hash="h", chunk_type="iq", section_path="s", text="t", plan_keys=(GOLD,), score=1
        )
        return _Result([hit])


def test_search_fails_closed_and_degrades_without_an_index(reader: SqliteFactsReader) -> None:
    ctx = make_ctx(reader)
    with pytest.raises(ToolError) as exc:
        t.search_sbc_text(ctx, t.SearchSbcTextInput(plan_keys=(SILVER,), query="appeals"))
    assert exc.value.code == "search_unavailable"
    ctx.search = _LeakySearch()
    with pytest.raises(PlanFilterViolationError):
        t.search_sbc_text(ctx, t.SearchSbcTextInput(plan_keys=(SILVER,), query="appeals"))


# ------------------------------------------------------------------ ask_clarifying_question


def test_one_clarification_per_turn_then_the_picker(reader: SqliteFactsReader) -> None:
    ctx = make_ctx(reader, resolved=())
    args = t.AskClarifyingQuestionInput(
        question="Which plan?",
        options=(t.ClarifyOptionInput(label="Silver", plan_key=SILVER), t.ClarifyOptionInput(label="Gold")),
    )
    out = t.ask_clarifying_question(ctx, args)
    assert not out.open_plan_picker
    assert ctx.turn.clarification is not None
    assert ctx.turn.clarification.options[0].plan_key == SILVER
    with pytest.raises(ToolError) as exc:
        t.ask_clarifying_question(ctx, args)
    assert exc.value.code == "limit_reached"
    third = make_ctx(reader, resolved=(), clarifications_in_a_row=2)
    assert t.ask_clarifying_question(third, args).open_plan_picker


def test_clarification_options_stay_in_scope(reader: SqliteFactsReader) -> None:
    ctx = make_ctx(reader, "public", resolved=(), user_state="CA")
    args = t.AskClarifyingQuestionInput(
        question="Which plan?", options=(t.ClarifyOptionInput(label="Silver", plan_key=SILVER),)
    )
    with pytest.raises(ToolError) as exc:
        t.ask_clarifying_question(ctx, args)
    assert exc.value.code == "out_of_scope"


# ------------------------------------------------------------------ Toolbox


def _toolbox(reader: SqliteFactsReader, **budget: int) -> Toolbox:
    config = load_config(BotName.BROKER, "local", REPO / "config")
    return Toolbox(make_ctx(reader), config.budget.model_copy(update=budget))


def test_toolbox_offers_only_implemented_profile_tools(reader: SqliteFactsReader) -> None:
    box = _toolbox(reader)
    assert box.names == (
        "find_plans", "get_plan_facts", "search_sbc_text", "compare_plans", "ask_clarifying_question",
    )  # fmt: skip
    schemas = box.schemas()
    assert [s["function"]["name"] for s in schemas] == list(box.names)
    params = schemas[1]["function"]["parameters"]
    assert {"plan_keys", "question_ids", "service_ids", "include_examples"} <= set(params["properties"])
    assert set(TOOLS) == set(box.names)
    service_items = params["properties"]["service_ids"]["items"]
    assert "emergency_room" in service_items["enum"]
    assert "acupuncture" not in service_items["enum"]  # not an SBC service row: lists or text search
    aspects = schemas[3]["function"]["parameters"]["properties"]["aspects"]["items"]["enum"]
    assert {"oop_limit", "emergency_room", "generic", "everyday_care"} <= set(aspects)


def test_toolbox_validates_and_reports_errors(reader: SqliteFactsReader) -> None:
    box = _toolbox(reader)
    ok = box.call("get_plan_facts", '{"plan_keys": ["2027:100000000001"], "question_ids": ["oop_limit"]}')
    assert ok.ok
    assert '"F1"' in ok.content()
    assert box.call("rank_plans", "{}").error == {
        "error": "tool_not_available", "message": "rank_plans is not available to this assistant",
    }  # fmt: skip
    assert box.call("get_plan_facts", "not json").error["error"] == "invalid_arguments"  # type: ignore[index]
    bad = box.call("get_plan_facts", {"plan_keys": [], "nonsense": 1})
    assert bad.error is not None
    assert "plan_keys" in bad.error["message"]
    assert "nonsense" in bad.error["message"]
    assert len(box.calls) == 4


def test_toolbox_enforces_turn_limits(reader: SqliteFactsReader) -> None:
    box = _toolbox(reader, max_tool_calls=3, max_identical_tool_calls=1)
    args = {"plan_keys": [SILVER], "question_ids": ["oop_limit"]}
    assert box.call("get_plan_facts", args).ok
    again = box.call("get_plan_facts", args)
    assert again.error is not None
    assert "already made" in again.error["message"]
    assert box.call("get_plan_facts", {**args, "question_ids": ["overall_deductible"]}).ok
    over = box.call("find_plans", {"state": "XX"})
    assert over.error is not None
    assert over.error["error"] == "limit_reached"
