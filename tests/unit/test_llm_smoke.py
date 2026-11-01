"""Live gateway smoke test, run against a mocked OpenRouter."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from sbc_core.config import load_config
from sbc_core.llm import OpenRouterClient
from sbc_core.llm_smoke import DOCUMENTS, RELEVANT, main, run

MODELS = load_config("broker", "local").models
DIMS = MODELS.embeddings.dimensions or 4


def _vector(i: int) -> list[float]:
    return [1.0 if k == i else 0.0 for k in range(DIMS)]


def _handler(*, reject_input_type: bool = False, rerank_first: int = RELEVANT) -> Any:
    def handle(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["provider"] == {"data_collection": "deny", "zdr": True}
        usage = {"prompt_tokens": 5, "total_tokens": 5, "cost": 0.000001}
        if request.url.path.endswith("/embeddings"):
            if reject_input_type and "input_type" in body:
                return httpx.Response(400, json={"error": {"message": "unknown parameter input_type"}})
            # Documents get one-hot vectors; the query matches the relevant document.
            n = len(body["input"])
            vectors = [_vector(i) for i in range(n)] if n > 1 else [_vector(RELEVANT)]
            data = [{"index": i, "embedding": v} for i, v in enumerate(vectors)]
            return httpx.Response(200, json={"data": data, "model": body["model"], "usage": usage})
        if request.url.path.endswith("/rerank"):
            order = [rerank_first] + [i for i in range(len(DOCUMENTS)) if i != rerank_first]
            results = [{"index": i, "relevance_score": 1.0 - n / 10} for n, i in enumerate(order)]
            return httpx.Response(200, json={"results": results, "model": body["model"], "usage": usage})
        return httpx.Response(
            200,
            json={
                "id": "g",
                "model": body["model"],
                "usage": usage,
                "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": "OK"}}],
            },
        )

    return handle


def _client(handler: Any) -> OpenRouterClient:
    return OpenRouterClient(SecretStr("k"), transport=httpx.MockTransport(handler), sleep=lambda _: None)


def test_all_checks_pass_on_a_well_behaved_api() -> None:
    checks = run(_client(_handler()), MODELS)
    assert [(c.name, c.ok) for c in checks] == [
        ("embeddings shape", True),
        ("embeddings relevance", True),
        ("rerank", True),
        ("chat", True),
    ]
    assert "cost" not in checks[0].detail or "$" in checks[0].detail


def test_rejected_input_type_is_diagnosed() -> None:
    (check, *_) = run(_client(_handler(reject_input_type=True)), MODELS)
    assert not check.ok
    assert "WITHOUT input_type succeeds" in check.detail


def test_wrong_rerank_order_fails() -> None:
    checks = {c.name: c for c in run(_client(_handler(rerank_first=0)), MODELS)}
    assert not checks["rerank"].ok


def test_no_key_skips(capsys: pytest.CaptureFixture[str]) -> None:
    assert main() == 2
    assert "OPENROUTER_API_KEY_BROKER" in capsys.readouterr().err
