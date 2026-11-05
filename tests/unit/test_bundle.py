"""Facts bundle builder on synthetic SBCs."""

from __future__ import annotations

import csv
import dataclasses
import json
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import closing
from pathlib import Path

import pytest

from sbc_extract.review import load_allowlist
from sbc_index.bundle import SOURCE_FIELDS, build_bundle

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "sbc_minimal.md"


def _corpus(root: Path, edit: Callable[[str, str], str] | None = None) -> tuple[Path, Path]:
    """Three synthetic SBCs (c.md fails verification). ``edit(name, text)`` can change a file first."""
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
        body = edit(name, body) if edit else body
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


SILVER_SHA = "a" * 64
GOLD_SHA = "b" * 64


def manifest_rows() -> list[dict[str, str]]:
    """Manifest rows for the corpus: the Silver plan is preview-only, Gold has a public deep link."""
    base = {
        "source": "portal", "portal": "example", "brand": "Example", "state": "XX",
        "market_unit": "Individual", "plan_type": "PPO", "network": "Example Network", "exchange": "On",
        "plan_status": "Active", "rx_rider": "", "coverage_period": "01/01/2027 - 12/31/2027",
        "hios_plan_ids": "", "language": "English", "local_path": "", "downloaded": "True", "bytes": "123456",
        "contract_code": "ZZ01",
    }  # fmt: skip
    silver = {
        "plan_name": "Example Silver PPO 3000", "sbc_id": "100000000001", "doc_sys_id": "D1",
        "dps_unique_id": "U1", "source_system": "EPDG", "fetch_method": "preview_post",
        "url": "https://portal.example/dps/", "sha256": SILVER_SHA.upper(),
    }  # fmt: skip
    gold = {
        "plan_name": "Example Gold", "sbc_id": "100000000002", "doc_sys_id": "D2", "dps_unique_id": "U2",
        "source_system": "DOCGEN", "fetch_method": "deeplink", "sha256": GOLD_SHA,
        "url": "https://portal.example/dpsdeeplink/deepLink/x/ZZ01/English/DG100000000002.pdf",
    }  # fmt: skip
    return [{**base, **silver}, {**base, **gold}]


def write_manifest(root: Path, rows: list[dict[str, str]] | None = None) -> Path:
    """Write a ``sbc-portal manifest`` CSV (default: :func:`manifest_rows`) under ``root``."""
    rows = manifest_rows() if rows is None else rows
    path = root / "manifest.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(dict.fromkeys(["sbc_id", *SOURCE_FIELDS, *rows[0]])))
        w.writeheader()
        w.writerows(rows)
    return path


def make_bundle(
    root: Path,
    edit: Callable[[str, str], str] | None = None,
    *,
    manifest: bool = True,
    rows: list[dict[str, str]] | None = None,
) -> Path:
    """Build a two-plan test bundle under ``root`` and return its path (with ``plan_source`` rows from
    ``rows``, default :func:`manifest_rows`, unless ``manifest`` is False)."""
    md_dir, report = _corpus(root, edit)
    out = root / "facts.sqlite"
    stats = build_bundle(
        md_dir,
        report,
        out,
        "test.1",
        root=root,
        manifest_csv=write_manifest(root, rows) if manifest else None,
    )
    assert (stats.plans, stats.quarantined) == (2, 1)
    return out


@pytest.fixture
def bundle(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    with closing(sqlite3.connect(make_bundle(tmp_path))) as db:
        yield db


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
    assert meta["review_allowlist_signed"] is True


def test_release_build_requires_signed_allowlist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    unsigned = dataclasses.replace(load_allowlist(), signed_off_by=None, signed_off_on=None)
    monkeypatch.setattr("sbc_index.bundle.load_allowlist", lambda: unsigned)
    md_dir, report = _corpus(tmp_path)
    with pytest.raises(SystemExit, match="no sign-off"):
        build_bundle(md_dir, report, tmp_path / "x.sqlite", "test.2", root=tmp_path, release=True)


# ------------------------------------------------------------------ plan_source (the PDF proxy's data)


def test_every_plan_gets_its_pdf_source(bundle: sqlite3.Connection) -> None:
    rows = bundle.execute(
        "SELECT p.plan_key, s.fetch_method, s.doc_sys_id, s.source_system, s.pdf_sha256, s.pdf_bytes"
        " FROM plan_source s JOIN plan p USING (plan_id) ORDER BY p.plan_key"
    ).fetchall()
    assert rows == [
        (
            "2027:100000000001",
            "preview_post",
            "D1",
            "EPDG",
            "a" * 64,
            123456,
        ),  # hash normalized to lower case
        ("2027:100000000002", "deeplink", "D2", "DOCGEN", "b" * 64, 123456),
    ]
    meta = {k: json.loads(v) for k, v in bundle.execute("SELECT key, value FROM meta")}
    assert meta["plan_source"] == {"rows": 2, "preview_only": 1, "missing": 0}


def test_without_a_manifest_the_table_is_empty(tmp_path: Path) -> None:
    with closing(sqlite3.connect(make_bundle(tmp_path, manifest=False))) as db:
        assert db.execute("SELECT count(*) FROM plan_source").fetchone() == (0,)
        meta = {k: json.loads(v) for k, v in db.execute("SELECT key, value FROM meta")}
    assert meta["plan_source"] is None


def _with_gold(**changes: str) -> list[dict[str, str]]:
    rows = manifest_rows()
    rows[1].update(changes)
    return rows


def test_undownloaded_or_unhashed_rows_count_as_missing(tmp_path: Path) -> None:
    md_dir, report = _corpus(tmp_path)
    manifest = write_manifest(tmp_path, _with_gold(downloaded="False"))
    stats = build_bundle(md_dir, report, tmp_path / "x.sqlite", "t", root=tmp_path, manifest_csv=manifest)
    assert (stats.sources, stats.sources_missing) == (1, 1)
    manifest = write_manifest(tmp_path, _with_gold(sha256="not-a-hash"))
    stats = build_bundle(md_dir, report, tmp_path / "y.sqlite", "t", root=tmp_path, manifest_csv=manifest)
    assert stats.sources_missing == 1


def test_release_build_requires_a_complete_manifest(tmp_path: Path) -> None:
    md_dir, report = _corpus(tmp_path)
    with pytest.raises(SystemExit, match="manifest is required"):
        build_bundle(md_dir, report, tmp_path / "x.sqlite", "t", root=tmp_path, release=True)
    partial = write_manifest(tmp_path, _with_gold(downloaded="False"))
    with pytest.raises(SystemExit, match="1 plans are missing"):
        build_bundle(
            md_dir, report, tmp_path / "x.sqlite", "t", root=tmp_path, release=True, manifest_csv=partial
        )
    assert not (tmp_path / "x.sqlite").exists()
    assert not (tmp_path / "x.tmp").exists()
    stats = build_bundle(
        md_dir,
        report,
        tmp_path / "x.sqlite",
        "t",
        root=tmp_path,
        release=True,
        manifest_csv=write_manifest(tmp_path),
    )
    assert stats.sources == 2


def test_a_file_that_is_not_a_manifest_is_rejected(tmp_path: Path) -> None:
    md_dir, report = _corpus(tmp_path)
    bad = tmp_path / "bad.csv"
    bad.write_text("sbc_id,url\n100000000001,https://x\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="not an sbc-portal manifest"):
        build_bundle(md_dir, report, tmp_path / "x.sqlite", "t", root=tmp_path, manifest_csv=bad)


def test_the_shipped_allowlist_is_signed() -> None:
    """The sign-off date sits under a quoted "on" key: YAML 1.1 reads a bare `on` as true, which hid it."""
    allowlist = load_allowlist()
    assert allowlist.signed
    assert (allowlist.signed_off_by, allowlist.signed_off_on) == ("Syamsruthin Kaliki", "2026-11-02")
