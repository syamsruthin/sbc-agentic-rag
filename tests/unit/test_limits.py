"""Deductible and out-of-pocket answer parser."""

from __future__ import annotations

from decimal import Decimal

import pytest

from sbc_extract.limits import parse_limit

EMBEDDED = (
    "Generally, you must pay all of the costs from providers up to the deductible amount before this plan "
    "begins to pay. If you have other family members on the plan, each family member must meet their own "
    "individual deductible until the total amount of deductible expenses paid by all family members meets "
    "the overall family deductible."
)
AGGREGATE = (
    "Generally, you must pay all of the costs from providers up to the deductible amount before this plan "
    "begins to pay. If you have other family members on the policy, the overall family deductible must be "
    "met before the plan begins to pay."
)


def test_two_tiers_embedded() -> None:
    r = parse_limit(
        "deductible",
        "$500/single or $1,500/family for Network Providers. $1,000/single or $3,000/family for Non-Network "
        "Providers.",
        EMBEDDED,
    )
    assert r.status == "exact"
    assert r.embedded is True
    net, non = r.entries
    assert (net.tiers, net.individual, net.family, net.individual_unit) == (
        ("in_net",),
        Decimal(500),
        Decimal(1500),
        "single",
    )
    assert (non.tiers, non.individual, non.family) == (("out_net",), Decimal(1000), Decimal(3000))


def test_aggregate_family_deductible() -> None:
    r = parse_limit("deductible", "$2,000/person or $4,000/family for In-Network Providers.", AGGREGATE)
    assert r.embedded is False


def test_flat_zero_deductible() -> None:
    r = parse_limit("deductible", "$0", "See the Common Medical Events chart below for your costs.")
    assert r.status == "exact"
    assert r.entries[0].individual == Decimal(0)
    assert r.entries[0].tiers == ()
    assert r.embedded is None


def test_combined_tiers_and_separate_rx_oop() -> None:
    r = parse_limit(
        "oop",
        "$2,500/single or $5,000/family for In-Network Providers. $6,000/single or $12,000/family for "
        "Non-Network Providers. This plan has a separate Out-of-Pocket Maximum of $3,000/single for Network "
        "Providers for Tier 4 Prescription Drugs.",
        "The out-of-pocket limit is the most you could pay in a year for covered services.",
    )
    assert r.status == "exact"
    (sep,) = r.separate
    assert (sep.amount, sep.tiers, sep.applies_to) == (
        Decimal(3000),
        ("in_net",),
        "Tier 4 Prescription Drugs",
    )


def test_tier_combination_phrase() -> None:
    r = parse_limit(
        "oop",
        "$9,000/person or $18,000/family for Tier 1 In-Network Providers and Tier 2 In-Network Providers "
        "combined.",
        "",
    )
    assert r.entries[0].tiers == ("in_net_t1", "in_net_t2")


def test_ihcp_and_three_part_forms() -> None:
    ihcp = parse_limit(
        "deductible",
        "$0 at Indian Health Care Provider (IHCP) or with IHCP referral at Non-IHCP; or $3,000/person or "
        "$6,000/family for Non-IHCP In-Network Providers.",
        "",
    )
    assert [e.tiers for e in ihcp.entries] == [("ihcp",), ("in_net",)]
    assert ihcp.entries[0].ihcp_referral
    three = parse_limit(
        "deductible", "$1,000/individual, $1,000/member or $2,000/family for In-Network Providers.", ""
    )
    assert (three.entries[0].individual, three.entries[0].per_member, three.entries[0].family) == (
        Decimal(1000),
        Decimal(1000),
        Decimal(2000),
    )


def test_not_applicable_and_hra_note() -> None:
    na = parse_limit(
        "oop", "Not Applicable.", "This plan does not have an out-of-pocket limit on your expenses."
    )
    assert na.entries[0].not_applicable
    hra = parse_limit(
        "deductible",
        "$500/person or $1,000/family for In-Network Providers. An HRA may be available to reimburse you for "
        "certain deductible and coinsurance amounts.",
        "",
    )
    assert hra.hra_note


@pytest.mark.parametrize(
    ("answer", "status"),
    [("", "blank"), ("$500/person or $1,000/household for In-Network Providers.", "unparsed")],
)
def test_blank_and_unknown_answers(answer: str, status: str) -> None:
    r = parse_limit("deductible", answer, "")
    assert r.status == status
    assert r.entries == ()
