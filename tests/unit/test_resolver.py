"""Deterministic plan resolution against a synthetic bundle (two plans that share contract code ZZ01)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from sbc_core.config import PlanScope
from sbc_core.facts import SqliteFactsReader
from sbc_retrieval.resolver import PlanResolver, id_tokens
from tests.unit.test_bundle import make_bundle

SILVER, GOLD = "2027:100000000001", "2027:100000000002"


@pytest.fixture
def reader(tmp_path: Path) -> Iterator[SqliteFactsReader]:
    with SqliteFactsReader(make_bundle(tmp_path)) as r:
        yield r


@pytest.fixture
def resolver(reader: SqliteFactsReader) -> PlanResolver:
    return PlanResolver(reader, PlanScope())


@pytest.mark.parametrize(
    ("text", "tokens"),
    [
        ("What's the deductible on BTCU8 and 0JF1?", ["BTCU8", "0JF1"]),
        ("hios 17575IN0990011-02, please", ["17575IN0990011-02"]),
        ("HIOS is shaped like a code; lookup ignores it", ["HIOS"]),
        ("plan 2027:166713189246", ["2027", "166713189246"]),
        ("plans with a $1300 deductible or 1,300 or 1300.00 or 1300/visit or 1300%", []),
        ("btcu8 in lower case is not a code", []),
        ("(ZZ01).", ["ZZ01"]),
    ],
)
def test_id_tokens(text: str, tokens: list[str]) -> None:
    assert id_tokens(text) == tokens


def test_unique_id_resolves(resolver: PlanResolver) -> None:
    r = resolver.resolve("What's the deductible on 100000000002?")
    assert (r.status, r.plan_keys, r.sources) == ("resolved", (GOLD,), {GOLD: "id"})


def test_id_shared_by_two_plans_is_ambiguous(resolver: PlanResolver) -> None:
    r = resolver.resolve("deductible on ZZ01")
    assert r.status == "ambiguous"
    assert r.plan_keys == ()
    assert {p.plan_key for p in r.ambiguities[0].candidates} == {SILVER, GOLD}


def test_ui_selection_resolves_and_unknown_selection_is_refused(resolver: PlanResolver) -> None:
    assert resolver.resolve("this plan", ui_selected=[SILVER]).plan_keys == (SILVER,)
    r = resolver.resolve("this plan", ui_selected=["2027:999999999999"])
    assert (r.status, r.not_found) == ("not_found", ("2027:999999999999",))


def test_name_search(resolver: PlanResolver) -> None:
    r = resolver.resolve("the gold plan", mentions=["Gold"])
    assert (r.status, r.plan_keys, r.sources) == ("resolved", (GOLD,), {GOLD: "search"})
    assert resolver.resolve("x", mentions=["Platinum"]).status == "not_found"


def test_ambiguous_name_never_guesses(reader: SqliteFactsReader) -> None:
    r = PlanResolver(reader, PlanScope(), max_options=1).resolve("x", mentions=["Example"])
    assert r.status == "ambiguous"
    assert r.plan_keys == ()
    (amb,) = r.ambiguities
    assert len(amb.candidates) == 1
    assert amb.more


def test_mention_of_an_already_resolved_plan_is_not_ambiguous(resolver: PlanResolver) -> None:
    r = resolver.resolve("Example Gold (100000000002)", mentions=["Example"])
    assert (r.status, r.plan_keys) == ("resolved", (GOLD,))


def test_no_plan_named_is_missing(resolver: PlanResolver) -> None:
    assert resolver.resolve("what's coinsurance?").status == "missing"


def test_scope_hides_plans_even_by_exact_id(reader: SqliteFactsReader) -> None:
    group_only = PlanResolver(reader, PlanScope(markets=("Small Group",)))
    r = group_only.resolve("deductible on 100000000002")
    assert (r.status, r.not_found) == ("not_found", ("100000000002",))
    assert group_only.resolve("x", ui_selected=[GOLD]).status == "not_found"
    assert group_only.resolve("x", mentions=["Gold"]).status == "not_found"
    assert PlanResolver(reader, PlanScope()).resolve("100000000002", states=["ZZ"]).status == "not_found"
