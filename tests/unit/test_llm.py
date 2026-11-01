"""OpenRouter gateway against a mocked HTTP transport."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from sbc_core.llm import GatewayError, OpenRouterClient, Usage

KEY = SecretStr("sk-or-test-key")
Handler = Callable[[httpx.Request], httpx.Response]


class Recorder:
    def __init__(self, *responses: httpx.Response | Exception) -> None:
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def body(self, n: int = 0) -> dict[str, Any]:
        data: dict[str, Any] = json.loads(self.requests[n].content)
        return data


def _client(
    handler: Handler, usages: list[Usage] | None = None, sleeps: list[float] | None = None
) -> OpenRouterClient:
    return OpenRouterClient(
        KEY,
        transport=httpx.MockTransport(handler),
        on_usage=usages.append if usages is not None else None,
        sleep=sleeps.append if sleeps is not None else lambda _: None,
    )


def _ok(data: dict[str, Any], **headers: str) -> httpx.Response:
    return httpx.Response(200, json=data, headers=headers)


CHAT = {
    "id": "gen-1",
    "model": "anthropic/claude-sonnet-5.5",
    "choices": [{
        "finish_reason": "tool_calls",
        "message": {"role": "assistant", "content": None, "tool_calls": [
            {"id": "call_1", "type": "function",
             "function": {"name": "get_plan_facts", "arguments": '{"plan_keys": ["2027:1"]}'}}
        ]},
    }],
    "usage": {"prompt_tokens": 120, "completion_tokens": 30, "total_tokens": 150, "cost": 0.00054},
}  # fmt: skip


def test_chat_sends_policy_fallbacks_and_effort_and_records_usage() -> None:
    rec, usages = Recorder(_ok(CHAT)), list[Usage]()
    with _client(rec, usages) as client:
        result = client.chat(
            "anthropic/claude-sonnet-5.5",
            [{"role": "user", "content": "hi"}],
            fallbacks=["anthropic/claude-haiku-5.5"],
            tools=[{"type": "function", "function": {"name": "get_plan_facts", "parameters": {}}}],
            effort="low",
            max_tokens=500,
        )
    req = rec.requests[0]
    assert req.url == "https://openrouter.ai/api/v1/chat/completions"
    assert req.headers["authorization"] == "Bearer sk-or-test-key"
    body = rec.body()
    assert body["provider"] == {"data_collection": "deny", "zdr": True}
    assert body["models"] == ["anthropic/claude-sonnet-5.5", "anthropic/claude-haiku-5.5"]
    assert body["reasoning"] == {"effort": "low"}
    assert body["usage"] == {"include": True}
    assert result.tool_calls[0].name == "get_plan_facts"
    assert json.loads(result.tool_calls[0].arguments) == {"plan_keys": ["2027:1"]}
    assert result.usage.cost_usd == pytest.approx(0.00054)
    assert usages == [result.usage]
    assert result.usage.request_id == "gen-1"


def test_embeddings_are_batched_ordered_and_checked() -> None:
    def page(n: int, start: int) -> httpx.Response:
        data = [{"index": i, "embedding": [float(start + i), 0.0]} for i in reversed(range(n))]
        return _ok({"model": "voyageai/voyage-4-lite", "data": data, "usage": {"total_tokens": 7}})

    rec, usages = Recorder(page(2, 0), page(1, 2)), list[Usage]()
    with _client(rec, usages) as client:
        result = client.embed("voyageai/voyage-4-lite", ["a", "b", "c"], dimensions=2, batch_size=2,
                              input_type="document")  # fmt: skip
    assert [v[0] for v in result.vectors] == [0.0, 1.0, 2.0]
    assert rec.body(0)["input"] == ["a", "b"]
    assert rec.body(0)["dimensions"] == 2
    assert rec.body(0)["input_type"] == "document"
    assert rec.body(1)["input"] == ["c"]
    assert len(usages) == 2
    assert usages[0].prompt_tokens == 7


def test_embedding_dimension_mismatch_is_an_error() -> None:
    rec = Recorder(_ok({"data": [{"index": 0, "embedding": [1.0]}]}))
    with _client(rec) as client, pytest.raises(GatewayError, match="dimensions"):
        client.embed("m", ["a"], dimensions=2)


def test_rerank_sorts_best_first_and_rejects_bad_indices() -> None:
    results = {"results": [{"index": 0, "relevance_score": 0.2}, {"index": 1, "relevance_score": 0.9}]}
    rec = Recorder(_ok(results), _ok({"results": [{"index": 5, "relevance_score": 1.0}]}))
    with _client(rec) as client:
        out = client.rerank("voyageai/rerank-3-lite", "q", ["d0", "d1"], top_n=2)
        assert [i.index for i in out.results] == [1, 0]
        assert rec.body()["top_n"] == 2
        with pytest.raises(GatewayError, match="out of range"):
            client.rerank("voyageai/rerank-3-lite", "q", ["d0"])
        with pytest.raises(GatewayError, match="no documents"):
            client.rerank("voyageai/rerank-3-lite", "q", [])


def test_retries_rate_limits_and_server_errors_with_backoff() -> None:
    sleeps: list[float] = []
    rec = Recorder(
        httpx.Response(429, json={"error": {"message": "slow down"}}, headers={"retry-after": "1.5"}),
        httpx.Response(502, json={"error": {"message": "bad gateway"}}),
        httpx.ConnectError("boom"),
        _ok(CHAT),
    )
    with _client(rec, sleeps=sleeps) as client:
        client.chat("m", [{"role": "user", "content": "hi"}])
    assert sleeps == [1.5, 2.0, 4.0]


def test_gives_up_after_max_retries_without_leaking_the_key() -> None:
    rec = Recorder(*[httpx.Response(503, json={"error": {"message": "down"}})] * 4)
    with _client(rec) as client, pytest.raises(GatewayError) as info:
        client.chat("m", [{"role": "user", "content": "hi"}])
    assert info.value.status == 503
    assert "down" in str(info.value)
    assert "sk-or" not in str(info.value)
    assert len(rec.requests) == 4


def test_client_errors_are_not_retried() -> None:
    rec = Recorder(httpx.Response(401, json={"error": {"message": "No auth credentials found"}}))
    with _client(rec) as client, pytest.raises(GatewayError, match="HTTP 401"):
        client.embed("m", ["a"])
    assert len(rec.requests) == 1


def test_error_inside_a_200_response() -> None:
    rec = Recorder(_ok({"error": {"message": "provider failed", "code": 502}}))
    with _client(rec) as client, pytest.raises(GatewayError, match="provider failed"):
        client.chat("m", [{"role": "user", "content": "hi"}])


def test_empty_key_is_rejected() -> None:
    with pytest.raises(GatewayError, match="empty"):
        OpenRouterClient(SecretStr(""))


def test_for_bot_uses_the_bot_key() -> None:
    from sbc_core.config import BotName, EnvSettings

    env = EnvSettings(openrouter_api_key_public=SecretStr("sk-or-public"))
    rec = Recorder(_ok(CHAT))
    with OpenRouterClient.for_bot(env, BotName.PUBLIC, transport=httpx.MockTransport(rec)) as client:
        client.chat("m", [{"role": "user", "content": "hi"}])
    assert rec.requests[0].headers["authorization"] == "Bearer sk-or-public"
    with pytest.raises(GatewayError, match="OPENROUTER_API_KEY_BROKER"):
        OpenRouterClient.for_bot(env, BotName.BROKER)
