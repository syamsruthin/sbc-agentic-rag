"""Plan-filtered hybrid search over the facts bundle (PLAN.md sections 3.2.4 and 3.4.3).

This is the production :class:`~sbc_core.interfaces.SearchIndex`. The search index lives in the same SQLite
file as the facts, so code, facts and index ship and roll back together:

- ``chunk_bm25``: each unique chunk's BM25 term weights (``sbc_core.bm25``), with document frequencies in
  ``bm25_df``. Written by ``sbc-build-bundle``; needs no API keys.
- ``chunk_vector``: each unique chunk's embedding, unit-normalized and stored as float16. Written by
  ``sbc-embed-bundle``; the model and dimensions are recorded in the bundle's ``embedding`` meta entry.

Every search is restricted to the requested plans in SQL (a join through ``plan_chunk``), so candidate sets
are small (about 61 chunks per plan) and are scored exactly, with no approximate index. Dense and BM25
rankings are fused with Reciprocal Rank Fusion. Every hit is checked again in code before it is returned;
a hit that belongs to none of the requested plans fails the whole search closed.
"""

from __future__ import annotations

import json
import math
import sqlite3
from array import array
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

import numpy as np

from sbc_core.types import SearchHit, SearchQuery, SparseVector

DENSE_DTYPE = np.dtype("<f2")


class SearchIndexError(Exception):
    """The bundle has no usable search index, or a query doesn't fit it."""


class PlanFilterViolationError(RuntimeError):
    """A search produced a chunk outside the requested plans. Never shown to users; alert P1."""


# ------------------------------------------------------------------ encoding (shared with the bundle build)


def pack_sparse(vector: SparseVector) -> tuple[bytes, bytes]:
    return array("I", vector.indices).tobytes(), array("f", vector.values).tobytes()


def unpack_sparse(terms: bytes, weights: bytes) -> dict[int, float]:
    ids, values = array("I"), array("f")
    ids.frombytes(terms)
    values.frombytes(weights)
    return dict(zip(ids, values, strict=True))


def pack_dense(vector: Sequence[float]) -> bytes:
    """Unit-normalized float16, so a dot product is the cosine similarity."""
    v = np.asarray(vector, dtype=np.float32)
    norm = float(np.linalg.norm(v))
    if norm == 0.0 or not math.isfinite(norm):
        raise ValueError("cannot store a zero or non-finite vector")
    return (v / norm).astype(DENSE_DTYPE).tobytes()


def bm25_idf(documents: int, df: int) -> float:
    """The BM25 IDF, the same formula Qdrant's ``Modifier.IDF`` applies."""
    return math.log(1.0 + (documents - df + 0.5) / (df + 0.5))


def rrf(rankings: Iterable[Sequence[int]], k: int) -> dict[int, float]:
    """Reciprocal Rank Fusion of ranked id lists (rank 1 scores ``1 / (k + 1)``)."""
    scores: dict[int, float] = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking, start=1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank)
    return scores


def _in(values: Sequence[object]) -> str:
    return ",".join("?" * len(values))


class SqliteSearchIndex:
    def __init__(
        self,
        path: Path | str,
        *,
        prefetch_limit: int = 20,
        rrf_k: int = 60,
        expected_embedding: tuple[str, int] | None = None,
    ) -> None:
        """Open the bundle read-only. ``expected_embedding`` is the configured ``(model, dimensions)``; when
        given, the bundle's vectors must come from exactly that model, or the index refuses to start."""
        path = Path(path)
        if not path.is_file():
            raise SearchIndexError(f"facts bundle not found: {path}")
        self._db = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, check_same_thread=False)
        try:
            meta = {k: json.loads(v) for k, v in self._db.execute("SELECT key, value FROM meta")}
        except sqlite3.DatabaseError as exc:
            self._db.close()
            raise SearchIndexError(f"not a facts bundle: {path}: {exc}") from None
        bm25: dict[str, Any] | None = meta.get("bm25")
        if not bm25:
            self._db.close()
            raise SearchIndexError(f"{path} has no BM25 index; rebuild it with sbc-build-bundle")
        self.bundle_version = str(meta["bundle_version"])
        self.documents = int(bm25["documents"])
        self.embedding: dict[str, Any] | None = meta.get("embedding")
        if expected_embedding is not None:
            model, dims = expected_embedding
            got = (self.embedding or {}).get("model"), (self.embedding or {}).get("dimensions")
            if got != (model, dims):
                self._db.close()
                raise SearchIndexError(
                    f"{path} has embeddings {got[0] or 'none'} ({got[1] or 0} dims) but the config expects "
                    f"{model} ({dims} dims); run sbc-embed-bundle"
                )
        self.prefetch_limit = prefetch_limit
        self.rrf_k = rrf_k

    def close(self) -> None:
        self._db.close()

    def __enter__(self) -> SqliteSearchIndex:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @property
    def has_dense(self) -> bool:
        return self.embedding is not None

    def search(self, query: SearchQuery) -> list[SearchHit]:
        if query.dense is not None and self.embedding is None:
            raise SearchIndexError(f"bundle {self.bundle_version} has no embeddings for a dense query")
        plans = list(dict.fromkeys(query.plan_keys))
        sql = (
            "SELECT pc.chunk_id, p.plan_key FROM plan p JOIN plan_chunk pc ON pc.plan_id = p.plan_id"  # noqa: S608
            f" JOIN chunk c ON c.chunk_id = pc.chunk_id WHERE p.plan_key IN ({_in(plans)})"
        )
        params: list[object] = list(plans)
        if query.chunk_types:
            sql += f" AND c.chunk_type IN ({_in(query.chunk_types)})"
            params += list(query.chunk_types)
        owners: dict[int, set[str]] = {}
        for chunk_id, plan_key in self._db.execute(sql, params):  # only "?" placeholders are interpolated
            owners.setdefault(chunk_id, set()).add(plan_key)
        if not owners:
            return []
        candidates = sorted(owners)
        rankings = []
        if query.dense is not None:
            rankings.append(self._dense_ranking(candidates, query.dense))
        if query.sparse is not None:
            rankings.append(self._bm25_ranking(candidates, query.sparse))
        fused = rrf(rankings, self.rrf_k)
        top = sorted(fused, key=lambda c: (-fused[c], c))[: query.limit]
        return self._hits(top, fused, owners, plans)

    # ------------------------------------------------------------------ rankings

    def _dense_ranking(self, candidates: Sequence[int], dense: Sequence[float]) -> list[int]:
        dims = int(self.embedding["dimensions"]) if self.embedding else 0
        if len(dense) != dims:
            raise SearchIndexError(f"query vector has {len(dense)} dimensions, the bundle has {dims}")
        rows = self._db.execute(
            f"SELECT chunk_id, vector FROM chunk_vector WHERE chunk_id IN ({_in(candidates)})",  # noqa: S608
            candidates,
        ).fetchall()
        if len(rows) != len(candidates):
            raise SearchIndexError(f"bundle {self.bundle_version}: some candidate chunks have no vector")
        ids = [r[0] for r in rows]
        matrix = np.frombuffer(b"".join(r[1] for r in rows), dtype=DENSE_DTYPE).reshape(len(rows), dims)
        q = np.asarray(dense, dtype=np.float32)
        q /= float(np.linalg.norm(q)) or 1.0
        scores = matrix.astype(np.float32) @ q
        order = sorted(range(len(ids)), key=lambda i: (-float(scores[i]), ids[i]))
        return [ids[i] for i in order[: self.prefetch_limit]]

    def _bm25_ranking(self, candidates: Sequence[int], sparse: SparseVector) -> list[int]:
        query = dict(zip(sparse.indices, sparse.values, strict=True))
        terms = list(query)
        idf = {
            term: bm25_idf(self.documents, df)
            for term, df in self._db.execute(
                f"SELECT term_id, df FROM bm25_df WHERE term_id IN ({_in(terms)})",  # noqa: S608
                terms,
            )
        }
        if not idf:
            return []
        scores: dict[int, float] = {}
        rows = self._db.execute(
            f"SELECT chunk_id, terms, weights FROM chunk_bm25 WHERE chunk_id IN ({_in(candidates)})",  # noqa: S608
            candidates,
        )
        for chunk_id, packed_terms, packed_weights in rows:
            weights = unpack_sparse(packed_terms, packed_weights)
            score = sum(query[t] * w * idf[t] for t, w in weights.items() if t in idf)
            if score > 0:
                scores[chunk_id] = score
        order = sorted(scores, key=lambda c: (-scores[c], c))
        return order[: self.prefetch_limit]

    def _hits(
        self, top: Sequence[int], scores: dict[int, float], owners: dict[int, set[str]], plans: Sequence[str]
    ) -> list[SearchHit]:
        if not top:
            return []
        rows = {
            r[0]: r[1:]
            for r in self._db.execute(
                "SELECT chunk_id, chunk_hash, chunk_type, section_path, text FROM chunk"  # noqa: S608
                f" WHERE chunk_id IN ({_in(top)})",
                list(top),
            )
        }
        wanted = set(plans)
        hits = []
        for chunk_id in top:
            mine = tuple(k for k in plans if k in owners[chunk_id])
            if not mine or not owners[chunk_id] <= wanted:
                raise PlanFilterViolationError(f"chunk {chunk_id} belongs to none of the requested plans")
            chunk_hash, chunk_type, section_path, text = rows[chunk_id]
            hits.append(
                SearchHit(
                    chunk_hash=chunk_hash, chunk_type=chunk_type, section_path=section_path, text=text,
                    plan_keys=mine, score=scores[chunk_id],
                )
            )  # fmt: skip
        return hits
