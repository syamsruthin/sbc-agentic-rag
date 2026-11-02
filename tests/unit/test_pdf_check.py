"""PDF gold check: phrase matching, column gutters and cell assignment on synthetic poppler words."""

from __future__ import annotations

from evals.harness.pdf_check import Grid, Word, columns_from_headers, compare, find_phrase


def _line(text: str, x: float, y: float, line: int, width: float = 6.0, height: float = 13.5) -> list[Word]:
    """Words of one poppler line, laid out left to right from (x, y)."""
    out = []
    for token in text.split():
        out.append(Word(x, y, x + width * len(token), y + height, token, line))
        x += width * len(token) + width
    return out


def _page() -> list[Word]:
    """A two-column benefit table: labels at x=100, in-network values at x=400, two centered rows.

    Row 1 (Specialist visit) has a two-line value set flush; row 2's value starts above its label, the case
    a midpoint between label boxes gets wrong."""
    words: list[Word] = []
    words += _line("Services You May Need", 100, 50, 0)
    words += _line("Network Provider", 400, 50, 1)
    words += _line("Specialist visit", 100, 100, 2)
    words += _line("$40/visit deductible does not", 400, 93.25, 3)
    words += _line("apply", 400, 106.75, 4)
    words += _line("Imaging (CT/PET scans, MRIs)", 100, 140, 5)
    words += _line("30% coinsurance", 400, 140, 6)
    words += _line("Typically Generic (Tier 1)", 100, 190, 7)
    words += _line("$10/prescription, deductible", 400, 156, 8)  # starts above row 3's label
    words += _line("does not apply", 400, 169.5, 9)
    words += _line("and more text", 400, 183, 10)
    words += _line("and more text", 400, 196.5, 11)
    words += _line("and more text", 400, 210, 12)
    words += _line("(home delivery)", 400, 223.5, 13)
    words += _line("* For more information about limitations see the plan or policy document", 50, 260, 14)
    return words


def test_find_phrase_ignores_spaces_and_wraps() -> None:
    words = _line("Preventive care/screening/ immunization", 0, 0, 0)
    assert find_phrase(words, "Preventive care/screening/immunization") == [(0, 2)]
    assert find_phrase(words, "care/screening") == []  # must end on a word boundary
    assert find_phrase(words, "Preventive care") == []


def test_compare_statuses() -> None:
    assert compare("No charge", "No  charge") == "cell_exact"
    assert compare("care/screening/ immunization", "care/screening/immunization") == "cell_wrapped"
    assert compare("Designated Specialist $50/visit", "Designated Specialist: $50/visit") == "cell_markup"
    assert compare("50% coinsurance", "30% coinsurance") is None


def test_columns_split_at_the_gutter() -> None:
    words = _page()
    columns = columns_from_headers(words, ["Services You May Need", "Network Provider"])
    assert columns is not None
    (_, _, services_right), (_, network_left, _) = columns
    assert services_right == network_left
    label_right = max(w.x1 for w in words if w.line in (2, 5, 7))
    assert label_right < network_left < 400
    assert columns_from_headers(words, ["Services You May Need", "Missing Header"]) is None


def test_centered_cells_are_assigned_to_their_rows() -> None:
    words = _page()
    columns = columns_from_headers(words, ["Services You May Need", "Network Provider"])
    assert columns is not None
    grid = Grid.build(words, columns, ["Specialist visit", "Imaging (CT/PET scans, MRIs)",
                                       "Typically Generic (Tier 1)"])  # fmt: skip
    assert grid.cell(words, "Specialist visit", "Network Provider") == ["$40/visit deductible does not apply"]
    assert grid.cell(words, "Imaging (CT/PET scans, MRIs)", "Network Provider") == ["30% coinsurance"]
    (drug,) = grid.cell(words, "Typically Generic (Tier 1)", "Network Provider")
    assert drug.startswith("$10/prescription")
    assert drug.endswith("(home delivery)")
    assert "policy document" not in drug  # the page-wide footnote line is ignored


def test_top_aligned_rows_run_to_the_next_label() -> None:
    words: list[Word] = []
    words += _line("Important Questions", 50, 10, 0)
    words += _line("Answers", 250, 10, 1)
    words += _line("What is the overall deductible?", 50, 40, 2, width=4)
    words += _line("$500/person.", 250, 40, 3)
    words += _line("$1,000/family.", 250, 70, 4)  # a second paragraph, far below the label
    words += _line("Are there other deductibles?", 50, 100, 5, width=4)
    words += _line("No.", 250, 100, 6)
    words += _line(
        "All copayment and coinsurance costs shown in this chart are after the deductible", 20, 140, 7
    )
    words += _line("Unrelated", 250, 160, 8)
    columns = columns_from_headers(words, ["Important Questions", "Answers"])
    assert columns is not None
    grid = Grid.build(words, columns, ["What is the overall deductible?", "Are there other deductibles?"],
                      align="top")  # fmt: skip
    assert grid.cell(words, "What is the overall deductible?", "Answers") == ["$500/person. $1,000/family."]
    assert grid.cell(words, "Are there other deductibles?", "Answers") == ["No."]  # stops at the wide line


def test_a_repeated_label_returns_one_cell_per_occurrence_top_to_bottom() -> None:
    words: list[Word] = []
    words += _line("Services You May Need", 100, 50, 0)
    words += _line("Network Provider", 400, 50, 1)
    words += _line("Physician/surgeon fees", 100, 100, 2)  # outpatient surgery
    words += _line("No charge", 400, 100, 3)
    words += _line("Facility fee", 100, 140, 4)
    words += _line("$200/visit", 400, 140, 5)
    words += _line("Physician/surgeon fees", 100, 180, 6)  # hospital stay
    words += _line("40% coinsurance", 400, 180, 7)
    words += _line("Urgent care", 100, 220, 8)
    columns = columns_from_headers(words, ["Services You May Need", "Network Provider"])
    assert columns is not None
    grid = Grid.build(words, columns, ["Physician/surgeon fees", "Facility fee", "Urgent care"])
    assert grid.cell(words, "Physician/surgeon fees", "Network Provider") == ["No charge", "40% coinsurance"]
    assert grid.cell(words, "Urgent care", "Network Provider") == [""]


def test_headers_that_wrap_or_nest_are_found() -> None:
    words: list[Word] = []
    words += _line("Services You May Need", 100, 50, 0)
    words += _line("Tier 1 In-Network Provider", 300, 50, 1, width=4)  # contains "In-Network Provider"
    words += _line("In-", 420, 50, 2)  # "In-Network Provider" wrapped onto two lines
    words += _line("Network Provider", 420, 63.5, 3, width=3)
    words += _line("Specialist visit", 100, 100, 4)
    columns = columns_from_headers(words, ["Services You May Need", "Tier 1 In-Network Provider",
                                           "In-Network Provider"])  # fmt: skip
    assert columns is not None
    bands = {name: (left, right) for name, left, right in columns}
    assert bands["Tier 1 In-Network Provider"][1] <= 420 <= bands["In-Network Provider"][1]
