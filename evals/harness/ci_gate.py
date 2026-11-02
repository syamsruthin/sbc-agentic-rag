"""Deterministic eval gate for CI (PLAN.md section 5.5). Needs no API keys and no SBC data.

1. Every committed golden dataset loads, matches its directory's version, and is internally consistent.
2. The retrieval eval runs in its ``bm25_only`` setup on the synthetic fixture bundle, with one generated item
   per (plan, chunk) plus look-alike-plan items that must clarify. Hard gates (plan filter 100%, no confident
   wrong-plan resolution) must pass.

The full eval on the real corpus needs the facts bundle, which CI doesn't have; run it locally with
``python -m evals.harness.retrieval`` (data/README.md).

Usage: ``uv run python -m evals.harness.ci_gate``
"""

from __future__ import annotations

import re
import sys
import tempfile
from collections import Counter
from pathlib import Path

from evals.harness.golden import GoldenItem, load_golden
from evals.harness.retrieval import RunReport, Scopes, bm25_searcher, run
from sbc_core.config import BotName, load_config
from sbc_core.facts import SqliteFactsReader

REPO = Path(__file__).resolve().parents[2]
GOLDEN_ROOT = REPO / "evals" / "golden"
_CHUNK_HASH_RE = re.compile(r"^[0-9a-f]{20}$")
_PLAN_KEY_RE = re.compile(r"^20\d\d:\d{12}$")
_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")


def validate_golden(root: Path = GOLDEN_ROOT) -> tuple[dict[str, Counter[str]], list[str]]:
    """Category counts per dataset version, and problems found."""
    problems: list[str] = []
    counts: dict[str, Counter[str]] = {}
    for directory in sorted(p for p in root.iterdir() if p.is_dir()):
        if not _SEMVER_RE.match(directory.name):
            problems.append(f"{directory.name}: golden directories are named by semantic version")
            continue
        try:
            items = load_golden(directory)
        except ValueError as exc:
            problems.append(f"{directory.name}: {exc}")
            continue
        counts[directory.name] = Counter(i.category for i in items)
        for item in items:
            problems += [f"{directory.name}/{item.id}: {p}" for p in item_problems(item, directory.name)]
    return counts, problems


def item_problems(item: GoldenItem, version: str) -> list[str]:
    out = []
    if item.dataset_version != version:
        out.append(f"dataset_version {item.dataset_version} in directory {version}")
    out += [f"bad chunk hash {h!r}" for h in item.gold.chunk_hashes if not _CHUNK_HASH_RE.match(h)]
    out += [f"bad plan key {k!r}" for k in item.gold.plan_keys if not _PLAN_KEY_RE.match(k)]
    p = item.provenance
    if p.verified_against == "pdf" and not (p.reviewed_by and p.reviewed_at):
        out.append("verified against the PDF but no reviewer and date")
    if p.verified_against == "pending" and p.reviewed_by:
        out.append("has a reviewer but is still pending")
    if item.gold.expected_values and not p.pdf_pages:
        out.append("expected values without PDF page references")
    return out


def synthetic_items(bundle: Path) -> list[GoldenItem]:
    """One item per (plan, searchable chunk) of a bundle, asked with the plan selected in the UI, plus an
    item naming a contract code the fixture's two plans share (must clarify)."""
    from sbc_index.search_build import read_chunks

    _, records = read_chunks(bundle)
    items: list[GoldenItem] = []

    def add(**fields: object) -> None:
        items.append(
            GoldenItem.model_validate(
                {
                    "id": f"gs-{len(items) + 1:06d}",
                    "dataset_version": "0.0.0",
                    "bot": "broker",
                    "difficulty": 1,
                    "provenance": {"method": "synthetic", "author": "ci_gate", "verified_against": "pending"},
                    **fields,
                }
            )
        )

    for record in records:
        if record.chunk_type not in ("service_row", "iq", "list"):
            continue
        topic = record.section_path.rsplit(" > ", 1)[-1]
        for plan_key in record.plan_keys:
            add(
                category="exclusions_limitations",
                turns=[{"role": "user", "content": f"What does the SBC say about {topic}?"}],
                context={"ui_selected_plan_keys": [plan_key]},
                gold={
                    "plan_keys": [plan_key],
                    "expected_behavior": "answer",
                    "sections": [record.section_path],
                    "chunk_hashes": [record.chunk_hash],
                    "reference_answer": "synthetic",
                },
            )
    add(
        category="ambiguous",
        turns=[{"role": "user", "content": "What's the deductible on ZZ01?"}],
        gold={"expected_behavior": "clarify"},
    )
    add(
        category="ambiguous",
        turns=[{"role": "user", "content": "What's the deductible on Example?"}],
        context={"plan_mentions": ["Example"]},
        gold={"expected_behavior": "clarify"},
    )
    return items


def fixture_eval(tmp: Path) -> RunReport:
    from tests.unit.test_bundle import make_bundle

    bundle = make_bundle(tmp)
    settings = load_config(BotName.BROKER, "local", REPO / "config").retrieval
    with SqliteFactsReader(bundle) as reader:
        return run(
            synthetic_items(bundle),
            reader,
            bm25_searcher(bundle),
            settings,
            setup="bm25_only",
            scopes=Scopes.from_config(REPO / "config"),
            run_id="ci-fixture",
        )


def main() -> int:
    counts, problems = validate_golden()
    for version, by_category in counts.items():
        print(f"golden {version}: {sum(by_category.values())} items {dict(sorted(by_category.items()))}")
    with tempfile.TemporaryDirectory() as tmp:
        report = fixture_eval(Path(tmp))
    s = report.summary
    print(
        f"fixture eval ({s['items']} items): plan filter {s['plan_filter']['correctness']} "
        f"({s['plan_filter']['hits']} hits), resolution {s['resolution']['accuracy']}, confident wrong-plan "
        f"{s['resolution']['confident_wrong_plan']}, service mapping {s['service_mapping']['accuracy']}, "
        f"recall@5 {s['retrieval']['recall_at_5']}"
    )
    problems += [f"fixture eval hard gate failed: {g}" for g in report.hard_gates_failed]
    if s["plan_filter"]["hits"] == 0:
        problems.append("fixture eval ran no searches")
    for problem in problems:
        print(f"FAIL {problem}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
