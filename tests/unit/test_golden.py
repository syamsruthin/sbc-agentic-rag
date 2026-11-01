"""Golden dataset schema, loader and bundle consistency check."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evals.harness.golden import GoldenItem, check_against_bundle, load_golden
from tests.unit.test_bundle import _corpus

ITEM = {
    "id": "gs-000001",
    "dataset_version": "0.1.0",
    "bot": "both",
    "category": "single_field_lookup",
    "difficulty": 1,
    "turns": [{"role": "user", "content": "What's the specialist copay on ZZ01?"}],
    "gold": {
        "plan_keys": ["2027:100000000001"],
        "expected_behavior": "answer",
        "expected_values": [
            {
                "plan_key": "2027:100000000001",
                "fact": "benefit:specialist_visit:in_net",
                "verbatim": "$60/visit, deductible does not apply",
            }
        ],
    },
    "provenance": {"method": "hand", "author": "test", "verified_against": "pdf", "reviewed_by": "reviewer"},
}


def test_valid_item_and_gating() -> None:
    item = GoldenItem.model_validate(ITEM)
    assert item.gating


def test_unreviewed_item_does_not_gate() -> None:
    pending = {**ITEM, "provenance": {"method": "synthetic", "author": "x", "verified_against": "pending"}}
    assert not GoldenItem.model_validate(pending).gating


@pytest.mark.parametrize(
    "change",
    [
        {"id": "golden-1"},
        {"turns": [{"role": "assistant", "content": "hi"}]},
        {"gold": {"expected_behavior": "answer"}},
        {"gold": {**ITEM["gold"], "plan_keys": []}},  # type: ignore[dict-item]
    ],
)
def test_invalid_items_are_rejected(change: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="validation error"):
        GoldenItem.model_validate({**ITEM, **change})


def test_loader_rejects_duplicate_ids(tmp_path: Path) -> None:
    (tmp_path / "a.jsonl").write_text(json.dumps(ITEM) + "\n" + json.dumps(ITEM) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate"):
        load_golden(tmp_path)


def test_bundle_check_flags_disagreements(tmp_path: Path) -> None:
    from sbc_index.bundle import build_bundle

    md_dir, report = _corpus(tmp_path)
    bundle = tmp_path / "facts.sqlite"
    build_bundle(md_dir, report, bundle, "t", root=tmp_path)
    good = GoldenItem.model_validate(ITEM)
    wrong_gold = json.loads(json.dumps(ITEM))
    wrong_gold["id"] = "gs-000002"
    wrong_gold["gold"]["expected_values"][0]["verbatim"] = "$50/visit"
    bad = GoldenItem.model_validate(wrong_gold)
    problems = check_against_bundle([good, bad], bundle)
    assert len(problems) == 1
    assert "gs-000002" in problems[0]


def test_bundle_check_reads_coverage_example_totals(tmp_path: Path) -> None:
    from sbc_index.bundle import build_bundle

    md_dir, report = _corpus(tmp_path)
    bundle = tmp_path / "facts.sqlite"
    build_bundle(md_dir, report, bundle, "t", root=tmp_path)
    item = json.loads(json.dumps(ITEM))
    item["gold"]["expected_values"] = [
        {
            "plan_key": "2027:100000000001",
            "fact": "example:peg:The total Peg would pay is",
            "verbatim": "$5,770",
        },
        {"plan_key": "2027:100000000001", "fact": "example:peg:Total Example Cost", "verbatim": "$12,700"},
    ]
    assert check_against_bundle([GoldenItem.model_validate(item)], bundle) == []
