"""BM25 sparse vectors for Qdrant's ``bm25`` named vector (PLAN.md sections 3.2.4 and 3.4.3).

Term weights come from this local tokenizer, not an ML model. Documents get the BM25 term-frequency part
(saturated by ``k1`` and length-normalized by ``b`` against the corpus average length); Qdrant applies the
IDF part itself through the collection's ``Modifier.IDF``. Queries get weight 1 per distinct term, so the
score of a query against a chunk is the usual BM25 sum.

Both the index build and the runtime query path use this module, so they always tokenize the same way.
Bump :data:`TOKENIZER_VERSION` whenever tokenization changes; the collection must then be rebuilt.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass

from sbc_core.types import SparseVector

TOKENIZER_VERSION = 1

# Dollar amounts and percentages are kept as single tokens ("$1000", "30%") because SBC questions often
# hinge on them; commas inside numbers are dropped so "$1,000" and "$1000" match.
_TOKEN_RE = re.compile(r"\$?\d[\d,]*(?:\.\d+)?%?|[a-z0-9]+(?:'[a-z]+)?")
_STOP_WORDS = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "but",
        "by",
        "for",
        "from",
        "has",
        "have",
        "if",
        "in",
        "into",
        "is",
        "it",
        "its",
        "of",
        "on",
        "or",
        "such",
        "that",
        "the",
        "their",
        "then",
        "there",
        "these",
        "they",
        "this",
        "to",
        "was",
        "were",
        "will",
        "with",
        "you",
        "your",
    ]
)


def tokenize(text: str) -> list[str]:
    text = unicodedata.normalize("NFKC", text).lower().replace("’", "'").replace("‘", "'")
    tokens = []
    for raw in _TOKEN_RE.findall(text):
        token = raw.replace(",", "").removesuffix("'s")
        if token and token not in _STOP_WORDS:
            tokens.append(token)
    return tokens


def token_id(token: str) -> int:
    """Stable 32-bit index for a token (Qdrant sparse indices are uint32)."""
    return int.from_bytes(hashlib.blake2b(token.encode(), digest_size=4).digest(), "little")


@dataclass(frozen=True)
class Bm25Encoder:
    avg_doc_len: float
    k1: float = 1.2
    b: float = 0.75

    @classmethod
    def fit(cls, documents: Iterable[str], *, k1: float = 1.2, b: float = 0.75) -> Bm25Encoder:
        lengths = [len(tokenize(d)) for d in documents]
        avg = sum(lengths) / len(lengths) if lengths else 1.0
        return cls(avg_doc_len=max(avg, 1.0), k1=k1, b=b)

    def encode_document(self, text: str) -> SparseVector:
        tokens = tokenize(text)
        norm = self.k1 * (1 - self.b + self.b * len(tokens) / self.avg_doc_len)
        weights: defaultdict[int, float] = defaultdict(float)
        for token, tf in Counter(tokens).items():
            weights[token_id(token)] += tf * (self.k1 + 1) / (tf + norm)
        return _sparse(weights)

    @staticmethod
    def encode_query(text: str) -> SparseVector:
        weights: defaultdict[int, float] = defaultdict(float)
        for token in set(tokenize(text)):
            weights[token_id(token)] += 1.0
        return _sparse(weights)

    def params(self) -> dict[str, float | int]:
        return {
            "tokenizer_version": TOKENIZER_VERSION,
            "avg_doc_len": self.avg_doc_len,
            "k1": self.k1,
            "b": self.b,
        }


def _sparse(weights: dict[int, float]) -> SparseVector:
    items = sorted(weights.items())
    return SparseVector(indices=tuple(i for i, _ in items), values=tuple(float(w) for _, w in items))
