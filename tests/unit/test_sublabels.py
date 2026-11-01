"""Re-joining sub-labels the PDF wrapped."""

from __future__ import annotations

from sbc_core.sublabels import merge_label_lines


def test_wrapped_label_is_rejoined() -> None:
    lines = ["Designated", "Specialist", "$50/visit"]
    assert merge_label_lines(lines) == ["Designated Specialist", "$50/visit"]


def test_longest_label_wins() -> None:
    assert merge_label_lines(["Lower", "Copay", "PCP", "$0/visit"]) == ["Lower Copay PCP", "$0/visit"]


def test_unknown_words_stay_separate() -> None:
    lines = ["20% coinsurance, Durable", "Medical Equipment", "deductible applies"]
    assert merge_label_lines(lines) == lines
