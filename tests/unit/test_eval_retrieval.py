"""Retrieval eval harness: metric math, scoring rules, the fixture run and the CI gate's checks."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from evals.harness.ci_gate import item_problems, synthetic_items, validate_golden
from evals.harness.golden import GoldenItem
from evals.harness.retrieval import (
    PlanRanking,
    Scopes,
    bm25_memory_searcher,
    check_gates,
    gold_services,
    predicted_services,
    run,
    score_resolution,
    summarize,
    to_markdown,
    write_reports,
)
from sbc_core.config import PlanScope, Retrieval
from sbc_core.facts import SqliteFactsReader
from sbc_core.taxonomy import load_taxonomy
from sbc_core.types import SearchHit
from sbc_retrieval.resolver import Ambiguity, Resolution
from sbc_retrieval.text_search import TextSearcher
from tests.unit.test_bundle import make_bundle
from tests.unit.test_golden import ITEM

SILVER, GOLD = "2027:100000000001", "2027:100000000002"
SETTINGS = Retrieval(
    prefetch_limit=20,
    rrf_k=60,
    rerank_candidates=20,
    keep_per_plan=6,
    rerank_enabled=True,
    max_plans_per_search=6,
)
SCOPES = Scopes(broker=PlanScope(), public=PlanScope(markets=("Individual",)))


def _item(**changes: Any) -> GoldenItem:
    data = json.loads(json.dumps(ITEM))
    for key, value in changes.items():
        if key in ("gold", "provenance", "context"):
            data[key] = {**data.get(key, {}), **value}
        else:
            data[key] = value
    return GoldenItem.model_validate(data)


@pytest.fixture
def bundle(tmp_path: Path) -> Path:
    return make_bundle(tmp_path)


@pytest.fixture
def reader(bundle: Path) -> Iterator[SqliteFactsReader]:
    with SqliteFactsReader(bundle) as r:
        yield r


# ------------------------------------------------------------------------------------------- metrics


def test_ranking_metrics() -> None:
    r = PlanRanking("2027:1", gold=["g1", "g2"], ranked=["x", "g1", "y", "z", "w", "v", "g2"], keep=6)
    assert r.recall_at(5) == 0.5
    assert r.recall_at(10) == 1.0
    assert r.reciprocal_rank == 0.5
    assert r.context == ["x", "g1", "y", "z", "w", "v"]
    assert r.context_precision == 0.5  # the one gold chunk in context is at rank 2
    assert r.context_recall == 0.5
    perfect = PlanRanking("2027:1", gold=["g"], ranked=["g", "x"], keep=6)
    assert (perfect.context_precision, perfect.reciprocal_rank) == (1.0, 1.0)
    missed = PlanRanking("2027:1", gold=["g"], ranked=["x"], keep=6)
    assert (missed.context_precision, missed.reciprocal_rank, missed.recall_at(5)) == (0.0, 0.0, 0.0)


def _resolution(
    status: str, keys: tuple[str, ...] = (), ambiguities: tuple[Ambiguity, ...] = ()
) -> Resolution:
    return Resolution(status=status, plan_keys=keys, ambiguities=ambiguities)  # type: ignore[arg-type]


def test_resolution_scoring() -> None:
    item = _item()  # gold plan 2027:100000000001
    assert score_resolution(item, _resolution("resolved", ("2027:100000000001",))).correct
    wrong = score_resolution(item, _resolution("resolved", ("2027:100000000009",)))
    assert (wrong.correct, wrong.confident_wrong) == (False, True)
    asked = score_resolution(item, _resolution("ambiguous"))
    assert (asked.correct, asked.confident_wrong) == (False, False)

    clarify = _item(
        category="ambiguous", gold={"plan_keys": [], "expected_behavior": "clarify", "expected_values": []}
    )
    assert score_resolution(clarify, _resolution("ambiguous")).correct
    guessed = score_resolution(clarify, _resolution("resolved", ("2027:100000000001",)))
    assert (guessed.correct, guessed.confident_wrong) == (False, True)


def test_clarify_options_must_be_offered(reader: SqliteFactsReader) -> None:
    plans = reader.plans([SILVER, GOLD])
    item = _item(
        category="ambiguous",
        gold={
            "plan_keys": [],
            "expected_behavior": "clarify",
            "expected_values": [],
            "clarify_options_must_include": ["ZZ01"],
        },
    )
    offered = _resolution("ambiguous", ambiguities=(Ambiguity("x", (plans[SILVER],)),))
    assert score_resolution(item, offered).correct
    item = _item(
        category="ambiguous",
        gold={
            "plan_keys": [],
            "expected_behavior": "clarify",
            "expected_values": [],
            "clarify_options_must_include": ["QQ99"],
        },
    )
    assert not score_resolution(item, offered).correct


def test_gold_services_come_from_values_and_sections() -> None:
    tax = load_taxonomy()
    item = _item(
        gold={
            "sections": [
                "If you need immediate medical attention > Emergency room care",
                "Important Questions > What is the overall deductible?",
            ]
        }
    )
    assert gold_services(item, tax) == {"specialist_visit", "emergency_room"}


def test_predicted_services_map_drug_words_to_the_plans_tiers(reader: SqliteFactsReader) -> None:
    tax = load_taxonomy()
    assert predicted_services("generic drug copay?", [SILVER], reader, tax) == ({"rx_tier_1"}, False)
    assert predicted_services("any Rx coverage?", [SILVER], reader, tax) == ({"rx_tier_1"}, False)
    assert predicted_services("doctor visit", [SILVER], reader, tax)[1]  # ambiguous term


def test_plan_names_are_masked_before_mapping(reader: SqliteFactsReader, bundle: Path) -> None:
    from evals.harness.retrieval import evaluate_item

    item = _item(
        turns=[{"role": "user", "content": "Specialist copay on Blue Rx Option 7 (ZZ01)?"}],
        context={"plan_mentions": ["Blue Rx Option 7"]},
    )
    searcher = bm25_memory_searcher(bundle)(SETTINGS)
    result = evaluate_item(item, reader, searcher, keep=6, scopes=SCOPES, taxonomy=load_taxonomy())
    assert result.services is not None
    assert result.services.predicted == ["specialist_visit"]  # "Rx" in the mention is not a drug question


# ------------------------------------------------------------------------------------------- runs


def test_fixture_run_meets_the_hard_gates(bundle: Path, reader: SqliteFactsReader) -> None:
    report = run(
        synthetic_items(bundle),
        reader,
        bm25_memory_searcher(bundle),
        SETTINGS,
        setup="bm25_only",
        scopes=SCOPES,
    )
    s = report.summary
    assert s["plan_filter"]["correctness"] == 1.0
    assert s["plan_filter"]["hits"] > 0
    assert s["resolution"]["confident_wrong_plan"] == 0
    assert s["resolution"]["accuracy"] == 1.0
    assert s["retrieval"]["recall_at_10"] == 1.0
    assert report.hard_gates_failed == []
    assert any("pending" in n for n in report.notes)
    assert {g["gate"] for g in report.gates} == {
        "plan filter 100%",
        "0 confident wrong-plan resolutions",
        "service mapping >= 95%",
    }


class _LeakyIndex:
    """An index whose filter is broken: it reports a chunk for a plan that doesn't contain it."""

    def search(self, query: Any) -> list[SearchHit]:
        return [
            SearchHit(
                chunk_hash="0" * 20,
                chunk_type="iq",
                section_path="s",
                text="t",
                plan_keys=query.plan_keys,
                score=1.0,
            )
        ]


def test_a_leaky_index_fails_the_plan_filter_gate(bundle: Path, reader: SqliteFactsReader) -> None:
    items = synthetic_items(bundle)[:3]
    report = run(
        items, reader, lambda s: TextSearcher(_LeakyIndex(), s), SETTINGS, setup="bm25_only", scopes=SCOPES
    )
    assert report.summary["plan_filter"]["violations"] == 3
    assert report.summary["plan_filter"]["correctness"] == 0.0
    assert report.hard_gates_failed == ["plan filter 100%"]


def test_gates_and_reports(bundle: Path, reader: SqliteFactsReader, tmp_path: Path) -> None:
    gates = check_gates(summarize([]), "full")
    assert {g["gate"]: g["passed"] for g in gates}["recall@5 >= 0.90"] is None  # nothing measured
    report = run(
        synthetic_items(bundle)[:4],
        reader,
        bm25_memory_searcher(bundle),
        SETTINGS,
        setup="bm25_only",
        scopes=SCOPES,
        run_id="retrieval-bm25_only-20270101T000000Z",
    )
    json_path, md_path = write_reports([report], tmp_path / "reports")
    assert json.loads(json_path.read_text())[0]["setup"] == "bm25_only"
    markdown = md_path.read_text()
    assert "| Plan-filter correctness | 1.000 |" in markdown
    assert to_markdown([report]) == markdown


# ------------------------------------------------------------------------------------------- CI gate


def test_item_problems() -> None:
    bad = _item(gold={"chunk_hashes": ["XYZ"]}, provenance={"verified_against": "pdf", "reviewed_by": None})
    problems = item_problems(bad, "0.2.0")
    assert any("dataset_version" in p for p in problems)
    assert any("chunk hash" in p for p in problems)
    assert any("no reviewer" in p for p in problems)
    assert any("PDF page" in p for p in problems)


def test_validate_golden(tmp_path: Path) -> None:
    (tmp_path / "0.1.0").mkdir()
    good = json.loads(json.dumps(ITEM))
    good["dataset_version"] = "0.1.0"
    good["provenance"] |= {"reviewed_at": "2026-11-20", "pdf_pages": [2]}
    (tmp_path / "0.1.0" / "single.jsonl").write_text(json.dumps(good) + "\n", encoding="utf-8")
    (tmp_path / "latest").mkdir()
    counts, problems = validate_golden(tmp_path)
    assert counts == {"0.1.0": {"single_field_lookup": 1}}
    assert problems == ["latest: golden directories are named by semantic version"]
