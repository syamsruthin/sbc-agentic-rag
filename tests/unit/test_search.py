"""The bundle's search index: BM25 scoring, dense scoring, fusion and the plan filter."""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from sbc_core.bm25 import Bm25Encoder
from sbc_core.search import SearchIndexError, SqliteSearchIndex, bm25_idf, pack_dense, rrf
from sbc_core.types import SearchQuery
from sbc_index.search_build import read_chunks
from tests.unit.test_bundle import make_bundle
from tests.unit.test_search_build import DIMS, fake_embed, make_embedded_bundle

PLAN_A, PLAN_B = "2027:100000000001", "2027:100000000002"


def test_rrf_sums_reciprocal_ranks() -> None:
    scores = rrf([[1, 2, 3], [3, 1]], k=60)
    assert scores[1] == pytest.approx(1 / 61 + 1 / 62)
    assert scores[3] == pytest.approx(1 / 63 + 1 / 61)
    assert scores[2] == pytest.approx(1 / 62)


def test_idf_matches_the_bm25_formula() -> None:
    assert bm25_idf(100, 1) == pytest.approx(math.log(1 + 99.5 / 1.5))
    assert bm25_idf(100, 100) > 0


def test_pack_dense_rejects_zero_vectors() -> None:
    with pytest.raises(ValueError, match="zero"):
        pack_dense([0.0] * DIMS)


def test_bm25_scores_equal_the_textbook_sum(tmp_path: Path) -> None:
    """Score = sum over query terms of idf(term) x the document's saturated term weight."""
    bundle = make_bundle(tmp_path)
    _, records = read_chunks(bundle)
    texts = [r.text for r in records]
    encoder = Bm25Encoder.fit(texts)
    query = "specialist visit deductible"
    q_terms = set(Bm25Encoder.encode_query(query).indices)
    df = {t: sum(t in set(encoder.encode_document(x).indices) for x in texts) for t in q_terms}
    expected = {}
    for r in records:
        if PLAN_A not in r.plan_keys:
            continue
        doc = encoder.encode_document(r.text)
        weights = dict(zip(doc.indices, doc.values, strict=True))
        score = sum(bm25_idf(len(texts), df[t]) * weights[t] for t in q_terms if t in weights)
        if score > 0:
            expected[r.chunk_hash] = score
    best = sorted(expected, key=lambda h: -expected[h])

    with SqliteSearchIndex(bundle, rrf_k=60) as index:
        hits = index.search(
            SearchQuery(plan_keys=(PLAN_A,), text=query, sparse=Bm25Encoder.encode_query(query), limit=100)
        )
    assert [h.chunk_hash for h in hits] == best[: len(hits)]
    assert len(hits) == min(len(best), index.prefetch_limit)


def test_dense_ranks_by_cosine_and_fuses_with_bm25(tmp_path: Path) -> None:
    bundle = make_embedded_bundle(tmp_path)
    _, records = read_chunks(bundle)
    target = next(r for r in records if PLAN_A in r.plan_keys and r.chunk_type == "service_row")
    with SqliteSearchIndex(bundle) as index:
        dense = tuple(fake_embed([target.text])[0])
        hits = index.search(SearchQuery(plan_keys=(PLAN_A,), text="x", dense=dense))
        assert hits[0].chunk_hash == target.chunk_hash
        assert hits[0].score == pytest.approx(1 / 61)
        sparse = Bm25Encoder.encode_query(target.text)
        fused = index.search(SearchQuery(plan_keys=(PLAN_A,), text="x", dense=dense, sparse=sparse))
        assert fused[0].chunk_hash == target.chunk_hash
        assert fused[0].score == pytest.approx(2 / 61)


def _plan_b_specialist(name: str, text: str) -> str:
    """Give plan B its own specialist row, so the plans stop sharing every chunk."""
    return text.replace("$60/visit", "$75/visit") if name == "b.md" else text


def test_plan_filter_and_chunk_types(tmp_path: Path) -> None:
    bundle = make_bundle(tmp_path, _plan_b_specialist)
    _, records = read_chunks(bundle)
    only_b = {r.chunk_hash for r in records if r.plan_keys == (PLAN_B,)}
    assert only_b, "plan B has its own specialist row"
    with SqliteSearchIndex(bundle) as index:
        query = Bm25Encoder.encode_query("specialist visit $75/visit $60/visit deductible")
        hits = index.search(SearchQuery(plan_keys=(PLAN_A,), text="q", sparse=query, limit=100))
        assert hits
        assert all(h.plan_keys == (PLAN_A,) for h in hits)
        assert not {h.chunk_hash for h in hits} & only_b
        both = index.search(SearchQuery(plan_keys=(PLAN_A, PLAN_B), text="q", sparse=query, limit=100))
        assert {h.chunk_hash for h in both} & only_b
        assert all(set(h.plan_keys) <= {PLAN_A, PLAN_B} for h in both)
        iq = index.search(SearchQuery(plan_keys=(PLAN_A,), text="q", sparse=query, chunk_types=("iq",)))
        assert iq
        assert {h.chunk_type for h in iq} == {"iq"}
        unknown = SearchQuery(plan_keys=("2027:999999999999",), text="q", sparse=query)
        assert index.search(unknown) == []


def test_dense_queries_need_matching_vectors(tmp_path: Path) -> None:
    with SqliteSearchIndex(make_bundle(tmp_path)) as plain:
        assert not plain.has_dense
        with pytest.raises(SearchIndexError, match="no embeddings"):
            plain.search(SearchQuery(plan_keys=(PLAN_A,), text="x", dense=(1.0,) * DIMS))
    with SqliteSearchIndex(make_embedded_bundle(tmp_path / "embedded")) as index:
        assert index.has_dense
        with pytest.raises(SearchIndexError, match="dimensions"):
            index.search(SearchQuery(plan_keys=(PLAN_A,), text="x", dense=(1.0,) * (DIMS + 1)))


def test_missing_or_foreign_bundle(tmp_path: Path) -> None:
    with pytest.raises(SearchIndexError, match="not found"):
        SqliteSearchIndex(tmp_path / "nope.sqlite")
    junk = tmp_path / "junk.sqlite"
    junk.write_text("not sqlite")
    with pytest.raises(SearchIndexError, match="not a facts bundle"):
        SqliteSearchIndex(junk)
