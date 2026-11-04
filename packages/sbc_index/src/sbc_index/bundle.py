"""Build the facts bundle: one read-only SQLite file per release (PLAN.md sections 3.2.3 and 3.2.5).

It holds the structured facts the runtime reads exactly, the unique search chunks with their plan links and
BM25 term weights (``sbc_index.search_build``; ``sbc-embed-bundle`` adds the embeddings), a quarantine list of
plans left out and why, and each plan's PDF source from the portal manifest (``plan_source``): how the PDF is
fetched, the portal's preview-form fields and the PDF's sha256, which the API's PDF proxy needs for the SBCs
the portal serves only through its preview form (PLAN.md 3.7).
"""

from __future__ import annotations

import argparse
import csv
import dataclasses
import hashlib
import json
import sqlite3
import sys
from collections import Counter
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from sbc_core.taxonomy import load_taxonomy
from sbc_extract.costs import GRAMMAR_VERSION, CostParse
from sbc_extract.extract import Chunk, Extracted, extract
from sbc_extract.limits import LimitParse
from sbc_extract.review import load_allowlist
from sbc_index.puf_check import hios_ids
from sbc_index.search_build import PAGE_SIZE, write_bm25

SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
-- Repeated text (limitations, answers, list items, narrative, labels) is stored once and referenced by id.
CREATE TABLE text_string (text_id INTEGER PRIMARY KEY, text TEXT NOT NULL UNIQUE);
CREATE TABLE cost_string (
    cost_id INTEGER PRIMARY KEY, verbatim TEXT NOT NULL UNIQUE, status TEXT NOT NULL,
    parse_json TEXT NOT NULL, grammar_version INTEGER NOT NULL
);
CREATE TABLE limit_string (
    limit_id INTEGER PRIMARY KEY, kind TEXT NOT NULL, verbatim TEXT NOT NULL, why TEXT NOT NULL,
    status TEXT NOT NULL, parse_json TEXT NOT NULL, UNIQUE (kind, verbatim, why)
);
CREATE TABLE plan (
    plan_id INTEGER PRIMARY KEY, plan_key TEXT NOT NULL UNIQUE, plan_year INTEGER NOT NULL,
    sbc_id TEXT NOT NULL, contract_code TEXT NOT NULL, plan_name TEXT NOT NULL, issuer TEXT, plan_type TEXT,
    coverage_period TEXT, coverage_for TEXT, hios_ids TEXT, eoc_url TEXT, source_url TEXT, source_pdf TEXT,
    state TEXT, market TEXT, network TEXT, exchange TEXT, plan_status TEXT, brand TEXT,
    catalog_plan_name TEXT,
    catalog_json TEXT NOT NULL, metal_tier TEXT, metal_tier_source TEXT, verification_status TEXT NOT NULL,
    review_classes TEXT NOT NULL, chart_notes TEXT NOT NULL, md_sha1 TEXT NOT NULL
);
CREATE TABLE plan_question (
    plan_id INTEGER NOT NULL, question_id TEXT NOT NULL, answer_id INTEGER NOT NULL, why_id INTEGER NOT NULL,
    pages TEXT NOT NULL, PRIMARY KEY (plan_id, question_id)
) WITHOUT ROWID;
CREATE TABLE plan_limit (
    plan_id INTEGER NOT NULL, kind TEXT NOT NULL, limit_id INTEGER NOT NULL, PRIMARY KEY (plan_id, kind)
) WITHOUT ROWID;
CREATE TABLE plan_benefit (
    plan_id INTEGER NOT NULL, service_id TEXT NOT NULL, tier_id TEXT NOT NULL, row_order INTEGER NOT NULL,
    service_label_id INTEGER NOT NULL, tier_label_id INTEGER NOT NULL, cost_id INTEGER NOT NULL,
    limitations_id INTEGER NOT NULL, limitations_kind TEXT NOT NULL, carried_note INTEGER NOT NULL,
    event_inferred INTEGER NOT NULL, pages TEXT NOT NULL, chunk_id INTEGER NOT NULL,
    PRIMARY KEY (plan_id, service_id, tier_id)
) WITHOUT ROWID;
CREATE TABLE plan_list_item (
    plan_id INTEGER NOT NULL, list_id TEXT NOT NULL, position INTEGER NOT NULL, item_id INTEGER NOT NULL,
    pages TEXT NOT NULL, PRIMARY KEY (plan_id, list_id, position)
) WITHOUT ROWID;
CREATE TABLE plan_example (
    plan_id INTEGER NOT NULL, example_id TEXT NOT NULL, title_id INTEGER NOT NULL, total_cost TEXT NOT NULL,
    total_pay TEXT NOT NULL, data_id INTEGER NOT NULL, pages TEXT NOT NULL, PRIMARY KEY (plan_id, example_id)
) WITHOUT ROWID;
CREATE TABLE plan_narrative (
    plan_id INTEGER NOT NULL, title TEXT NOT NULL, text_id INTEGER NOT NULL, pages TEXT NOT NULL,
    PRIMARY KEY (plan_id, title)
) WITHOUT ROWID;
CREATE TABLE chunk (
    chunk_id INTEGER PRIMARY KEY, chunk_hash TEXT NOT NULL UNIQUE, chunk_type TEXT NOT NULL,
    section_path TEXT NOT NULL, text TEXT NOT NULL
);
CREATE TABLE plan_chunk (
    plan_id INTEGER NOT NULL, position INTEGER NOT NULL, chunk_id INTEGER NOT NULL, pages TEXT NOT NULL,
    PRIMARY KEY (plan_id, position)
) WITHOUT ROWID;
CREATE TABLE quarantine (markdown TEXT PRIMARY KEY, plan_key TEXT, reasons TEXT NOT NULL);
-- Each plan's PDF as the portal manifest records it (sbc-portal catalog + manifest). fetch_method 'deeplink'
-- has a public PDF URL; 'preview_post' is served only by the portal's previewDocument form, whose fields
-- are kept here so the API can fetch the PDF on request and check it against pdf_sha256.
CREATE TABLE plan_source (
    plan_id INTEGER PRIMARY KEY, fetch_method TEXT NOT NULL, portal TEXT NOT NULL, catalog_url TEXT NOT NULL,
    state TEXT NOT NULL, market_unit TEXT NOT NULL, plan_name TEXT NOT NULL, plan_type TEXT NOT NULL,
    coverage_period TEXT NOT NULL, network TEXT NOT NULL, language TEXT NOT NULL, plan_status TEXT NOT NULL,
    doc_sys_id TEXT NOT NULL, dps_unique_id TEXT NOT NULL, brand TEXT NOT NULL, source_system TEXT NOT NULL,
    pdf_sha256 TEXT NOT NULL, pdf_bytes INTEGER NOT NULL
);
-- Exact plan lookup by the IDs a user can type: contract code, SBC id, HIOS plan id with its -NN variant
-- ("hios") and without it ("hios_base", which can match several variants).
CREATE TABLE plan_identifier (
    kind TEXT NOT NULL, value TEXT NOT NULL, plan_id INTEGER NOT NULL, PRIMARY KEY (kind, value, plan_id)
) WITHOUT ROWID;
CREATE VIRTUAL TABLE plan_name_fts USING fts5(
    plan_key UNINDEXED, plan_name, catalog_plan_name, contract_code, tokenize = 'trigram'
);
CREATE INDEX plan_scope ON plan (plan_year, state, market);
CREATE INDEX benefit_service ON plan_benefit (service_id, tier_id);
CREATE INDEX plan_chunk_by_chunk ON plan_chunk (chunk_id);
"""


@dataclasses.dataclass
class BuildStats:
    plans: int = 0
    quarantined: int = 0
    benefits: int = 0
    chunks: int = 0
    plan_chunks: int = 0
    sources: int = 0
    preview_only: int = 0
    sources_missing: int = 0
    cost_status: Counter[str] = dataclasses.field(default_factory=Counter)
    cost_cells: Counter[str] = dataclasses.field(default_factory=Counter)
    quarantine_reasons: Counter[str] = dataclasses.field(default_factory=Counter)


def _pages(pages: Iterable[int]) -> str:
    return ",".join(str(p) for p in pages)


def _identifiers(
    contract_code: str, sbc_id: str, sbc_hios: str, catalog: dict[str, str]
) -> list[tuple[str, str]]:
    ids = [("contract_code", contract_code), ("sbc_id", sbc_id)]
    for hios in hios_ids(sbc_hios, json.dumps(catalog)):
        ids += [("hios", hios), ("hios_base", hios.split("-")[0])]
    return [(kind, value) for kind, value in ids if value]


SOURCE_FIELDS = (
    "fetch_method", "portal", "url", "state", "market_unit", "plan_name", "plan_type", "coverage_period",
    "network", "language", "plan_status", "doc_sys_id", "dps_unique_id", "brand", "source_system", "sha256",
    "bytes",
)  # fmt: skip
"""Manifest columns copied into ``plan_source``, in its column order (``url`` is stored as ``catalog_url``,
``sha256`` as ``pdf_sha256``)."""
_SHA256_HEX = frozenset("0123456789abcdef")


def load_manifest(path: Path) -> dict[str, dict[str, str]]:
    """The ``sbc-portal manifest`` CSV by ``sbc_id``; rows without a downloaded, hashed PDF are left out."""
    with path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    missing = {"sbc_id", "downloaded", *SOURCE_FIELDS} - set(rows[0] if rows else {})
    if missing:
        raise SystemExit(f"{path}: not an sbc-portal manifest (missing columns: {sorted(missing)})")
    out: dict[str, dict[str, str]] = {}
    for r in rows:
        sha = r["sha256"].strip().lower()
        if r["downloaded"].strip().lower() == "true" and len(sha) == 64 and set(sha) <= _SHA256_HEX:
            out[r["sbc_id"]] = {**r, "sha256": sha}
    return out


# Fixed placeholder lists, not user input.
_INSERT_PLAN = "INSERT INTO plan VALUES (NULL," + ",".join("?" * 27) + ")"  # noqa: S608
_INSERT_BENEFIT = "INSERT INTO plan_benefit VALUES (" + ",".join("?" * 13) + ")"  # noqa: S608
_INSERT_SOURCE = "INSERT INTO plan_source VALUES (?," + ",".join("?" * len(SOURCE_FIELDS)) + ")"  # noqa: S608


class _Writer:
    def __init__(self, db: sqlite3.Connection, manifest: dict[str, dict[str, str]] | None = None) -> None:
        self.db = db
        self.ids: dict[tuple[str, ...], int] = {}
        self.manifest = manifest

    def _intern(self, key: tuple[str, ...], sql: str, params: tuple[Any, ...]) -> int:
        if key not in self.ids:
            self.ids[key] = int(self.db.execute(sql, params).lastrowid or 0)
        return self.ids[key]

    def text(self, value: str) -> int:
        return self._intern(("text", value), "INSERT INTO text_string (text) VALUES (?)", (value,))

    def cost(self, verbatim: str, parsed: CostParse, stats: BuildStats) -> int:
        if ("cost", verbatim) not in self.ids:
            stats.cost_status[parsed.status] += 1
        return self._intern(
            ("cost", verbatim),
            "INSERT INTO cost_string (verbatim, status, parse_json, grammar_version) VALUES (?, ?, ?, ?)",
            (verbatim, parsed.status, parsed.model_dump_json(), parsed.grammar_version),
        )

    def limit(self, kind: str, parsed: LimitParse, why: str) -> int:
        return self._intern(
            ("limit", kind, parsed.verbatim, why),
            "INSERT INTO limit_string (kind, verbatim, why, status, parse_json) VALUES (?, ?, ?, ?, ?)",
            (kind, parsed.verbatim, why, parsed.status, parsed.model_dump_json()),
        )

    def chunk(self, ch: Chunk) -> int:
        return self._intern(
            ("chunk", ch.chunk_hash),
            "INSERT INTO chunk (chunk_hash, chunk_type, section_path, text) VALUES (?, ?, ?, ?)",
            (ch.chunk_hash, ch.chunk_type, ch.section_path, ch.text),
        )

    def add(self, e: Extracted, stats: BuildStats) -> None:
        plan = e.plan
        assert plan is not None  # noqa: S101 - callers pass extractable plans only
        c = plan.catalog
        plan_id = int(
            self.db.execute(
                _INSERT_PLAN,
                (
                    plan.plan_key, plan.plan_year, plan.sbc_id, plan.contract_code, plan.plan_name,
                    plan.issuer, plan.plan_type, plan.coverage_period, plan.coverage_for, plan.hios_ids,
                    plan.eoc_url,
                    plan.source_url, plan.source_pdf, c.get("state"), c.get("market"), c.get("network"),
                    c.get("exchange"), c.get("plan_status"), c.get("brand"), c.get("plan_name"),
                    json.dumps(c, sort_keys=True), plan.metal_tier, plan.metal_tier_source,
                    plan.verification_status, json.dumps(plan.review_classes), json.dumps(plan.chart_notes),
                    plan.md_sha1,
                ),
            ).lastrowid
            or 0
        )  # fmt: skip
        self.db.execute(
            "INSERT INTO plan_name_fts VALUES (?, ?, ?, ?)",
            (plan.plan_key, plan.plan_name, c.get("plan_name", ""), plan.contract_code),
        )
        ids = _identifiers(plan.contract_code, plan.sbc_id, plan.hios_ids, c)
        self.db.executemany(
            "INSERT OR IGNORE INTO plan_identifier VALUES (?, ?, ?)",
            [(kind, value, plan_id) for kind, value in ids],
        )
        self.db.executemany(
            "INSERT INTO plan_question VALUES (?, ?, ?, ?, ?)",
            [(plan_id, q.question_id, self.text(q.answer_verbatim), self.text(q.why_verbatim),
              _pages(q.pages)) for q in e.questions],
        )  # fmt: skip
        whys = {"deductible": "overall_deductible", "oop": "oop_limit"}
        why_text = {q.question_id: q.why_verbatim for q in e.questions}
        self.db.executemany(
            "INSERT INTO plan_limit VALUES (?, ?, ?)",
            [(plan_id, kind, self.limit(kind, p, why_text[whys[kind]])) for kind, p in e.limits.items()],
        )
        chunk_ids = {h: self.chunk(ch) for h, ch in e.chunks.items()}
        rows = []
        for b in e.benefits:
            parsed = e.costs[b.cost_verbatim]
            stats.cost_cells[parsed.status] += 1
            rows.append((
                plan_id, b.service_id, b.tier_id, b.row_order, self.text(b.service_label),
                self.text(b.tier_label), self.cost(b.cost_verbatim, parsed, stats),
                self.text(b.limitations_verbatim), b.limitations_kind, int(b.carried_note),
                int(b.event_inferred), _pages(b.pages), chunk_ids[b.chunk_hash],
            ))  # fmt: skip
        self.db.executemany(_INSERT_BENEFIT, rows)
        self.db.executemany(
            "INSERT INTO plan_list_item VALUES (?, ?, ?, ?, ?)",
            [
                (plan_id, i.list_id, i.position, self.text(i.item_verbatim), _pages(i.pages))
                for i in e.list_items
            ],
        )
        self.db.executemany(
            "INSERT INTO plan_example VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(plan_id, x.example_id, self.text(x.title), x.total_cost, x.total_pay,
              self.text(json.dumps({"subtitle": x.subtitle, "plan_terms": x.plan_terms,
                                    "services": x.services, "cost_sharing": x.cost_sharing,
                                    "not_covered": x.not_covered,
                                    "total_pay_label": x.total_pay_label})),
              _pages(x.pages)) for x in e.examples],
        )  # fmt: skip
        self.db.executemany(
            "INSERT INTO plan_narrative VALUES (?, ?, ?, ?)",
            [(plan_id, title, self.text(text), _pages(pages)) for title, text, pages in e.narrative],
        )
        self.db.executemany(
            "INSERT INTO plan_chunk VALUES (?, ?, ?, ?)",
            [(plan_id, pc.position, chunk_ids[pc.chunk_hash], _pages(pc.pages)) for pc in e.plan_chunks],
        )
        self._source(plan_id, plan.sbc_id, stats)
        stats.plans += 1
        stats.benefits += len(e.benefits)
        stats.chunks = sum(1 for k in self.ids if k[0] == "chunk")
        stats.plan_chunks += len(e.plan_chunks)

    def _source(self, plan_id: int, sbc_id: str, stats: BuildStats) -> None:
        if self.manifest is None:
            return
        row = self.manifest.get(sbc_id)
        if row is None:
            stats.sources_missing += 1
            return
        values = [(row.get(k) or "").strip() for k in SOURCE_FIELDS]
        values[-1] = str(int(values[-1] or 0))
        self.db.execute(_INSERT_SOURCE, (plan_id, *values))
        stats.sources += 1
        stats.preview_only += row["fetch_method"] == "preview_post"


def build_bundle(
    markdown_dir: Path,
    report_csv: Path,
    out: Path,
    version: str,
    *,
    root: Path,
    release: bool = False,
    manifest_csv: Path | None = None,
) -> BuildStats:
    """Extract every Markdown file under ``markdown_dir`` and write the bundle to ``out``.

    With ``manifest_csv`` (the ``sbc-portal manifest`` output), every plan gets its ``plan_source`` row; a
    release build requires the manifest and refuses a bundle with any plan missing from it."""
    allowlist = load_allowlist()
    if release and not allowlist.signed:
        raise SystemExit(
            "release build refused: the REVIEW allowlist has no sign-off (data/review_allowlist.yaml)"
        )
    if release and manifest_csv is None:
        raise SystemExit("release build refused: the portal manifest is required (--manifest)")
    manifest = load_manifest(manifest_csv) if manifest_csv is not None else None
    with report_csv.open(newline="", encoding="utf-8") as f:
        report = {r["markdown"]: r for r in csv.DictReader(f)}
    files = sorted(markdown_dir.rglob("*.md"))
    if not files:
        raise SystemExit(f"no Markdown files under {markdown_dir}")
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    db = sqlite3.connect(tmp)
    db.executescript(SCHEMA)
    writer, stats = _Writer(db, manifest), BuildStats()
    manifest_hash = hashlib.sha256()
    for path in files:
        rel = str(path.relative_to(root))
        text = path.read_text(encoding="utf-8")
        manifest_hash.update(f"{rel}\0{hashlib.sha1(text.encode()).hexdigest()}\n".encode())  # noqa: S324
        e = extract(text, report.get(rel, {}))
        if e.ok:
            writer.add(e, stats)
        else:
            stats.quarantined += 1
            for reason in e.quarantine_reasons:
                stats.quarantine_reasons[reason.split(":")[0]] += 1
            db.execute(
                "INSERT INTO quarantine VALUES (?, ?, ?)",
                (rel, e.plan.plan_key if e.plan else None, json.dumps(e.quarantine_reasons)),
            )
    if release and stats.sources_missing:
        db.close()
        tmp.unlink(missing_ok=True)
        raise SystemExit(
            f"release build refused: {stats.sources_missing} plans are missing from the manifest"
        )
    bm25 = write_bm25(db)
    meta: dict[str, Any] = {
        "bundle_version": version,
        "source_manifest_sha256": manifest_hash.hexdigest(),
        "grammar_version": GRAMMAR_VERSION,
        "taxonomy_version": load_taxonomy().version,
        "review_allowlist_signed": allowlist.signed,
        "plans": stats.plans,
        "quarantined": stats.quarantined,
        "unique_chunks": stats.chunks,
        "bm25": bm25,
        "plan_source": {
            "rows": stats.sources, "preview_only": stats.preview_only, "missing": stats.sources_missing,
        } if manifest is not None else None,
    }  # fmt: skip
    db.executemany("INSERT INTO meta VALUES (?, ?)", [(k, json.dumps(v)) for k, v in meta.items()])
    db.commit()
    db.execute(f"PRAGMA page_size = {PAGE_SIZE}")
    db.execute("VACUUM")
    db.close()
    tmp.replace(out)
    return stats


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Build the SBC facts bundle (SQLite).")
    ap.add_argument("--year", default="2027")
    ap.add_argument("--version", required=True, help="bundle version, e.g. 2027.1")
    ap.add_argument("--out", type=Path, help="default: bundles/facts-<version>.sqlite")
    ap.add_argument(
        "--release", action="store_true", help="require a signed REVIEW allowlist and the manifest"
    )
    ap.add_argument(
        "--manifest", type=Path,
        help="sbc-portal manifest CSV for plan_source (default: data/catalog/sbc_manifest_<year>.csv)",
    )  # fmt: skip
    args = ap.parse_args(argv)
    root = Path.cwd()
    md_dir = root / "data" / "markdown" / "sbc" / args.year
    report = root / "data" / "markdown" / f"verification_report_{args.year}.csv"
    out = args.out or root / "bundles" / f"facts-{args.version}.sqlite"
    manifest = args.manifest
    if manifest is None:
        default = root / "data" / "catalog" / f"sbc_manifest_{args.year}.csv"
        manifest = default if default.is_file() else None
        if manifest is None:
            print(f"no manifest at {default}: plan_source stays empty (no PDF proxy data)", file=sys.stderr)
    stats = build_bundle(
        md_dir, report, out, args.version, root=root, release=args.release, manifest_csv=manifest
    )
    cells = sum(stats.cost_cells.values())
    exact = stats.cost_cells["exact"] / cells if cells else 0.0
    print(f"{out} ({out.stat().st_size / 1e6:.0f} MB)", file=sys.stderr)
    print(
        f"plans {stats.plans}, quarantined {stats.quarantined} {dict(stats.quarantine_reasons)}",
        file=sys.stderr,
    )
    print(
        f"benefit rows {stats.benefits}, cost cells exact {exact:.2%} {dict(stats.cost_cells)}",
        file=sys.stderr,
    )
    print(f"unique chunks {stats.chunks}, plan-chunk links {stats.plan_chunks}", file=sys.stderr)
    if manifest is not None:
        print(
            f"plan sources {stats.sources} ({stats.preview_only} preview-only), "
            f"missing {stats.sources_missing}",
            file=sys.stderr,
        )
