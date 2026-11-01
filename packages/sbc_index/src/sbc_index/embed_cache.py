"""Embedding cache keyed by ``(model, dimensions, chunk_hash)`` (PLAN.md section 3.2.5).

Chunk hashes are content addresses, so a cached vector is valid for as long as the model and dimension
setting stay the same. Only chunks missing from the cache are sent to OpenRouter. The cache file is archived
with each bundle, so a lost Qdrant cluster can be rebuilt without re-embedding.
"""

from __future__ import annotations

import sqlite3
from array import array
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS embedding (
    model TEXT NOT NULL, dimensions INTEGER NOT NULL, chunk_hash TEXT NOT NULL, vector BLOB NOT NULL,
    PRIMARY KEY (model, dimensions, chunk_hash)
) WITHOUT ROWID;
"""
_QUERY_BATCH = 500

Embedder = Callable[[Sequence[str]], Sequence[Sequence[float]]]


def _pack(vector: Sequence[float]) -> bytes:
    return array("f", vector).tobytes()


def _unpack(blob: bytes) -> tuple[float, ...]:
    values = array("f")
    values.frombytes(blob)
    return tuple(values)


class EmbeddingCache:
    def __init__(self, path: Path | str, model: str, dimensions: int) -> None:
        self.model, self.dimensions = model, dimensions
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path)
        self._db.executescript(SCHEMA)

    def close(self) -> None:
        self._db.close()

    def __enter__(self) -> EmbeddingCache:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def get_many(self, chunk_hashes: Sequence[str]) -> dict[str, tuple[float, ...]]:
        found: dict[str, tuple[float, ...]] = {}
        for start in range(0, len(chunk_hashes), _QUERY_BATCH):
            batch = list(chunk_hashes[start : start + _QUERY_BATCH])
            placeholders = ",".join("?" * len(batch))
            # Only "?" placeholders are interpolated.
            sql = (
                "SELECT chunk_hash, vector FROM embedding WHERE model = ? AND dimensions = ? "  # noqa: S608
                f"AND chunk_hash IN ({placeholders})"
            )
            rows = self._db.execute(sql, (self.model, self.dimensions, *batch))
            found.update((h, _unpack(v)) for h, v in rows)
        return found

    def put_many(self, vectors: Mapping[str, Sequence[float]]) -> None:
        bad = [h for h, v in vectors.items() if len(v) != self.dimensions]
        if bad:
            raise ValueError(f"{len(bad)} vectors do not have {self.dimensions} dimensions, e.g. {bad[0]}")
        with self._db:
            self._db.executemany(
                "INSERT OR REPLACE INTO embedding VALUES (?, ?, ?, ?)",
                [(self.model, self.dimensions, h, _pack(v)) for h, v in vectors.items()],
            )

    def ensure(
        self, texts: Mapping[str, str], embed: Embedder, *, batch_size: int = 128
    ) -> tuple[dict[str, tuple[float, ...]], int]:
        """Vectors for every ``chunk_hash -> text``; embeds and stores only the missing ones.

        Returns the vectors and how many were newly embedded. Each embedded batch is committed at once, so an
        interrupted build resumes where it stopped.
        """
        hashes = list(texts)
        vectors = self.get_many(hashes)
        missing = [h for h in hashes if h not in vectors]
        for start in range(0, len(missing), batch_size):
            batch = missing[start : start + batch_size]
            got = embed([texts[h] for h in batch])
            if len(got) != len(batch):
                raise ValueError(f"embedder returned {len(got)} vectors for {len(batch)} texts")
            # Round-trip through float32 so fresh and cached vectors are identical.
            new = {h: _unpack(_pack(v)) for h, v in zip(batch, got, strict=True)}
            self.put_many(new)
            vectors.update(new)
        return vectors, len(missing)
