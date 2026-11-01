"""Cross-check bundle facts against the CMS Plan Attributes PUF (PLAN.md section 3.2.3, check 4).

For SBCs linked to a HIOS plan ID, compare the in-network deductible and out-of-pocket maximum and the three
coverage examples with the values CMS publishes. The SBC is the legal document, so a mismatch never changes a
fact; it goes into the report for a person to explain.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
from collections import Counter
from contextlib import closing
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

EXAMPLES = {"peg": "SBCHavingaBaby", "joe": "SBCHavingDiabetes", "mia": "SBCHavingSimplefracture"}
EXAMPLE_FIELDS = {"Deductibles": "Deductible", "Copayments": "Copayment", "Coinsurance": "Coinsurance",
                  "Limits or exclusions": "Limit"}  # fmt: skip
_MONEY_RE = re.compile(r"\$([\d,]+(?:\.\d+)?)")
# The SBC front matter lists HIOS IDs as "A, B"; the portal catalog joins them as "A;B".
_HIOS_SPLIT_RE = re.compile(r"[,;]")
_LIMITS_SQL = (
    "SELECT l.kind, s.parse_json FROM plan_limit l JOIN limit_string s USING (limit_id) WHERE l.plan_id = ?"
)


@dataclass(frozen=True)
class Mismatch:
    plan_key: str
    hios_id: str
    check: str
    sbc: str
    puf: str


def money(text: str | None) -> Decimal | None:
    m = _MONEY_RE.search(text or "")
    return Decimal(m[1].replace(",", "")) if m else None


def _limit_in_net(parse_json: str) -> tuple[Decimal | None, Decimal | None]:
    parse = json.loads(parse_json)
    for entry in parse.get("entries", []):
        if entry.get("not_applicable"):
            continue
        tiers = entry.get("tiers") or []
        if not tiers or tiers[0] in ("in_net", "in_net_t1"):
            ind = entry.get("individual")
            fam = entry.get("family")
            return (
                Decimal(str(ind)) if ind is not None else None,
                Decimal(str(fam)) if fam is not None else None,
            )
    return None, None


def hios_ids(sbc_hios: str | None, catalog_json: str) -> list[str]:
    """HIOS plan IDs printed in the SBC, or the catalog's when the SBC prints none."""
    raw = sbc_hios or json.loads(catalog_json).get("hios_plan_ids", "")
    return [h.strip() for h in _HIOS_SPLIT_RE.split(raw) if h.strip()]


def check(bundle: Path, puf_csv: Path) -> tuple[list[Mismatch], Counter[str]]:
    with puf_csv.open(newline="", encoding="utf-8-sig") as f:
        puf = {r["PlanId"]: r for r in csv.DictReader(f)}
    with closing(sqlite3.connect(bundle)) as db:
        return _check(db, puf)


def _check(db: sqlite3.Connection, puf: dict[str, dict[str, str]]) -> tuple[list[Mismatch], Counter[str]]:
    mismatches: list[Mismatch] = []
    counts: Counter[str] = Counter()
    plans = db.execute("SELECT plan_id, plan_key, hios_ids, catalog_json FROM plan").fetchall()
    for plan_id, plan_key, hios, catalog_json in plans:
        linked = [h for h in hios_ids(hios, catalog_json) if h in puf]
        if len(linked) != 1:
            counts["not linked" if not linked else "linked to several PUF plans (skipped)"] += 1
            continue
        hios_id = linked[0]
        row = puf[hios_id]
        counts["linked"] += 1
        prefix = "TEHB" if row.get("MedicalDrugDeductiblesIntegrated") == "Yes" else "MEHB"
        limits = dict(db.execute(_LIMITS_SQL, (plan_id,)).fetchall())
        pairs = {
            "deductible individual": (
                _limit_in_net(limits["deductible"])[0],
                f"{prefix}DedInnTier1Individual",
            ),
            "deductible family": (
                _limit_in_net(limits["deductible"])[1],
                f"{prefix}DedInnTier1FamilyPerGroup",
            ),
            "oop individual": (_limit_in_net(limits["oop"])[0], f"{prefix}InnTier1IndividualMOOP"),
            "oop family": (_limit_in_net(limits["oop"])[1], f"{prefix}InnTier1FamilyPerGroupMOOP"),
        }
        for name, (sbc_value, column) in pairs.items():
            puf_value = money(row.get(column))
            if sbc_value is None or puf_value is None:
                counts[f"{name}: not comparable"] += 1
            elif sbc_value == puf_value:
                counts[f"{name}: match"] += 1
            else:
                mismatches.append(Mismatch(plan_key, hios_id, name, str(sbc_value), row.get(column, "")))
        for example_id, puf_prefix in EXAMPLES.items():
            found = db.execute("SELECT data_id FROM plan_example WHERE plan_id = ? AND example_id = ?",
                               (plan_id, example_id)).fetchone()  # fmt: skip
            if not found:
                continue
            (text,) = db.execute("SELECT text FROM text_string WHERE text_id = ?", found).fetchone()
            data = json.loads(text)
            lines = dict(data["cost_sharing"]) | dict(data["not_covered"])
            for label, suffix in EXAMPLE_FIELDS.items():
                sbc_value, puf_value = money(lines.get(label)), money(row.get(puf_prefix + suffix))
                name = f"{example_id} {label.lower()}"
                if sbc_value is None or puf_value is None:
                    counts[f"{example_id}: not comparable"] += 1
                elif sbc_value == puf_value:
                    counts[f"{example_id}: match"] += 1
                else:
                    mismatches.append(
                        Mismatch(
                            plan_key, hios_id, name, lines.get(label, ""), row.get(puf_prefix + suffix, "")
                        )
                    )
    return mismatches, counts


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(
        description="Cross-check a facts bundle against the CMS Plan Attributes PUF."
    )
    ap.add_argument("bundle", type=Path)
    ap.add_argument("--puf", type=Path, default=Path("data/reference/cms_puf_2027/plan-attributes-puf.csv"))
    ap.add_argument("--out", type=Path, default=Path("data/markdown/puf_check_2027.csv"))
    args = ap.parse_args(argv)
    mismatches, counts = check(args.bundle, args.puf)
    with args.out.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["plan_key", "hios_id", "check", "sbc", "puf"])
        w.writerows([m.plan_key, m.hios_id, m.check, m.sbc, m.puf] for m in mismatches)
    for key, n in sorted(counts.items()):
        print(f"{n:6d}  {key}")
    print(f"{len(mismatches)} mismatches -> {args.out}")
