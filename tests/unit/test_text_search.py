"""Plan-filtered hybrid search: the bundle's SQLite index and the TextSearcher pipeline."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

import pytest

from sbc_core.config import Retrieval
from sbc_core.llm import GatewayError
from sbc_core.search import SqliteSearchIndex
from sbc_core.types import SearchHit, SearchQuery
from sbc_retrieval.text_search import SearchRequestError, TextSearcher, keep_per_plan
from tests.unit.test_search_build import fake_embed, make_embedded_bundle

PLAN_A, PLAN_B = "2027:100000000001", "2027:100000000002"
SETTINGS = Retrieval(prefetch_limit=20, rrf_k=60, rerank_candidates=20, keep_per_plan=6, rerank_enabled=True,
                     max_plans_per_search=6)  # fmt: skip


@pytest.fixture
def index(tmp_path: Path) -> Iterator[SqliteSearchIndex]:
    with SqliteSearchIndex(make_embedded_bundle(tmp_path)) as idx:
        yield idx


def _embed_query(text: str) -> Sequence[float]:
    return fake_embed([text])[0]


def test_bm25_finds_the_row_and_respects_the_plan_filter(index: SqliteSearchIndex) -> None:
    result = TextSearcher(index, SETTINGS).search([PLAN_A], "specialist visit copay")
    assert result.hits[0].section_path.endswith("> Specialist visit")
    assert all(h.plan_keys == (PLAN_A,) for h in result.hits)
    assert result.degraded == ["embedding_disabled"]
    assert len(result.hits) <= SETTINGS.keep_per_plan


def test_dense_and_bm25_are_fused(index: SqliteSearchIndex) -> None:
    (target,) = (
        TextSearcher(index, SETTINGS.model_copy(update={"keep_per_plan": 1}))
        .search([PLAN_A], "specialist visit")
        .hits
    )
    # Fake embeddings only match identical text, so embed the target chunk's own text as the "query".
    searcher = TextSearcher(index, SETTINGS, embed_query=lambda _: fake_embed([target.text])[0])
    result = searcher.search([PLAN_A], "specialist visit")
    assert result.degraded == []
    assert result.hits[0].chunk_hash == target.chunk_hash


def test_shared_chunks_report_only_requested_plans(index: SqliteSearchIndex) -> None:
    result = TextSearcher(index, SETTINGS).search([PLAN_A, PLAN_B], "cosmetic surgery")
    assert result.degraded == ["embedding_disabled"]
    assert result.hits[0].section_path == "Excluded Services"
    assert set(result.hits[0].plan_keys) == {PLAN_A, PLAN_B}
    assert all(set(h.plan_keys) <= {PLAN_A, PLAN_B} for h in result.hits)


def test_unknown_plan_returns_nothing(index: SqliteSearchIndex) -> None:
    query = SearchQuery(
        plan_keys=("2027:999999999999",), text="x", sparse=None, dense=tuple(_embed_query("x"))
    )
    assert index.search(query) == []


def test_chunk_type_filter(index: SqliteSearchIndex) -> None:
    result = TextSearcher(index, SETTINGS).search([PLAN_A], "deductible", chunk_types=["iq"])
    assert result.hits
    assert {h.chunk_type for h in result.hits} == {"iq"}


class StubIndex:
    def __init__(self, hits: list[SearchHit]) -> None:
        self.hits = hits
        self.queries: list[SearchQuery] = []

    def search(self, query: SearchQuery) -> list[SearchHit]:
        self.queries.append(query)
        return self.hits


def _hit(n: int, *plans: str) -> SearchHit:
    return SearchHit(chunk_hash=f"h{n}", chunk_type="iq", section_path="s", text=f"t{n}", plan_keys=plans,
                     score=1.0 / (n + 1))  # fmt: skip


def test_rerank_reorders_and_failures_degrade() -> None:
    stub = StubIndex([_hit(0, "2027:1"), _hit(1, "2027:1"), _hit(2, "2027:1")])

    def rerank(query: str, docs: Sequence[str]) -> list[tuple[int, float]]:
        return [(2, 0.9), (0, 0.5)]

    result = TextSearcher(stub, SETTINGS, rerank=rerank).search(["2027:1"], "q")
    assert [h.chunk_hash for h in result.hits] == ["h2", "h0", "h1"]
    assert result.reranked

    def broken(*_: object) -> Any:
        raise GatewayError("down", 503)

    result = TextSearcher(stub, SETTINGS, rerank=broken, embed_query=broken).search(["2027:1"], "q")
    assert [h.chunk_hash for h in result.hits] == ["h0", "h1", "h2"]
    assert [d.split(":")[0] for d in result.degraded] == ["embedding_failed", "rerank_failed"]
    assert stub.queries[-1].dense is None
    assert stub.queries[-1].sparse is not None


def test_rerank_can_be_disabled() -> None:
    stub = StubIndex([_hit(0, "2027:1"), _hit(1, "2027:1")])
    off = SETTINGS.model_copy(update={"rerank_enabled": False})
    result = TextSearcher(stub, off, rerank=lambda q, d: [(1, 1.0)]).search(["2027:1"], "q")
    assert not result.reranked


def test_request_limits() -> None:
    searcher = TextSearcher(StubIndex([]), SETTINGS)
    with pytest.raises(SearchRequestError, match="at least one"):
        searcher.search([], "q")
    with pytest.raises(SearchRequestError, match="at most 6"):
        searcher.search([f"2027:{i}" for i in range(7)], "q")


def test_keep_per_plan_counts_shared_hits_for_each_plan() -> None:
    hits = [_hit(0, "2027:1", "2027:2"), _hit(1, "2027:1"), _hit(2, "2027:2"), _hit(3, "2027:1")]
    kept = keep_per_plan(hits, ["2027:1", "2027:2"], 2)
    assert [(h.chunk_hash, h.plan_keys) for h in kept] == [
        ("h0", ("2027:1", "2027:2")),
        ("h1", ("2027:1",)),
        ("h2", ("2027:2",)),
    ]


def test_query_expansion_adds_sbc_labels() -> None:
    from sbc_core.taxonomy import load_taxonomy
    from sbc_retrieval.text_search import expand_query

    tax = load_taxonomy()
    assert expand_query("How much is an MRI?", tax) == "How much is an MRI? Imaging (CT/PET scans, MRIs)"
    assert expand_query("Specialist visit", tax) == "Specialist visit"  # already the SBC's wording
    assert expand_query("acupuncture", tax) == "acupuncture"


def test_bm25_query_is_expanded_unless_disabled() -> None:
    stub = StubIndex([])
    TextSearcher(stub, SETTINGS).search(["2027:1"], "MRI")
    off = SETTINGS.model_copy(update={"synonym_expansion": False})
    TextSearcher(stub, off).search(["2027:1"], "MRI")
    expanded, plain = (q.sparse for q in stub.queries)
    assert expanded is not None
    assert plain is not None
    assert set(plain.indices) < set(expanded.indices)
    assert stub.queries[0].text == "MRI"  # the dense/rerank text stays the user's
