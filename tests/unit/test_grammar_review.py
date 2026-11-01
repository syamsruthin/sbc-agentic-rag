"""Cost-grammar review sheet."""

from __future__ import annotations

from pathlib import Path

from sbc_index.grammar_review import _pattern, write_review
from tests.unit.test_bundle import make_bundle


def test_pattern_masks_amounts() -> None:
    assert _pattern("$1,250/visit, then 30% coinsurance") == "$N/visit, then P% coinsurance"
    assert _pattern("No charge for the first 3 visits") == "No charge for the first N visits"


def test_review_sheet_lists_every_pattern(tmp_path: Path) -> None:
    out = tmp_path / "review.md"
    patterns, cells = write_review(make_bundle(tmp_path), out)
    text = out.read_text(encoding="utf-8")
    assert patterns > 0
    assert cells > 0
    assert f"{patterns} patterns" in text
    table = [line for line in text.splitlines() if line.startswith("| ")][1:]  # skip the header row
    assert len(table) == patterns
    assert any("$N/visit, deductible does not apply" in row and "| exact |" in row for row in table)
