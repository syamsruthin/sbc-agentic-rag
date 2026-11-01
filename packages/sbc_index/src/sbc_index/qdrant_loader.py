"""Load a facts bundle's unique chunks into a new, versioned Qdrant collection (PLAN.md 3.2.4 and 3.2.5).

One point per unique chunk, with a ``dense`` vector (OpenRouter embeddings, int8-quantized in RAM, full
vectors on disk) and a ``bm25`` sparse vector (local tokenizer, Qdrant applies IDF). The payload lists every
plan containing the chunk, plus their years, states and markets, each with a keyword index, so every search
can be filtered by plan.

A collection is never modified after it is validated: each bundle version gets its own
collection, ``{prefix}__sbc_chunks__{bundle_version}``, and the API config pins the exact name.
Validation checks the point count and that plan-filtered queries return only that plan's chunks.

A BM25-only collection (no embedder) needs no API keys; the retrieval eval uses one in Qdrant's in-memory
local mode for its BM25-only configuration (PLAN.md section 5.1).
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sqlite3
import sys
import uuid
from collections.abc import Callable, Iterator, Sequence
from contextlib import closing
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client import models as qm

from sbc_core.bm25 import Bm25Encoder
from sbc_core.config import Env, EnvSettings, load_config
from sbc_core.llm import OpenRouterClient
from sbc_index.embed_cache import Embedder, EmbeddingCache

DENSE = "dense"
SPARSE = "bm25"
KEYWORD_FIELDS = ("plan_keys", "states", "markets", "chunk_type")
INTEGER_FIELDS = ("plan_years",)
_POINT_NAMESPACE = uuid.UUID("5f0b8a52-3f53-4c1e-9d0e-2b8f7f3e6a10")
UPSERT_BATCH = 256


class LoadError(Exception):
    """The collection could not be built or failed validation."""


def collection_name(prefix: str, bundle_version: str) -> str:
    """``dev__sbc_chunks__2027_1`` for prefix ``dev`` and bundle ``2027.1``."""
    if not re.fullmatch(r"[a-z]+", prefix):
        raise LoadError(f"bad collection prefix {prefix!r}")
    version = re.sub(r"[^0-9A-Za-z]+", "_", bundle_version).strip("_")
    if not version:
        raise LoadError(f"bad bundle version {bundle_version!r}")
    return f"{prefix}__sbc_chunks__{version}"


def point_id(chunk_hash: str) -> str:
    """Qdrant needs integer or UUID ids; a UUID derived from the content hash keeps ids stable."""
    return str(uuid.uuid5(_POINT_NAMESPACE, chunk_hash))


@dataclass(frozen=True)
class ChunkRecord:
    chunk_hash: str
    chunk_type: str
    section_path: str
    text: str
    plan_keys: tuple[str, ...]
    plan_years: tuple[int, ...]
    states: tuple[str, ...]
    markets: tuple[str, ...]

    def payload(self) -> dict[str, Any]:
        data = asdict(self)
        return {k: list(v) if isinstance(v, tuple) else v for k, v in data.items()}


def read_chunks(bundle: Path) -> tuple[str, list[ChunkRecord]]:
    """The bundle version and every unique chunk with the plans that contain it."""
    with closing(sqlite3.connect(f"{bundle.resolve().as_uri()}?mode=ro", uri=True)) as db:
        version = json.loads(db.execute("SELECT value FROM meta WHERE key = 'bundle_version'").fetchone()[0])
        rows = db.execute(
            """SELECT c.chunk_hash, c.chunk_type, c.section_path, c.text, p.plan_key, p.plan_year, p.state,
                      p.market
               FROM chunk c JOIN plan_chunk pc ON pc.chunk_id = c.chunk_id
               JOIN plan p ON p.plan_id = pc.plan_id
               ORDER BY c.chunk_id, p.plan_key"""
        ).fetchall()
    grouped: dict[str, tuple[tuple[str, str, str], set[str], set[int], set[str], set[str]]] = {}
    for chunk_hash, chunk_type, path, text, plan_key, year, state, market in rows:
        entry = grouped.setdefault(chunk_hash, ((chunk_type, path, text), set(), set(), set(), set()))
        entry[1].add(plan_key)
        entry[2].add(year)
        if state:
            entry[3].add(state)
        if market:
            entry[4].add(market)
    records = [
        ChunkRecord(
            h, *meta, tuple(sorted(keys)), tuple(sorted(years)), tuple(sorted(states)), tuple(sorted(markets))
        )
        for h, (meta, keys, years, states, markets) in grouped.items()
    ]
    return str(version), records


@dataclass
class LoadReport:
    collection: str
    bundle_version: str
    embedding_model: str
    dimensions: int
    points: int = 0
    plan_chunk_links: int = 0
    newly_embedded: int = 0
    bm25: dict[str, float | int] = field(default_factory=dict)
    plan_filter_checks: int = 0


def create_collection(client: QdrantClient, name: str, dimensions: int | None) -> None:
    """A collection with ``dense`` and ``bm25`` vectors, or ``bm25`` only when ``dimensions`` is None."""
    vectors: dict[str, qm.VectorParams] = {}
    quantization = None
    if dimensions is not None:
        vectors[DENSE] = qm.VectorParams(size=dimensions, distance=qm.Distance.COSINE, on_disk=True)
        quantization = qm.ScalarQuantization(
            scalar=qm.ScalarQuantizationConfig(type=qm.ScalarType.INT8, always_ram=True)
        )
    client.create_collection(
        name,
        vectors_config=vectors,
        sparse_vectors_config={SPARSE: qm.SparseVectorParams(modifier=qm.Modifier.IDF)},
        quantization_config=quantization,
        on_disk_payload=True,
    )
    for name_ in KEYWORD_FIELDS:
        client.create_payload_index(name, name_, qm.PayloadSchemaType.KEYWORD)
    for name_ in INTEGER_FIELDS:
        client.create_payload_index(name, name_, qm.PayloadSchemaType.INTEGER)


def _batches(items: Sequence[ChunkRecord], size: int) -> Iterator[Sequence[ChunkRecord]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def load(
    client: QdrantClient,
    bundle: Path,
    *,
    prefix: str,
    embed: Embedder | None,
    cache: EmbeddingCache | None,
    recreate: bool = False,
    filter_checks: int = 20,
    log: Callable[[str], None] = lambda _: None,
) -> LoadReport:
    """Build and validate the collection. Without ``embed`` and ``cache`` it holds BM25 vectors only."""
    if (embed is None) != (cache is None):
        raise LoadError("pass both an embedder and an embedding cache, or neither for a BM25-only collection")
    version, records = read_chunks(bundle)
    if not records:
        raise LoadError(f"{bundle} has no chunks")
    name = collection_name(prefix, version)
    report = LoadReport(name, version, cache.model if cache else "none", cache.dimensions if cache else 0)
    if client.collection_exists(name):
        if not recreate:
            raise LoadError(f"collection {name} already exists; collections are immutable (use --recreate)")
        client.delete_collection(name)

    vectors: dict[str, tuple[float, ...]] | None = None
    if embed is not None and cache is not None:
        vectors, report.newly_embedded = cache.ensure({r.chunk_hash: r.text for r in records}, embed)
        log(f"{len(records)} unique chunks, {report.newly_embedded} newly embedded")
    encoder = Bm25Encoder.fit(r.text for r in records)
    report.bm25 = encoder.params()

    create_collection(client, name, cache.dimensions if cache else None)
    for batch in _batches(records, UPSERT_BATCH):
        points = []
        for r in batch:
            sparse = encoder.encode_document(r.text)
            vector: dict[str, Any] = {
                SPARSE: qm.SparseVector(indices=list(sparse.indices), values=list(sparse.values))
            }
            if vectors is not None:
                vector[DENSE] = list(vectors[r.chunk_hash])
            points.append(qm.PointStruct(id=point_id(r.chunk_hash), vector=vector, payload=r.payload()))
        client.upsert(name, points=points, wait=True)
    report.points = len(records)
    report.plan_chunk_links = sum(len(r.plan_keys) for r in records)
    report.plan_filter_checks = validate(client, name, records, vectors, checks=filter_checks)
    log(f"{name}: {report.points} points, {report.plan_chunk_links} plan links, validated")
    return report


def validate(
    client: QdrantClient,
    name: str,
    records: Sequence[ChunkRecord],
    vectors: dict[str, tuple[float, ...]] | None,
    *,
    checks: int = 20,
    seed: int = 0,
) -> int:
    """Point count, and plan-filtered queries for a sample of plans. Returns the number of plan checks.

    Probes use the dense vector of a chunk of the plan, or its BM25 query terms when there are no vectors."""
    count = client.count(name, exact=True).count
    if count != len(records):
        raise LoadError(f"{name}: {count} points, expected {len(records)}")
    by_plan: dict[str, set[str]] = {}
    texts = {r.chunk_hash: r.text for r in records}
    for r in records:
        for key in r.plan_keys:
            by_plan.setdefault(key, set()).add(r.chunk_hash)
    rng = random.Random(seed)  # noqa: S311 - sampling, not security
    plans = sorted(by_plan)
    sample = rng.sample(plans, min(checks, len(plans)))
    for plan_key in sample:
        probe = rng.choice(sorted(by_plan[plan_key]))
        flt = qm.Filter(must=[qm.FieldCondition(key="plan_keys", match=qm.MatchAny(any=[plan_key]))])
        if vectors is not None:
            query: Any = list(vectors[probe])
            using = DENSE
        else:
            terms = Bm25Encoder.encode_query(texts[probe])
            query, using = qm.SparseVector(indices=list(terms.indices), values=list(terms.values)), SPARSE
        hits = client.query_points(
            name, query=query, using=using, query_filter=flt, limit=50, with_payload=True
        ).points
        returned = {str((h.payload or {}).get("chunk_hash")) for h in hits}
        if not returned:
            raise LoadError(f"{name}: plan-filtered query for {plan_key} returned nothing")
        if returned - by_plan[plan_key]:
            raise LoadError(f"{name}: plan filter for {plan_key} returned another plan's chunks")
        if any(plan_key not in (h.payload or {}).get("plan_keys", []) for h in hits):
            raise LoadError(f"{name}: plan filter for {plan_key} returned a point without that plan")
    return len(sample)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Load a facts bundle's chunks into a new Qdrant collection.")
    ap.add_argument("bundle", type=Path)
    ap.add_argument("--env", choices=[e.value for e in Env], help="collection prefix (default: APP_ENV)")
    ap.add_argument("--cache", type=Path, default=Path("bundles/embeddings.sqlite"))
    ap.add_argument("--recreate", action="store_true", help="drop and rebuild an existing collection")
    ap.add_argument("--report", type=Path, help="write the load report as JSON")
    args = ap.parse_args(argv)

    env = EnvSettings()
    app_env = Env(args.env) if args.env else env.app_env
    config = load_config("broker", app_env)
    key = env.qdrant_api_key_rw or env.qdrant_api_key
    if not env.qdrant_url or key is None:
        raise SystemExit("set QDRANT_URL and QDRANT_API_KEY_RW (or QDRANT_API_KEY)")
    role = config.models.embeddings
    dims = role.dimensions
    if dims is None:
        raise SystemExit("models.embeddings.dimensions must be set in config/base.yaml")
    qdrant = QdrantClient(url=env.qdrant_url, api_key=key.get_secret_value(), timeout=60)
    with (
        OpenRouterClient.for_bot(env, config.bot.name) as gateway,
        EmbeddingCache(args.cache, role.model, dims) as cache,
    ):

        def embed(texts: Sequence[str]) -> Sequence[Sequence[float]]:
            return gateway.embed(role.model, texts, dimensions=dims, input_type="document").vectors

        report = load(
            qdrant, args.bundle, prefix=config.search_index.collection_prefix, embed=embed, cache=cache,
            recreate=args.recreate, log=lambda msg: print(msg, file=sys.stderr),
        )  # fmt: skip
    if args.report:
        args.report.write_text(json.dumps(asdict(report), indent=2) + "\n", encoding="utf-8")
    print(json.dumps(asdict(report), indent=2))
