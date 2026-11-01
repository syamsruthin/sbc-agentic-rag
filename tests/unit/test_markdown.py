"""Strict SBC Markdown parser, on a synthetic document with the converter's layout."""

from __future__ import annotations

from pathlib import Path

import pytest

from sbc_extract.markdown import QUESTIONS, MarkdownFormatError, parse_markdown

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "sbc_minimal.md"


@pytest.fixture(scope="module")
def text() -> str:
    return FIXTURE.read_text(encoding="utf-8")


def test_front_matter_values_stay_strings(text: str) -> None:
    sbc = parse_markdown(text)
    assert sbc.front_matter["catalog_sbc_id"] == "100000000001"
    assert sbc.front_matter["catalog_contract_code"] == "ZZ01"


def test_header_and_title(text: str) -> None:
    sbc = parse_markdown(text)
    assert sbc.title == "Example Silver PPO 3000"
    assert sbc.header["Plan Type"] == "PPO"
    assert sbc.header["Coverage Period"] == "01/01/2027 - 12/31/2027"


def test_questions_follow_the_cms_template(text: str) -> None:
    sbc = parse_markdown(text)
    assert tuple(q.question for q in sbc.questions) == QUESTIONS
    assert sbc.questions[0].answer == "$3,000/person or $6,000/family for In-Network Providers."
    assert sbc.question_pages == (1, 2)


def test_services_keep_event_note_pages_and_column_order(text: str) -> None:
    sbc = parse_markdown(text)
    assert [c.label for c in sbc.columns] == ["In-Network Provider", "Out-of-Network Provider"]
    assert sbc.chart_notes[0].startswith("All copayment and coinsurance costs")
    specialist, generic = sbc.services
    assert specialist.event_note is None
    assert specialist.values[0] == ("In-Network Provider", "$60/visit, deductible does not apply")
    assert specialist.limitations == "none"
    assert generic.event_note is not None
    assert generic.event_note.startswith("More information")
    assert generic.pages == (2, 3)
    assert generic.limitations == "(blank in the SBC)"


def test_lists_narrative_and_examples(text: str) -> None:
    sbc = parse_markdown(text)
    assert sbc.excluded.items == ("Cosmetic surgery", "Long-term care")
    assert sbc.other_covered.items == ("Acupuncture",)
    assert sbc.narrative[0].title == "Your Rights to Continue Coverage"
    peg = sbc.examples[0]
    assert peg.total_cost == "$12,700"
    assert peg.total_pay == "$5,770"
    assert dict(peg.cost_sharing)["Coinsurance"] == "$2,700"
    assert sbc.examples_pages == (5,)


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        ("## Excluded Services", "## Excluded Things", "sections"),
        ("### Do you need a referral to see a specialist?", "### Is a referral needed?", "CMS template"),
        ("- **Out-of-Network Provider:** 50% coinsurance\n", "", "tier columns"),
        ("**Answer:** No.\n", "Stray line\n", "unexpected line"),
        ("### Mia’s Simple Fracture", "Mia’s Simple Fracture", "3 coverage examples"),
    ],
)
def test_layout_drift_fails_loudly(text: str, old: str, new: str, message: str) -> None:
    assert old in text
    with pytest.raises(MarkdownFormatError, match=message):
        parse_markdown(text.replace(old, new, 1))
