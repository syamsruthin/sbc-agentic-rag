"""CMS PUF cross-check on a synthetic bundle."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from sbc_index.puf_check import check, hios_ids, money
from tests.unit.test_bundle import make_bundle

# Plan a lists its HIOS IDs only in the catalog (";"-joined); plan b prints two in the SBC (", "-joined).
CATALOG_IDS = "catalog_hios_plan_ids: 11111XX0010001-01;11111XX0019999-01\n"
SBC_IDS = "hios_plan_ids: 22222XX0010001-01, 22222XX0010002-01\n"

FIELDS = ["PlanId", "MedicalDrugDeductiblesIntegrated", "TEHBDedInnTier1Individual",
          "TEHBDedInnTier1FamilyPerGroup", "TEHBInnTier1IndividualMOOP", "TEHBInnTier1FamilyPerGroupMOOP",
          *(f"{p}{s}" for p in ("SBCHavingaBaby", "SBCHavingDiabetes", "SBCHavingSimplefracture")
            for s in ("Deductible", "Copayment", "Coinsurance", "Limit"))]  # fmt: skip


def _edit(name: str, text: str) -> str:
    if name == "a.md":
        return text.replace("catalog_sbc_id:", CATALOG_IDS + "catalog_sbc_id:", 1)
    if name == "b.md":
        return text.replace("plan_type:", SBC_IDS + "plan_type:", 1)
    return text


def _puf_row(plan_id: str, **overrides: str) -> dict[str, str]:
    row = {
        "PlanId": plan_id, "MedicalDrugDeductiblesIntegrated": "Yes",
        "TEHBDedInnTier1Individual": "$3,000", "TEHBDedInnTier1FamilyPerGroup": "$6,000 per group",
        "TEHBInnTier1IndividualMOOP": "$8,000", "TEHBInnTier1FamilyPerGroupMOOP": "$16,000 per group",
        "SBCHavingaBabyDeductible": "$3,000", "SBCHavingaBabyCopayment": "$10",
        "SBCHavingaBabyCoinsurance": "$2,700", "SBCHavingaBabyLimit": "$60",
        "SBCHavingDiabetesDeductible": "$900", "SBCHavingDiabetesCopayment": "$600",
        "SBCHavingDiabetesCoinsurance": "$0", "SBCHavingDiabetesLimit": "$20",
        "SBCHavingSimplefractureDeductible": "$2,500", "SBCHavingSimplefractureCopayment": "$200",
        "SBCHavingSimplefractureCoinsurance": "$0", "SBCHavingSimplefractureLimit": "$0",
    }  # fmt: skip
    return row | overrides


@pytest.fixture
def puf(tmp_path: Path) -> Path:
    path = tmp_path / "puf.csv"
    rows = [
        _puf_row("11111XX0010001-01", SBCHavingaBabyCoinsurance="$2,500"),
        _puf_row("22222XX0010001-01"),
        _puf_row("22222XX0010002-01"),
    ]
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    return path


def test_hios_ids_accepts_both_separators() -> None:
    assert hios_ids("A, B", "{}") == ["A", "B"]
    assert hios_ids("", '{"hios_plan_ids": "A;B"}') == ["A", "B"]
    assert hios_ids(None, "{}") == []


def test_money() -> None:
    assert money("$6,000 per group") == 6000
    assert money("Not Applicable") is None


def test_catalog_linked_plan_is_checked_and_mismatch_reported(tmp_path: Path, puf: Path) -> None:
    mismatches, counts = check(make_bundle(tmp_path, _edit), puf)
    assert counts["linked"] == 1
    assert counts["linked to several PUF plans (skipped)"] == 1
    for name in ("deductible individual", "deductible family", "oop individual", "oop family"):
        assert counts[f"{name}: match"] == 1
    assert counts["joe: match"] == 4
    assert [(m.plan_key, m.hios_id, m.check, m.sbc, m.puf) for m in mismatches] == [
        ("2027:100000000001", "11111XX0010001-01", "peg coinsurance", "$2,700", "$2,500")
    ]


def test_unlinked_plans_are_counted(tmp_path: Path, puf: Path) -> None:
    mismatches, counts = check(make_bundle(tmp_path), puf)
    assert (mismatches, counts["not linked"], counts["linked"]) == ([], 2, 0)
