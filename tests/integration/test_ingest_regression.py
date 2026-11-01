"""Regression guard for the SBC converter and verifier.

Re-converts a fixed sample of local PDFs into a scratch project root and requires the Markdown to be
byte-identical to the committed corpus output in ``data/markdown``, and the verifier verdicts to match
``data/markdown/verification_report_2027.csv``. Needs the fetched data (see data/README.md), so it is skipped
in CI and wherever ``data/raw`` is absent.
"""

from __future__ import annotations

import csv
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.conftest import REPO_ROOT

DATA = REPO_ROOT / "data"
CATALOG = DATA / "catalog" / "sbc_catalog_2027.csv"
REPORT = DATA / "markdown" / "verification_report_2027.csv"
SAMPLE_SIZE = 12

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not (DATA / "raw").is_dir() or not CATALOG.is_file(), reason="SBC data not fetched"),
]


def _sample() -> list[dict[str, str]]:
    with CATALOG.open(newline="", encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if (REPO_ROOT / r["local_path"]).is_file()]
    rows.sort(key=lambda r: r["sbc_id"])
    step = max(1, len(rows) // SAMPLE_SIZE)
    return rows[::step][:SAMPLE_SIZE]


def _run(tool: str, args: list[str], root: Path) -> subprocess.CompletedProcess[str]:
    exe = Path(sys.executable).with_name(tool)
    env = {**os.environ, "SBC_PROJECT_ROOT": str(root)}
    return subprocess.run(  # noqa: S603 - fixed local console script
        [str(exe), *args], cwd=root, env=env, capture_output=True, text=True, check=True
    )


@pytest.fixture(scope="module")
def scratch_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("ingest")
    (root / "data" / "catalog").mkdir(parents=True)
    shutil.copy2(CATALOG, root / "data" / "catalog" / CATALOG.name)
    for row in _sample():
        dest = root / row["local_path"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO_ROOT / row["local_path"], dest)
    return root


def test_converter_output_is_byte_identical(scratch_root: Path) -> None:
    pdfs = sorted(str(p) for p in (scratch_root / "data" / "raw").rglob("*.pdf"))
    _run("sbc-to-markdown", ["--force", "--workers", "4", *pdfs], scratch_root)
    for pdf in pdfs:
        rel = Path(pdf).relative_to(scratch_root / "data" / "raw").with_suffix(".md")
        new = (scratch_root / "data" / "markdown" / rel).read_bytes()
        assert new == (DATA / "markdown" / rel).read_bytes(), rel


def test_verifier_verdicts_match_report(scratch_root: Path) -> None:
    mds = sorted(str(p.relative_to(scratch_root)) for p in (scratch_root / "data" / "markdown").rglob("*.md"))
    assert mds, "run after the converter test"
    out = _run("sbc-verify", ["--workers", "4", *mds], scratch_root).stdout
    with REPORT.open(newline="", encoding="utf-8") as f:
        expected = {r["markdown"]: r for r in csv.DictReader(f)}
    for md in mds:
        block = out.split(f"{md}: ", 1)[1].split("\ndata/markdown/", 1)[0]
        ok = block.startswith("OK")
        lines = [line.strip() for line in block.splitlines()]
        review = [line.removeprefix("review: ") for line in lines if line.startswith("review:")]
        status = "FAIL" if not ok else ("REVIEW" if review else "PASS")
        assert status == expected[md]["status"], md
        assert " | ".join(review) == expected[md]["needs_review"], md
