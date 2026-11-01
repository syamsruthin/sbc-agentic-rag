"""Qdrant collection loader against Qdrant's in-memory local mode."""

from __future__ import annotations

import hashlib
import warnings
from collections.abc import Iterator, Sequence
from pathlib import Path

import pytest
from qdrant_client import QdrantClient

from sbc_index.embed_cache import EmbeddingCache
from sbc_index.qdrant_loader import LoadError, collection_name, load, point_id, read_chunks
from tests.unit.test_bundle import make_bundle

DIMS = 8


def fake_embed(texts: Sequence[str]) -> list[list[float]]:
    """Deterministic pseudo-embeddings: identical text, identical vector."""
    out = []
    for text in texts:
        digest = hashlib.sha256(text.encode()).digest()
        out.append([b / 255 + 0.01 for b in digest[:DIMS]])
    return out


class CountingEmbedder:
    def __init__(self) -> None:
        self.calls = 0
        self.texts = 0

    def __call__(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls += 1
        self.texts += len(texts)
        return fake_embed(texts)


@pytest.fixture
def qdrant() -> Iterator[QdrantClient]:
    client = QdrantClient(":memory:")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)  # local mode ignores payload indexes and says so
        yield client
    client.close()


@pytest.fixture
def cache(tmp_path: Path) -> Iterator[EmbeddingCache]:
    with EmbeddingCache(tmp_path / "emb.sqlite", "test/embed", DIMS) as c:
        yield c


def test_collection_name() -> None:
    assert collection_name("dev", "2027.1") == "dev__sbc_chunks__2027_1"
    with pytest.raises(LoadError):
        collection_name("Dev", "2027.1")
    with pytest.raises(LoadError):
        collection_name("dev", "..")


def test_point_ids_are_stable_uuids() -> None:
    assert point_id("abc") == point_id("abc")
    assert point_id("abc") != point_id("abd")


def test_read_chunks_groups_plans(tmp_path: Path) -> None:
    version, records = read_chunks(make_bundle(tmp_path))
    assert version == "test.1"
    shared = [r for r in records if len(r.plan_keys) == 2]
    assert shared, "the two synthetic plans share most chunks"
    assert all(r.markets == ("Individual",) and r.plan_years == (2027,) for r in records)
    header = [r for r in records if r.chunk_type == "header"]
    assert header


def test_load_creates_validates_and_reuses_the_cache(
    tmp_path: Path, qdrant: QdrantClient, cache: EmbeddingCache
) -> None:
    bundle = make_bundle(tmp_path)
    embedder = CountingEmbedder()
    report = load(qdrant, bundle, prefix="local", embed=embedder, cache=cache)
    _, records = read_chunks(bundle)
    assert report.collection == "local__sbc_chunks__test_1"
    assert report.points == len(records) == qdrant.count(report.collection).count
    assert report.newly_embedded == len(records) == embedder.texts
    assert report.plan_filter_checks == 2
    assert report.bm25["tokenizer_version"] == 1

    info = qdrant.get_collection(report.collection)
    assert info.config.params.sparse_vectors is not None
    assert "bm25" in info.config.params.sparse_vectors

    point = qdrant.retrieve(report.collection, [point_id(records[0].chunk_hash)], with_payload=True)[0]
    assert point.payload is not None
    assert point.payload["text"] == records[0].text
    assert point.payload["plan_keys"] == list(records[0].plan_keys)

    with pytest.raises(LoadError, match="already exists"):
        load(qdrant, bundle, prefix="local", embed=embedder, cache=cache)
    again = CountingEmbedder()
    report = load(qdrant, bundle, prefix="local", embed=again, cache=cache, recreate=True)
    assert (report.newly_embedded, again.calls) == (0, 0)


def test_cache_rejects_wrong_dimensions(cache: EmbeddingCache) -> None:
    with pytest.raises(ValueError, match="dimensions"):
        cache.put_many({"h": [1.0, 2.0]})
    with pytest.raises(ValueError, match="returned 0 vectors"):
        cache.ensure({"h": "text"}, lambda texts: [])


def test_cache_round_trips_float32(cache: EmbeddingCache) -> None:
    vectors, new = cache.ensure({"h": "text"}, fake_embed)
    assert new == 1
    assert cache.get_many(["h"]) == vectors
