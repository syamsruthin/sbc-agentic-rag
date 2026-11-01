"""Plan-filtered hybrid text search, the engine behind the ``search_sbc_text`` tool (PLAN.md 3.4.3).

1. Embed the query (OpenRouter) and encode BM25 terms locally.
2. Search the index under the mandatory plan filter (dense + BM25, fused with RRF).
3. Rerank the top candidates (OpenRouter), then keep the best ``keep_per_plan`` chunks per plan.

Degraded modes are explicit and recorded on the result: if the embedding call fails the search runs on BM25
alone, and if rerank fails the RRF order is kept.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from sbc_core.bm25 import Bm25Encoder
from sbc_core.config import Models, Retrieval
from sbc_core.interfaces import SearchIndex
from sbc_core.llm import GatewayError, OpenRouterClient
from sbc_core.types import ChunkType, SearchHit, SearchQuery

QueryEmbedder = Callable[[str], Sequence[float]]
Reranker = Callable[[str, Sequence[str]], Sequence[tuple[int, float]]]
"""(query, documents) -> [(document index, score)], best first."""


class SearchRequestError(ValueError):
    """The request is outside what this bot or search may do (no plans, too many plans)."""


@dataclass
class TextSearchResult:
    hits: list[SearchHit]
    degraded: list[str] = field(default_factory=list)
    """e.g. ["embedding_failed: ...", "rerank_failed: ..."]; recorded on the trace."""
    reranked: bool = False


class TextSearcher:
    def __init__(
        self,
        index: SearchIndex,
        settings: Retrieval,
        *,
        embed_query: QueryEmbedder | None = None,
        rerank: Reranker | None = None,
    ) -> None:
        self.index = index
        self.settings = settings
        self.embed_query = embed_query
        self.rerank = rerank if settings.rerank_enabled else None

    @classmethod
    def with_gateway(
        cls,
        index: SearchIndex,
        settings: Retrieval,
        models: Models,
        gateway: OpenRouterClient,
    ) -> TextSearcher:
        """Wire query embedding and rerank to OpenRouter with the configured models."""
        embed_model, dims, rerank_model = (
            models.embeddings.model,
            models.embeddings.dimensions,
            models.rerank.model,
        )

        def embed(text: str) -> Sequence[float]:
            return gateway.embed(embed_model, [text], dimensions=dims, input_type="query").vectors[0]

        def rerank(query: str, documents: Sequence[str]) -> Sequence[tuple[int, float]]:
            return [(r.index, r.score) for r in gateway.rerank(rerank_model, query, documents).results]

        return cls(index, settings, embed_query=embed, rerank=rerank)

    def search(
        self, plan_keys: Sequence[str], query: str, *, chunk_types: Sequence[ChunkType] = ()
    ) -> TextSearchResult:
        plans = tuple(dict.fromkeys(plan_keys))
        if not plans:
            raise SearchRequestError("text search needs at least one resolved plan")
        if len(plans) > self.settings.max_plans_per_search:
            raise SearchRequestError(f"at most {self.settings.max_plans_per_search} plans per search")
        result = TextSearchResult(hits=[])
        dense = None
        if self.embed_query is not None:
            try:
                dense = tuple(float(x) for x in self.embed_query(query))
            except GatewayError as exc:
                result.degraded.append(f"embedding_failed: {exc}")
        else:
            result.degraded.append("embedding_disabled")
        # Enough candidates for every plan to fill its quota, and at least the rerank window.
        limit = min(100, max(self.settings.rerank_candidates, self.settings.keep_per_plan * len(plans)))
        sparse = Bm25Encoder.encode_query(query)
        candidates = self.index.search(
            SearchQuery(
                plan_keys=plans,
                text=query,
                dense=dense,
                sparse=sparse,
                chunk_types=tuple(chunk_types),
                limit=limit,
            )
        )
        if self.rerank is not None and len(candidates) > 1:
            try:
                order = self.rerank(query, [h.text for h in candidates[: self.settings.rerank_candidates]])
                ranked = [candidates[i].model_copy(update={"score": s}) for i, s in order]
                seen = {i for i, _ in order}
                rest = [h for i, h in enumerate(candidates) if i not in seen]
                candidates, result.reranked = ranked + rest, True
            except GatewayError as exc:
                result.degraded.append(f"rerank_failed: {exc}")
        result.hits = keep_per_plan(candidates, plans, self.settings.keep_per_plan)
        return result


def keep_per_plan(hits: Sequence[SearchHit], plan_keys: Sequence[str], keep: int) -> list[SearchHit]:
    """The best ``keep`` hits for each plan, in score order. A hit shared by plans counts for each of them."""
    counts = dict.fromkeys(plan_keys, 0)
    out = []
    for hit in hits:
        room = [k for k in hit.plan_keys if counts.get(k, keep) < keep]
        if room:
            for key in room:
                counts[key] += 1
            out.append(hit.model_copy(update={"plan_keys": tuple(room)}))
    return out
