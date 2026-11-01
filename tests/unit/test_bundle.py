"""Facts bundle builder on synthetic SBCs."""

from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path

import pytest

from sbc_index.bundle import build_bundle

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "sbc_minimal.md"


def _corpus(root: Path) -> tuple[Path, Path]:
    text = FIXTURE.read_text(encoding="utf-8")
    md_dir = root / "data" / "markdown" / "sbc" / "2027" / "example" / "XX" / "Individual"
    md_dir.mkdir(parents=True)
    plans = {
        "a.md": text,
        "b.md": text.replace("100000000001", "100000000002").replace(
            "Example Silver PPO 3000", "Example Gold"
        ),
        "c.md": text.replace("100000000001", "100000000003"),
    }
    rows = []
    for name, body in plans.items():
        (md_dir / name).write_text(body, encoding="utf-8")
        status = "FAIL" if name == "c.md" else "PASS"
        rows.append({"markdown": str((md_dir / name).relative_to(root)), "status": status, "problems": "",
                     "needs_review": ""})  # fmt: skip
    report = root / "report.csv"
    with report.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["markdown", "status", "problems", "needs_review"])
        w.writeheader()
        w.writerows(rows)
    return root / "data" / "markdown" / "sbc" / "2027", report


@pytest.fixture
def bundle(tmp_path: Path) -> sqlite3.Connection:
    md_dir, report = _corpus(tmp_path)
    out = tmp_path / "facts.sqlite"
    stats = build_bundle(md_dir, report, out, "test.1", root=tmp_path)
    assert (stats.plans, stats.quarantined) == (2, 1)
    return sqlite3.connect(out)


def test_plans_and_quarantine(bundle: sqlite3.Connection) -> None:
    keys = [r[0] for r in bundle.execute("SELECT plan_key FROM plan ORDER BY plan_key")]
    assert keys == ["2027:100000000001", "2027:100000000002"]
    (reasons,) = bundle.execute("SELECT reasons FROM quarantine").fetchone()
    assert "verification status FAIL" in json.loads(reasons)[0]


def test_benefit_lookup_returns_verbatim_cost(bundle: sqlite3.Connection) -> None:
    row = bundle.execute(
        """SELECT c.verbatim, c.status, t.text FROM plan_benefit b JOIN plan p USING (plan_id)
           JOIN cost_string c USING (cost_id) JOIN text_string t ON t.text_id = b.limitations_id
           WHERE p.plan_key = ? AND b.service_id = 'specialist_visit' AND b.tier_id = 'in_net'""",
        ("2027:100000000001",),
    ).fetchone()
    assert row == ("$60/visit, deductible does not apply", "exact", "none")


def test_identical_chunks_are_stored_once(bundle: sqlite3.Connection) -> None:
    (unique,) = bundle.execute("SELECT COUNT(*) FROM chunk").fetchone()
    (links,) = bundle.execute("SELECT COUNT(*) FROM plan_chunk").fetchone()
    assert links == 2 * unique


def test_plan_name_trigram_search(bundle: sqlite3.Connection) -> None:
    hits = bundle.execute(
        "SELECT plan_key FROM plan_name_fts WHERE plan_name_fts MATCH ?", ('"Gold"',)
    ).fetchall()
    assert hits == [("2027:100000000002",)]


def test_meta_records_versions(bundle: sqlite3.Connection) -> None:
    meta = {k: json.loads(v) for k, v in bundle.execute("SELECT key, value FROM meta")}
    assert meta["bundle_version"] == "test.1"
    assert meta["plans"] == 2
    assert meta["review_allowlist_signed"] is False


def test_release_build_requires_signed_allowlist(tmp_path: Path) -> None:
    md_dir, report = _corpus(tmp_path)
    with pytest.raises(SystemExit, match="no sign-off"):
        build_bundle(md_dir, report, tmp_path / "x.sqlite", "test.2", root=tmp_path, release=True)
