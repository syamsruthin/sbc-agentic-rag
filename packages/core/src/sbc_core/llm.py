"""OpenRouter gateway: chat completions, embeddings and rerank (PLAN.md section 3.6).

Every model call in the project goes through this module, so it is the one place that sets provider policy,
retries and usage accounting:

- **Data policy.** Each request asks OpenRouter to route only to providers that don't collect prompts and
  offer zero data retention (``provider.data_collection = "deny"``, ``provider.zdr = true``).
- **Retries.** Rate limits (429) and server errors (5xx) and timeouts are retried with exponential backoff,
  honoring ``Retry-After``. Other errors fail at once.
- **Usage.** Every successful call produces a :class:`Usage` record (tokens, OpenRouter's reported cost,
  latency) that is passed to the ``on_usage`` callback, so tracing and per-turn budgets can count it.

Error messages never include the API key or the request body.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from sbc_core.config import BotName, EnvSettings

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
_RETRY_STATUS = frozenset({408, 409, 429, 500, 502, 503, 504, 524, 529})
_MAX_BACKOFF_SECONDS = 20.0
EMBED_BATCH = 128


class GatewayError(Exception):
    """A model call failed. ``status`` is the HTTP status, or None for a network error."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Usage(_Model):
    kind: Literal["chat", "embeddings", "rerank"]
    model: str
    """The model that served the call, which can be a fallback."""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float | None = None
    """As reported by OpenRouter; None when the response doesn't include it."""
    latency_ms: int
    request_id: str | None = None


class ToolCall(_Model):
    id: str
    name: str
    arguments: str
    """JSON text exactly as the model produced it; the caller validates it."""


class ChatResult(_Model):
    content: str | None
    tool_calls: tuple[ToolCall, ...] = ()
    finish_reason: str | None = None
    usage: Usage


class EmbeddingResult(_Model):
    vectors: tuple[tuple[float, ...], ...]
    usage: tuple[Usage, ...]
    """One record per HTTP request (inputs are sent in batches)."""


class RerankItem(_Model):
    index: int = Field(ge=0)
    """Position of the document in the request."""
    score: float


class RerankResult(_Model):
    results: tuple[RerankItem, ...]
    """Best first."""
    usage: Usage


class OpenRouterClient:
    @classmethod
    def for_bot(cls, env: EnvSettings, bot: BotName, **kwargs: Any) -> OpenRouterClient:
        """A client with the bot's own OpenRouter key (each key has its own credit limit)."""
        key = env.openrouter_key_for(bot)
        if key is None:
            raise GatewayError(f"OPENROUTER_API_KEY_{bot.value.upper()} is not set")
        return cls(key, base_url=env.openrouter_base_url, **kwargs)

    def __init__(
        self,
        api_key: SecretStr,
        *,
        base_url: str = DEFAULT_BASE_URL,
        app_name: str = "sbc-agentic-rag",
        app_url: str = "https://github.com/syamsruthin/sbc-agentic-rag",
        timeout_seconds: float = 30.0,
        max_retries: int = 3,
        on_usage: Callable[[Usage], None] | None = None,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not api_key.get_secret_value():
            raise GatewayError("the OpenRouter API key is empty")
        self._client = httpx.Client(
            base_url=base_url.rstrip("/") + "/",
            headers={
                "Authorization": f"Bearer {api_key.get_secret_value()}",
                "HTTP-Referer": app_url,
                "X-Title": app_name,
            },
            timeout=timeout_seconds,
            transport=transport,
        )
        self._max_retries = max_retries
        self._on_usage = on_usage
        self._sleep = sleep

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> OpenRouterClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ------------------------------------------------------------------ transport

    def _post(self, path: str, body: dict[str, Any]) -> tuple[dict[str, Any], int, str | None]:
        body = {**body, "provider": {"data_collection": "deny", "zdr": True}}
        attempt = 0
        while True:
            start = time.monotonic()
            try:
                response = self._client.post(path, json=body)
            except httpx.TimeoutException:
                error: GatewayError = GatewayError(f"{path}: timed out")
                retry_after = None
            except httpx.TransportError as exc:
                error, retry_after = GatewayError(f"{path}: network error ({type(exc).__name__})"), None
            else:
                latency_ms = int((time.monotonic() - start) * 1000)
                if response.is_success:
                    data = _json(response, path)
                    if "error" in data:  # OpenRouter can report a provider failure with HTTP 200
                        raise GatewayError(f"{path}: {_error_message(data)}", response.status_code)
                    return data, latency_ms, response.headers.get("x-request-id") or data.get("id")
                message = _error_message(_json(response, path, strict=False))
                error = GatewayError(f"{path}: HTTP {response.status_code}: {message}", response.status_code)
                if response.status_code not in _RETRY_STATUS:
                    raise error
                retry_after = _retry_after(response.headers)
            if attempt >= self._max_retries:
                raise error
            self._sleep(min(_MAX_BACKOFF_SECONDS, retry_after if retry_after is not None else 2.0**attempt))
            attempt += 1

    def _record(self, usage: Usage) -> Usage:
        if self._on_usage is not None:
            self._on_usage(usage)
        return usage

    # ------------------------------------------------------------------ endpoints

    def chat(
        self,
        model: str,
        messages: Sequence[Mapping[str, Any]],
        *,
        fallbacks: Sequence[str] = (),
        tools: Sequence[Mapping[str, Any]] = (),
        tool_choice: str | Mapping[str, Any] | None = None,
        effort: Literal["low", "medium", "high"] | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        response_format: Mapping[str, Any] | None = None,
    ) -> ChatResult:
        """One chat completion. ``fallbacks`` become OpenRouter's ``models`` list after ``model``."""
        body: dict[str, Any] = {"model": model, "messages": list(messages), "usage": {"include": True}}
        if fallbacks:
            body["models"] = [model, *fallbacks]
        if tools:
            body["tools"] = list(tools)
        if tool_choice is not None:
            body["tool_choice"] = tool_choice
        if effort is not None:
            body["reasoning"] = {"effort": effort}
        if max_tokens is not None:
            body["max_tokens"] = max_tokens
        if temperature is not None:
            body["temperature"] = temperature
        if response_format is not None:
            body["response_format"] = response_format
        data, latency_ms, request_id = self._post("chat/completions", body)
        try:
            choice = data["choices"][0]
            message = choice["message"]
            calls = tuple(
                ToolCall(
                    id=c["id"], name=c["function"]["name"], arguments=c["function"].get("arguments") or "{}"
                )
                for c in message.get("tool_calls") or ()
            )
        except (KeyError, IndexError, TypeError) as exc:
            raise GatewayError(f"chat/completions: unexpected response shape ({exc!r})") from None
        usage = self._record(_usage("chat", data, model, latency_ms, request_id))
        return ChatResult(
            content=message.get("content"),
            tool_calls=calls,
            finish_reason=choice.get("finish_reason"),
            usage=usage,
        )

    def embed(
        self,
        model: str,
        texts: Sequence[str],
        *,
        dimensions: int | None = None,
        input_type: Literal["query", "document"] | None = None,
        batch_size: int = EMBED_BATCH,
    ) -> EmbeddingResult:
        """Embed ``texts`` in order, sending at most ``batch_size`` per request."""
        vectors: list[tuple[float, ...]] = []
        usages: list[Usage] = []
        for start in range(0, len(texts), batch_size):
            batch = list(texts[start : start + batch_size])
            body: dict[str, Any] = {"model": model, "input": batch, "encoding_format": "float"}
            if dimensions is not None:
                body["dimensions"] = dimensions
            if input_type is not None:
                body["input_type"] = input_type
            data, latency_ms, request_id = self._post("embeddings", body)
            try:
                items = sorted(data["data"], key=lambda d: d["index"])
                got = [tuple(float(x) for x in d["embedding"]) for d in items]
            except (KeyError, TypeError, ValueError) as exc:
                raise GatewayError(f"embeddings: unexpected response shape ({exc!r})") from None
            if len(got) != len(batch):
                raise GatewayError(f"embeddings: asked for {len(batch)} vectors, got {len(got)}")
            if dimensions is not None and any(len(v) != dimensions for v in got):
                raise GatewayError(f"embeddings: expected {dimensions} dimensions")
            vectors += got
            usages.append(self._record(_usage("embeddings", data, model, latency_ms, request_id)))
        return EmbeddingResult(vectors=tuple(vectors), usage=tuple(usages))

    def rerank(
        self, model: str, query: str, documents: Sequence[str], *, top_n: int | None = None
    ) -> RerankResult:
        """Scores for ``documents`` against ``query``, best first."""
        if not documents:
            raise GatewayError("rerank: no documents")
        body: dict[str, Any] = {"model": model, "query": query, "documents": list(documents)}
        if top_n is not None:
            body["top_n"] = top_n
        data, latency_ms, request_id = self._post("rerank", body)
        try:
            items = [
                RerankItem(index=int(r["index"]), score=float(r.get("relevance_score", r.get("score"))))
                for r in data.get("results") or data["data"]
            ]
        except (KeyError, TypeError, ValueError) as exc:
            raise GatewayError(f"rerank: unexpected response shape ({exc!r})") from None
        if any(i.index >= len(documents) for i in items):
            raise GatewayError("rerank: result index out of range")
        items.sort(key=lambda i: -i.score)
        return RerankResult(
            results=tuple(items), usage=self._record(_usage("rerank", data, model, latency_ms, request_id))
        )


def _json(response: httpx.Response, path: str, *, strict: bool = True) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError:
        if strict:
            raise GatewayError(f"{path}: response is not JSON", response.status_code) from None
        return {}
    return data if isinstance(data, dict) else {}


def _error_message(data: Mapping[str, Any]) -> str:
    error = data.get("error")
    if isinstance(error, dict):
        return str(error.get("message") or error.get("code") or "error")[:300]
    return str(error or "no error message")[:300]


def _retry_after(headers: httpx.Headers) -> float | None:
    try:
        return max(0.0, float(headers["retry-after"]))
    except (KeyError, ValueError):
        return None


def _usage(
    kind: Literal["chat", "embeddings", "rerank"], data: Mapping[str, Any], model: str, latency_ms: int,
    request_id: str | None,
) -> Usage:  # fmt: skip
    raw = data.get("usage") or {}
    prompt = int(raw.get("prompt_tokens") or raw.get("total_tokens") or 0)
    completion = int(raw.get("completion_tokens") or 0)
    cost = raw.get("cost")
    return Usage(
        kind=kind, model=str(data.get("model") or model), prompt_tokens=prompt, completion_tokens=completion,
        total_tokens=int(raw.get("total_tokens") or prompt + completion),
        cost_usd=float(cost) if cost is not None else None, latency_ms=latency_ms, request_id=request_id,
    )  # fmt: skip
