"""Qdrant implementation of :class:`~sbc_core.interfaces.SearchIndex` (PLAN.md section 3.4.3).

One ``query_points`` call: a dense and a BM25 prefetch, both under the mandatory plan filter, fused with
Reciprocal Rank Fusion. The filter is built here from ``SearchQuery.plan_keys``, never from model output.
Every returned point is checked again in code; a point that belongs to none of the requested plans means
the index or the filter is broken, so the whole search fails closed with :class:`PlanFilterViolationError`.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client import models as qm

from sbc_core.types import SearchHit, SearchQuery

DENSE = "dense"
SPARSE = "bm25"


class PlanFilterViolationError(RuntimeError):
    """A search returned a chunk outside the requested plans. Never shown to users; alert P1."""


def plan_filter(plan_keys: Sequence[str], chunk_types: Sequence[str] = ()) -> qm.Filter:
    must: list[qm.Condition] = [qm.FieldCondition(key="plan_keys", match=qm.MatchAny(any=list(plan_keys)))]
    if chunk_types:
        must.append(qm.FieldCondition(key="chunk_type", match=qm.MatchAny(any=list(chunk_types))))
    return qm.Filter(must=must)


def checked_hits(points: Iterable[Any], plan_keys: Sequence[str]) -> list[SearchHit]:
    """Turn points into hits, keeping only the requested plans; fail closed on any foreign point."""
    wanted = set(plan_keys)
    hits = []
    for point in points:
        payload = point.payload or {}
        mine = tuple(k for k in payload.get("plan_keys", ()) if k in wanted)
        if not mine:
            raise PlanFilterViolationError(f"point {point.id} belongs to none of the requested plans")
        hits.append(
            SearchHit(
                chunk_hash=payload["chunk_hash"], chunk_type=payload["chunk_type"],
                section_path=payload["section_path"], text=payload["text"], plan_keys=mine,
                score=float(point.score),
            )
        )  # fmt: skip
    return hits


class QdrantSearchIndex:
    def __init__(
        self, client: QdrantClient, collection: str, *, prefetch_limit: int = 20, rrf_k: int = 60
    ) -> None:
        self.client = client
        self.collection = collection
        self.prefetch_limit = prefetch_limit
        self.rrf_k = rrf_k

    def search(self, query: SearchQuery) -> list[SearchHit]:
        flt = plan_filter(query.plan_keys, query.chunk_types)
        prefetch = []
        if query.dense is not None:
            prefetch.append(
                qm.Prefetch(query=list(query.dense), using=DENSE, filter=flt, limit=self.prefetch_limit)
            )
        if query.sparse is not None:
            sparse = qm.SparseVector(indices=list(query.sparse.indices), values=list(query.sparse.values))
            prefetch.append(qm.Prefetch(query=sparse, using=SPARSE, filter=flt, limit=self.prefetch_limit))
        response = self.client.query_points(
            self.collection,
            prefetch=prefetch,
            query=qm.RrfQuery(rrf=qm.Rrf(k=self.rrf_k)),
            query_filter=flt,
            limit=query.limit,
            with_payload=["chunk_hash", "chunk_type", "section_path", "text", "plan_keys"],
        )
        return checked_hits(response.points, query.plan_keys)
