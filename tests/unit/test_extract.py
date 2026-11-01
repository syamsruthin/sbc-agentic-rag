"""Extraction to facts rows and chunks, on the synthetic fixture."""

from __future__ import annotations

from pathlib import Path

import pytest

from sbc_extract.extract import CARRIED_NOTE, extract
from sbc_extract.review import classify

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "sbc_minimal.md"
OK = {"status": "REVIEW", "needs_review": "joined across a line break: example"}


@pytest.fixture(scope="module")
def text() -> str:
    return FIXTURE.read_text(encoding="utf-8")


def test_clean_plan_extracts(text: str) -> None:
    e = extract(text, OK)
    assert e.ok, e.quarantine_reasons
    assert e.plan is not None
    assert e.plan.plan_key == "2027:100000000001"
    assert e.plan.metal_tier == "Silver"
    assert e.plan.metal_tier_source == "plan_name"
    assert e.plan.review_classes == ("line_break_join",)


def test_benefit_rows_are_canonical_and_verbatim(text: str) -> None:
    e = extract(text, OK)
    specialist = [b for b in e.benefits if b.service_id == "specialist_visit"]
    assert [(b.tier_id, b.cost_verbatim) for b in specialist] == [
        ("in_net", "$60/visit, deductible does not apply"),
        ("out_net", "50% coinsurance"),
    ]
    assert specialist[0].limitations_kind == "none"
    generic = next(b for b in e.benefits if b.service_id == "rx_tier_1")
    assert generic.drug_tags == ("generic",)
    assert generic.limitations_kind == "blank"
    assert e.costs["$60/visit, deductible does not apply"].status == "exact"


def test_limits_and_lists(text: str) -> None:
    e = extract(text, OK)
    assert e.limits["deductible"].status == "exact"
    assert e.limits["oop"].entries[0].family is not None
    assert [i.item_verbatim for i in e.list_items if i.list_id == "excluded"] == [
        "Cosmetic surgery",
        "Long-term care",
    ]
    assert {x.example_id for x in e.examples} == {"peg", "joe", "mia"}


def test_chunks_have_no_plan_name_and_link_to_the_plan(text: str) -> None:
    e = extract(text, OK)
    assert all("Example Silver PPO 3000" not in c.text for c in e.chunks.values())
    assert {pc.plan_key for pc in e.plan_chunks} == {"2027:100000000001"}
    service = next(c for c in e.chunks.values() if c.chunk_type == "service_row")
    assert service.section_path == "If you visit a health care provider’s office or clinic > Specialist visit"


def test_identical_text_shares_a_chunk(text: str) -> None:
    other = text.replace("100000000001", "100000000002").replace("Example Silver PPO 3000", "Another Plan")
    a, b = extract(text, OK), extract(other, OK)
    assert set(a.chunks) == set(b.chunks)


def test_carried_note_is_stripped_and_flagged(text: str) -> None:
    carried = text.replace(
        "**Limitations, Exceptions, & Other Important Information:** none",
        f"**Limitations, Exceptions, & Other Important Information:** Limit 20 visits. {CARRIED_NOTE}",
    )
    e = extract(carried, OK)
    row = next(b for b in e.benefits if b.service_id == "specialist_visit")
    assert row.carried_note
    assert row.limitations_verbatim == "Limit 20 visits."


@pytest.mark.parametrize(
    ("verification", "reason"),
    [
        ({"status": "FAIL", "needs_review": ""}, "verification status FAIL"),
        ({"status": "REVIEW", "needs_review": "something new happened"}, "not allowlisted"),
        ({}, "verification status missing"),
    ],
)
def test_unsafe_plans_are_quarantined(text: str, verification: dict[str, str], reason: str) -> None:
    e = extract(text, verification)
    assert not e.ok
    assert any(reason in r for r in e.quarantine_reasons)


def test_unknown_service_label_quarantines(text: str) -> None:
    e = extract(text.replace("#### Specialist visit", "#### Specialist consult"), OK)
    assert any("taxonomy" in r for r in e.quarantine_reasons)


def test_event_inferred_flag_comes_from_the_review_reason(text: str) -> None:
    reason = (
        "benefits[Specialist visit]: event label not printed beside this row; MD puts it under "
        "'If you visit a health care provider’s office or clinic'"
    )
    e = extract(text, {"status": "REVIEW", "needs_review": reason})
    assert e.ok
    assert next(b for b in e.benefits if b.service_id == "specialist_visit").event_inferred
    assert classify(reason)[0].cls == "event_inferred"
