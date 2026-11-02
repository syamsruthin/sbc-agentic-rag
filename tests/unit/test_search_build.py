"""Writing the search index into the facts bundle: BM25 tables at build time, embeddings afterwards."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import closing
from pathlib import Path

import pytest

from sbc_core.search import SqliteSearchIndex
from sbc_index.embed_cache import EmbeddingCache
from sbc_index.search_build import SearchBuildError, embed_bundle, read_chunks
from tests.unit.test_bundle import make_bundle

DIMS = 8
MODEL = "test/embed"


def fake_embed(texts: Sequence[str]) -> list[list[float]]:
    """Deterministic pseudo-embeddings: identical text, identical vector."""
    out = []
    for text in texts:
        digest = hashlib.sha256(text.encode()).digest()
        out.append([b / 255 - 0.5 for b in digest[:DIMS]])
    return out


class CountingEmbedder:
    def __init__(self) -> None:
        self.calls = 0
        self.texts = 0

    def __call__(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls += 1
        self.texts += len(texts)
        return fake_embed(texts)


def make_embedded_bundle(root: Path) -> Path:
    """The two-plan test bundle with fake embeddings."""
    bundle = make_bundle(root)
    with EmbeddingCache(root / "emb.sqlite", MODEL, DIMS) as cache:
        embed_bundle(bundle, fake_embed, cache)
    return bundle


@pytest.fixture
def cache(tmp_path: Path) -> Iterator[EmbeddingCache]:
    with EmbeddingCache(tmp_path / "emb.sqlite", MODEL, DIMS) as c:
        yield c


def _meta(bundle: Path) -> dict[str, object]:
    with closing(sqlite3.connect(bundle)) as db:
        return {k: json.loads(v) for k, v in db.execute("SELECT key, value FROM meta")}


def test_read_chunks_groups_plans(tmp_path: Path) -> None:
    version, records = read_chunks(make_bundle(tmp_path))
    assert version == "test.1"
    assert [r for r in records if len(r.plan_keys) == 2], "the two synthetic plans share most chunks"
    assert [r for r in records if r.chunk_type == "header"]


def test_bundle_build_writes_bm25_for_every_chunk(tmp_path: Path) -> None:
    bundle = make_bundle(tmp_path)
    bm25 = _meta(bundle)["bm25"]
    assert isinstance(bm25, dict)
    assert bm25["tokenizer_version"] == 1
    with closing(sqlite3.connect(bundle)) as db:
        (chunks,) = db.execute("SELECT COUNT(*) FROM chunk").fetchone()
        (bm25_rows,) = db.execute("SELECT COUNT(*) FROM chunk_bm25").fetchone()
        (vectors,) = db.execute("SELECT COUNT(*) FROM chunk_vector").fetchone()
        (max_df,) = db.execute("SELECT MAX(df) FROM bm25_df").fetchone()
    assert bm25["documents"] == chunks == bm25_rows
    assert 1 <= max_df <= chunks
    assert vectors == 0
    assert "embedding" not in _meta(bundle)


def test_embed_stores_validates_and_reuses_the_cache(tmp_path: Path, cache: EmbeddingCache) -> None:
    bundle = make_bundle(tmp_path)
    embedder = CountingEmbedder()
    report = embed_bundle(bundle, embedder, cache)
    _, records = read_chunks(bundle)
    assert report.chunks == len(records) == report.newly_embedded == embedder.texts
    assert report.plan_filter_checks == 2
    assert _meta(bundle)["embedding"] == {"model": MODEL, "dimensions": DIMS, "dtype": "float16",
                                          "normalized": True}  # fmt: skip

    with pytest.raises(SearchBuildError, match="already has embeddings"):
        embed_bundle(bundle, embedder, cache)
    again = CountingEmbedder()
    report = embed_bundle(bundle, again, cache, replace=True)
    assert (report.newly_embedded, again.calls) == (0, 0)


def test_index_refuses_a_bundle_embedded_with_another_model(tmp_path: Path) -> None:
    bundle = make_embedded_bundle(tmp_path)
    SqliteSearchIndex(bundle, expected_embedding=(MODEL, DIMS)).close()
    with pytest.raises(Exception, match="config expects other/model"):
        SqliteSearchIndex(bundle, expected_embedding=("other/model", DIMS))
    with pytest.raises(Exception, match="has embeddings none"):
        SqliteSearchIndex(make_bundle(tmp_path / "plain"), expected_embedding=(MODEL, DIMS))


def test_cache_rejects_wrong_dimensions(cache: EmbeddingCache) -> None:
    with pytest.raises(ValueError, match="dimensions"):
        cache.put_many({"h": [1.0, 2.0]})
    with pytest.raises(ValueError, match="returned 0 vectors"):
        cache.ensure({"h": "text"}, lambda texts: [])


def test_cache_round_trips_float32(cache: EmbeddingCache) -> None:
    vectors, new = cache.ensure({"h": "text"}, fake_embed)
    assert new == 1
    assert cache.get_many(["h"]) == vectors
