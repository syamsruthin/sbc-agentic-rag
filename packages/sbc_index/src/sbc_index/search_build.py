"""Write the search index into the facts bundle (PLAN.md sections 3.2.4 and 3.2.5).

The bundle holds the search index next to the facts, read at runtime by ``sbc_core.search.SqliteSearchIndex``:

- :func:`write_bm25` runs inside ``sbc-build-bundle``: BM25 term weights for every unique chunk (local
  tokenizer, ``sbc_core.bm25``) and the document frequency of every term. No API keys.
- :func:`embed_bundle` (``sbc-embed-bundle``) adds one embedding per unique chunk through the embedding
  cache, so only chunk text the cache hasn't seen is sent to OpenRouter. The bundle records the model and
  dimensions, and the API refuses a bundle embedded with a model other than the configured one.

After embedding, :func:`validate` checks the vector count and runs plan-filtered queries for a sample of
plans: each must find its probe chunk near the top and return only that plan's chunks.
"""

from __future__ import annotations

import argparse
import json
import random
import sqlite3
import sys
from collections import Counter
from collections.abc import Callable, Sequence
from contextlib import closing
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from sbc_core.bm25 import Bm25Encoder
from sbc_core.config import Env, EnvSettings, load_config
from sbc_core.llm import OpenRouterClient
from sbc_core.search import PlanFilterViolationError, SqliteSearchIndex, pack_dense, pack_sparse
from sbc_core.types import SearchQuery
from sbc_index.embed_cache import Embedder, EmbeddingCache

SEARCH_SCHEMA = """
CREATE TABLE chunk_bm25 (chunk_id INTEGER PRIMARY KEY, terms BLOB NOT NULL, weights BLOB NOT NULL);
CREATE TABLE bm25_df (term_id INTEGER PRIMARY KEY, df INTEGER NOT NULL);
CREATE TABLE chunk_vector (chunk_id INTEGER PRIMARY KEY, vector BLOB NOT NULL);
"""
PROBE_RANK = 3


class SearchBuildError(Exception):
    """The search index could not be written or failed validation."""


@dataclass(frozen=True)
class ChunkRecord:
    chunk_hash: str
    chunk_type: str
    section_path: str
    text: str
    plan_keys: tuple[str, ...]


def read_chunks(bundle: Path) -> tuple[str, list[ChunkRecord]]:
    """The bundle version and every unique chunk with the plans that contain it."""
    with closing(sqlite3.connect(f"{bundle.resolve().as_uri()}?mode=ro", uri=True)) as db:
        version = json.loads(db.execute("SELECT value FROM meta WHERE key = 'bundle_version'").fetchone()[0])
        rows = db.execute(
            """SELECT c.chunk_hash, c.chunk_type, c.section_path, c.text, p.plan_key
               FROM chunk c JOIN plan_chunk pc ON pc.chunk_id = c.chunk_id
               JOIN plan p ON p.plan_id = pc.plan_id
               ORDER BY c.chunk_id, p.plan_key"""
        ).fetchall()
    grouped: dict[str, tuple[tuple[str, str, str], set[str]]] = {}
    for chunk_hash, chunk_type, path, text, plan_key in rows:
        grouped.setdefault(chunk_hash, ((chunk_type, path, text), set()))[1].add(plan_key)
    records = [ChunkRecord(h, *meta, tuple(sorted(keys))) for h, (meta, keys) in grouped.items()]
    return str(version), records


def write_bm25(db: sqlite3.Connection) -> dict[str, Any]:
    """Create the search tables and fill the BM25 part. Returns the parameters for the ``bm25`` meta entry."""
    db.executescript(SEARCH_SCHEMA)
    chunks = db.execute("SELECT chunk_id, text FROM chunk ORDER BY chunk_id").fetchall()
    encoder = Bm25Encoder.fit(text for _, text in chunks)
    df: Counter[int] = Counter()
    rows = []
    for chunk_id, text in chunks:
        sparse = encoder.encode_document(text)
        df.update(sparse.indices)
        rows.append((chunk_id, *pack_sparse(sparse)))
    db.executemany("INSERT INTO chunk_bm25 VALUES (?, ?, ?)", rows)
    db.executemany("INSERT INTO bm25_df VALUES (?, ?)", sorted(df.items()))
    return {**encoder.params(), "documents": len(chunks), "terms": len(df)}


@dataclass
class EmbedReport:
    bundle_version: str
    embedding_model: str
    dimensions: int
    chunks: int = 0
    newly_embedded: int = 0
    plan_filter_checks: int = 0


def embed_bundle(
    bundle: Path,
    embed: Embedder,
    cache: EmbeddingCache,
    *,
    replace: bool = False,
    filter_checks: int = 20,
    log: Callable[[str], None] = lambda _: None,
) -> EmbedReport:
    """Store an embedding for every unique chunk in ``bundle``, then validate plan-filtered dense search."""
    version, records = read_chunks(bundle)
    if not records:
        raise SearchBuildError(f"{bundle} has no chunks")
    report = EmbedReport(version, cache.model, cache.dimensions, chunks=len(records))
    with closing(sqlite3.connect(bundle)) as db:
        row = db.execute("SELECT value FROM meta WHERE key = 'embedding'").fetchone()
        if row and not replace:
            raise SearchBuildError(f"{bundle} already has embeddings {json.loads(row[0])} (use --replace)")
        vectors, report.newly_embedded = cache.ensure({r.chunk_hash: r.text for r in records}, embed)
        log(f"{len(records)} unique chunks, {report.newly_embedded} newly embedded")
        ids = dict(db.execute("SELECT chunk_hash, chunk_id FROM chunk"))
        with db:
            db.execute("DELETE FROM chunk_vector")
            db.executemany(
                "INSERT INTO chunk_vector VALUES (?, ?)",
                [(ids[h], pack_dense(v)) for h, v in vectors.items()],
            )
            meta = {
                "model": cache.model, "dimensions": cache.dimensions, "dtype": "float16", "normalized": True,
            }  # fmt: skip
            db.execute("INSERT OR REPLACE INTO meta VALUES ('embedding', ?)", (json.dumps(meta),))
        db.execute("VACUUM")
    report.plan_filter_checks = validate(bundle, records, vectors, checks=filter_checks)
    log(f"bundle {version}: {report.chunks} vectors, {report.plan_filter_checks} plan-filter checks passed")
    return report


def validate(
    bundle: Path,
    records: Sequence[ChunkRecord],
    vectors: dict[str, tuple[float, ...]],
    *,
    checks: int = 20,
    seed: int = 0,
) -> int:
    """Vector count, and dense plan-filtered queries for a sample of plans. Returns the number of plan checks.

    Each probe is the vector of one of the plan's chunks, so that chunk must come back near the top, and every
    hit must belong to the plan."""
    with closing(sqlite3.connect(f"{bundle.resolve().as_uri()}?mode=ro", uri=True)) as db:
        (count,) = db.execute("SELECT COUNT(*) FROM chunk_vector").fetchone()
    if count != len(records):
        raise SearchBuildError(f"{bundle}: {count} vectors, expected {len(records)}")
    by_plan: dict[str, list[str]] = {}
    for r in records:
        for key in r.plan_keys:
            by_plan.setdefault(key, []).append(r.chunk_hash)
    rng = random.Random(seed)  # noqa: S311 - sampling, not security
    sample = rng.sample(sorted(by_plan), min(checks, len(by_plan)))
    with SqliteSearchIndex(bundle) as index:
        for plan_key in sample:
            probe = rng.choice(sorted(by_plan[plan_key]))
            try:
                hits = index.search(SearchQuery(plan_keys=(plan_key,), text="probe", dense=vectors[probe]))
            except PlanFilterViolationError as exc:
                raise SearchBuildError(f"plan filter for {plan_key} failed: {exc}") from None
            if not hits:
                raise SearchBuildError(f"plan-filtered query for {plan_key} returned nothing")
            if {h.chunk_hash for h in hits} - set(by_plan[plan_key]):
                raise SearchBuildError(f"plan filter for {plan_key} returned another plan's chunks")
            # Near-identical rows ("$40/visit" vs "$45/visit") can swap places after float16 rounding.
            if probe not in [h.chunk_hash for h in hits[:PROBE_RANK]]:
                raise SearchBuildError(f"probe chunk {probe} of {plan_key} not in the top {PROBE_RANK}")
    return len(sample)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Add chunk embeddings (OpenRouter) to a facts bundle.")
    ap.add_argument("bundle", type=Path)
    ap.add_argument("--env", choices=[e.value for e in Env], help="config environment (default: APP_ENV)")
    ap.add_argument("--cache", type=Path, default=Path("bundles/embeddings.sqlite"))
    ap.add_argument("--replace", action="store_true", help="replace embeddings already in the bundle")
    ap.add_argument("--report", type=Path, help="write the report as JSON")
    args = ap.parse_args(argv)

    env = EnvSettings()
    config = load_config("broker", Env(args.env) if args.env else env.app_env)
    role = config.models.embeddings
    dims = role.dimensions
    if dims is None:
        raise SystemExit("models.embeddings.dimensions must be set in config/base.yaml")
    with (
        OpenRouterClient.for_bot(env, config.bot.name) as gateway,
        EmbeddingCache(args.cache, role.model, dims) as cache,
    ):

        def embed(texts: Sequence[str]) -> Sequence[Sequence[float]]:
            return gateway.embed(role.model, texts, dimensions=dims, input_type="document").vectors

        report = embed_bundle(
            args.bundle, embed, cache, replace=args.replace, log=lambda msg: print(msg, file=sys.stderr)
        )
    if args.report:
        args.report.write_text(json.dumps(asdict(report), indent=2) + "\n", encoding="utf-8")
    print(json.dumps(asdict(report), indent=2))
