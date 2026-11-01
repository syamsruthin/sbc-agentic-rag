"""BM25 sparse encoding."""

from __future__ import annotations

from sbc_core.bm25 import Bm25Encoder, token_id, tokenize


def test_tokenize_keeps_amounts_and_drops_stop_words() -> None:
    text = "Copayment of $1,000/visit, then 30% coinsurance; the provider’s fee"
    assert tokenize(text) == ["copayment", "$1000", "visit", "30%", "coinsurance", "provider", "fee"]


def test_token_ids_are_stable_32_bit() -> None:
    assert token_id("mri") == token_id("mri")
    assert 0 <= token_id("mri") < 2**32
    assert token_id("mri") != token_id("ct")


def test_document_weights_saturate_and_normalize_length() -> None:
    enc = Bm25Encoder(avg_doc_len=4)
    short = enc.encode_document("imaging mri")
    long = enc.encode_document("imaging mri scan scan scan scan scan scan")
    w_short = dict(zip(short.indices, short.values, strict=True))[token_id("mri")]
    w_long = dict(zip(long.indices, long.values, strict=True))[token_id("mri")]
    assert w_short > w_long  # same tf, longer document
    scan = dict(zip(long.indices, long.values, strict=True))[token_id("scan")]
    assert scan < enc.k1 + 1  # tf saturates below k1 + 1


def test_query_is_binary_and_sorted() -> None:
    q = Bm25Encoder.encode_query("MRI mri imaging")
    assert q.values == (1.0, 1.0)
    assert list(q.indices) == sorted(q.indices)


def test_fit_uses_average_length() -> None:
    assert Bm25Encoder.fit(["mri ct pet scan", "mri"]).avg_doc_len == 2.5
    assert Bm25Encoder.fit([]).avg_doc_len == 1.0
