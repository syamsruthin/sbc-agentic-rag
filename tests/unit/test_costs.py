"""Cost-string grammar: structure of common patterns, re-render guarantee, and fail-closed behavior."""

from __future__ import annotations

from decimal import Decimal

import pytest

from sbc_extract.costs import CostTerm, canonical, parse_cost, render


def _only_term(text: str) -> CostTerm:
    parsed = parse_cost(text)
    assert parsed.status == "exact", parsed.reason
    (part,) = parsed.parts
    (channel,) = part.channels
    (term,) = channel.steps
    return term


def test_coinsurance() -> None:
    term = _only_term("20% coinsurance")
    assert (term.kind, term.pct, term.deductible) == ("coinsurance", 20, "not_stated")


@pytest.mark.parametrize(
    "text", ["$40/visit, deductible does not apply", "$40/visit deductible does not apply"]
)
def test_copay_with_waived_deductible_in_both_spellings(text: str) -> None:
    term = _only_term(text)
    assert (term.kind, term.amount, term.unit, term.deductible) == (
        "copay",
        Decimal(40),
        "visit",
        "does_not_apply",
    )


def test_named_deductible() -> None:
    term = _only_term("25% coinsurance, Home Health Care deductible applies")
    assert term.deductible == "applies"
    assert term.deductible_name == "Home Health Care"


def test_then_chain() -> None:
    parsed = parse_cost("$250/visit deductible does not apply then 30% coinsurance deductible does not apply")
    assert parsed.status == "exact"
    steps = parsed.parts[0].channels[0].steps
    assert [s.kind for s in steps] == ["copay", "coinsurance"]
    assert [s.deductible for s in steps] == ["does_not_apply", "does_not_apply"]


def test_first_visits_then() -> None:
    parsed = parse_cost("$35/visit for the first 3 visits; deductible does not apply, then 30% coinsurance")
    first, after = parsed.parts[0].channels[0].steps
    assert (first.amount, first.first_visits, first.deductible) == (Decimal(35), 3, "does_not_apply")
    assert after.pct == 30


def test_no_charge_first_visit_variant_wording() -> None:
    parsed = parse_cost("No charge/benefit for the first 3 visits, then $50/visit")
    assert parsed.status == "exact"
    first = parsed.parts[0].channels[0].steps[0]
    assert (first.kind, first.first_visits, first.wording) == ("no_charge", 3, "No charge/benefit")


def test_pharmacy_channels() -> None:
    parsed = parse_cost(
        "$15/prescription, deductible does not apply (retail) and $37.50/prescriptio n, deductible does not "
        "apply (home delivery)"
    )
    assert parsed.status == "exact"
    retail, mail = parsed.parts[0].channels
    assert (retail.channel, retail.steps[0].amount) == ("retail", Decimal(15))
    assert (mail.channel, mail.steps[0].amount) == ("home_delivery", Decimal("37.50"))


def test_capped_coinsurance_retail_only() -> None:
    parsed = parse_cost(
        "30% coinsurance up to $500/prescription, Prescription Drug deductible applies (retail only)"
    )
    term = parsed.parts[0].channels[0].steps[0]
    assert parsed.parts[0].channels[0].channel == "retail_only"
    assert (term.pct, term.cap_amount, term.cap_unit) == (30, Decimal(500), "prescription")
    assert (term.deductible, term.deductible_name) == ("applies", "Prescription Drug")


def test_greater_of_both_wordings() -> None:
    a = _only_term_channel("$60/prescription or 50% coinsurance, whichever is greater (retail only)")
    b = _only_term_channel("Greater of $60 or 50% coinsurance (retail only)")
    assert (a.kind, a.amount, a.pct) == (b.kind, b.amount, b.pct) == ("greater_of", Decimal(60), 50)


def _only_term_channel(text: str) -> CostTerm:
    parsed = parse_cost(text)
    assert parsed.status == "exact", parsed.reason
    return parsed.parts[0].channels[0].steps[0]


def test_sublabel_parts() -> None:
    parsed = parse_cost("Office Visit: $20/visit; Other Outpatient: 20% coinsurance")
    assert [p.sublabel for p in parsed.parts] == ["Office Visit", "Other Outpatient"]


def test_day_copay_with_admission_cap() -> None:
    term = _only_term("$300/day to a maximum of $1,500/admission")
    assert (term.amount, term.unit, term.cap_amount, term.cap_unit) == (
        Decimal(300),
        "day",
        Decimal(1500),
        "admission",
    )


@pytest.mark.parametrize(
    "text",
    [
        "20% coinsurance, Durable Medical; Equipment: deductible applies",  # converter-mangled cell
        "Not covered for laboratory, professional services, x-ray & diagnostic imaging",
        "25% coinsurance up to $400/prescription (retail and home delivery) Pharmacy OOP applies to Tier 4.",
        "$40/visit, deductible maybe applies",
    ],
)
def test_unknown_shapes_fail_closed(text: str) -> None:
    parsed = parse_cost(text)
    assert parsed.status == "unparsed"
    assert parsed.parts == ()


def test_blank_cell() -> None:
    assert parse_cost("(blank in the SBC)").status == "blank"


@pytest.mark.parametrize(
    "text",
    [
        "No charge",
        "Same as In-Network Tier 1",
        "Covered as In-Network",
        "$50 copayment up to plan's Maximum Allowed Amount",
        "Reimbursed Up to $30",
        "Amount above $35 Reimbursement",
        "40% coinsurance, deductible does not apply, up to a $60 maximum",
        "$45/day up to 3 days/admission",
        "$1,000/admission; then 20% coinsurance",
    ],
)
def test_exact_parses_re_render_to_canonical_text(text: str) -> None:
    parsed = parse_cost(text)
    assert parsed.status == "exact", parsed.reason
    assert render(parsed) == canonical(text)


def test_amounts_keep_their_written_form() -> None:
    parsed = parse_cost("$37.50/prescription (retail only)")
    assert render(parsed) == "$37.50/prescription (retail only)"
