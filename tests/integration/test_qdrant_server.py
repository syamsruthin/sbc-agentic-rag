"""Loader and hybrid search against a real Qdrant server (payload indexes, quantization, IDF modifier).

Opt-in: set QDRANT_TEST_URL (and QDRANT_TEST_API_KEY for Qdrant Cloud). For a local server:
``docker run -p 6333:6333 qdrant/qdrant``. The test creates and then deletes ``itest__sbc_chunks__test_1``.
Embeddings are faked, so no OpenRouter key is needed.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from qdrant_client import QdrantClient

from sbc_index.embed_cache import EmbeddingCache
from sbc_index.qdrant_loader import collection_name, load
from sbc_retrieval.qdrant_search import QdrantSearchIndex
from sbc_retrieval.text_search import TextSearcher
from tests.unit.test_bundle import make_bundle
from tests.unit.test_qdrant_loader import DIMS, fake_embed
from tests.unit.test_text_search import SETTINGS

URL = os.environ.get("QDRANT_TEST_URL")
pytestmark = [pytest.mark.integration, pytest.mark.skipif(not URL, reason="QDRANT_TEST_URL not set")]
PREFIX = "itest"


@pytest.fixture
def client() -> Iterator[QdrantClient]:
    qdrant = QdrantClient(url=URL, api_key=os.environ.get("QDRANT_TEST_API_KEY") or None, timeout=60)
    name = collection_name(PREFIX, "test.1")
    if qdrant.collection_exists(name):
        qdrant.delete_collection(name)
    yield qdrant
    if qdrant.collection_exists(name):
        qdrant.delete_collection(name)
    qdrant.close()


def test_load_and_search_on_a_server(tmp_path: Path, client: QdrantClient) -> None:
    with EmbeddingCache(tmp_path / "emb.sqlite", "test/embed", DIMS) as cache:
        report = load(client, make_bundle(tmp_path), prefix=PREFIX, embed=fake_embed, cache=cache)
    info = client.get_collection(report.collection)
    assert set(info.payload_schema) >= {"plan_keys", "plan_years", "states", "markets", "chunk_type"}
    assert info.config.quantization_config is not None

    settings = SETTINGS.model_copy(update={"rerank_enabled": False})
    result = TextSearcher(QdrantSearchIndex(client, report.collection), settings).search(
        ["2027:100000000001"], "specialist visit"
    )
    assert result.hits[0].section_path.endswith("> Specialist visit")
    assert all(h.plan_keys == ("2027:100000000001",) for h in result.hits)
