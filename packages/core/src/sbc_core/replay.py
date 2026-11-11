"""Record and replay model calls, so local runs needn't spend OpenRouter credit (docs/local/LOCAL_PLAN.md L7).

``ReplayGateway`` has the three methods of :class:`sbc_core.llm.OpenRouterClient` that the app uses (``chat``,
``embed``, ``rerank``) and is set by ``LLM_MODE`` (local runs only; the API refuses it elsewhere):

- ``live`` (default): not used; the API calls OpenRouter directly.
- ``record``: every call goes to OpenRouter, and its request and result are saved as one JSON file under
  ``LLM_RECORDINGS_DIR`` (default ``.local/llm-recordings``), named by a hash of the request.
- ``replay``: every call is answered from those files with no network call, at no cost (usage ``cost_usd`` 0,
  ``request_id`` ``replay``). A request that wasn't recorded raises :class:`ReplayMissError`, a
  :class:`~sbc_core.llm.GatewayError`, so the turn ends the way an OpenRouter outage does: the safe fallback.

The same question in the same conversation state sends the same requests (prompts, tool results and
placeholder IDs are deterministic for a given bundle and configuration), so a recorded session replays
exactly. Changing a prompt, the bundle or a model changes the requests, and they must be recorded again.

Recordings hold SBC text (tool results inside the messages), so they stay out of the repository (``.local/``
is git-ignored), like the rest of the SBC data.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel

from sbc_core.llm import ChatResult, EmbeddingResult, GatewayError, RerankResult, Usage

LlmMode = Literal["live", "record", "replay"]


class ModelGateway(Protocol):
    """The model calls the app makes: :class:`~sbc_core.llm.OpenRouterClient` or a :class:`ReplayGateway`."""

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
    ) -> ChatResult: ...

    def embed(
        self,
        model: str,
        texts: Sequence[str],
        *,
        dimensions: int | None = None,
        input_type: Literal["query", "document"] | None = None,
    ) -> EmbeddingResult: ...

    def rerank(self, model: str, query: str, documents: Sequence[str]) -> RerankResult: ...


class ReplayMissError(GatewayError):
    """Replay mode got a request that was never recorded."""


def request_key(kind: str, request: Mapping[str, Any]) -> str:
    """A stable name for a request: the sha256 of its canonical JSON."""
    canonical = json.dumps({"kind": kind, **request}, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _replayed(usage: Usage) -> Usage:
    return usage.model_copy(update={"cost_usd": 0.0, "latency_ms": 0, "request_id": "replay"})


class ReplayGateway:
    """Records model calls through ``inner`` (``record``) or answers them from disk (``replay``)."""

    def __init__(self, inner: ModelGateway | None, directory: Path | str, mode: LlmMode) -> None:
        if mode == "live":
            raise ValueError(
                "ReplayGateway is for record or replay mode; live mode calls OpenRouter directly"
            )
        if mode == "record" and inner is None:
            raise ValueError("record mode needs a real gateway (an OpenRouter key)")
        self._inner = inner
        self._dir = Path(directory)
        self._mode = mode

    @property
    def mode(self) -> LlmMode:
        return self._mode

    def _path(self, kind: str, key: str) -> Path:
        return self._dir / kind / f"{key}.json"

    def _call[R: BaseModel](
        self, kind: str, request: dict[str, Any], result_type: type[R], live: Any, replayed: Any
    ) -> R:
        key = request_key(kind, request)
        path = self._path(kind, key)
        if self._mode == "replay":
            if not path.exists():
                raise ReplayMissError(
                    f"replay: no recorded {kind} call {key[:12]} (record it with LLM_MODE=record)"
                )
            saved = result_type.model_validate(json.loads(path.read_text())["result"])
            result: R = replayed(saved)
            return result
        fresh: R = live()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"kind": kind, "request": request, "result": fresh.model_dump(mode="json")}
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=1, default=str))
        return fresh

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
        request = {
            "model": model, "messages": list(messages), "fallbacks": list(fallbacks), "tools": list(tools),
            "tool_choice": tool_choice, "effort": effort, "max_tokens": max_tokens,
            "temperature": temperature, "response_format": response_format,
        }  # fmt: skip

        def live() -> ChatResult:
            assert self._inner is not None  # noqa: S101 - checked in __init__ for record mode
            return self._inner.chat(
                model, messages, fallbacks=fallbacks, tools=tools, tool_choice=tool_choice, effort=effort,
                max_tokens=max_tokens, temperature=temperature, response_format=response_format,
            )  # fmt: skip

        def replayed(saved: ChatResult) -> ChatResult:
            return saved.model_copy(update={"usage": _replayed(saved.usage)})

        return self._call("chat", request, ChatResult, live, replayed)

    def embed(
        self,
        model: str,
        texts: Sequence[str],
        *,
        dimensions: int | None = None,
        input_type: Literal["query", "document"] | None = None,
    ) -> EmbeddingResult:
        request = {"model": model, "texts": list(texts), "dimensions": dimensions, "input_type": input_type}

        def live() -> EmbeddingResult:
            assert self._inner is not None  # noqa: S101 - checked in __init__ for record mode
            return self._inner.embed(model, texts, dimensions=dimensions, input_type=input_type)

        def replayed(saved: EmbeddingResult) -> EmbeddingResult:
            return saved.model_copy(update={"usage": tuple(_replayed(u) for u in saved.usage)})

        return self._call("embeddings", request, EmbeddingResult, live, replayed)

    def rerank(self, model: str, query: str, documents: Sequence[str]) -> RerankResult:
        request = {"model": model, "query": query, "documents": list(documents)}

        def live() -> RerankResult:
            assert self._inner is not None  # noqa: S101 - checked in __init__ for record mode
            return self._inner.rerank(model, query, documents)

        def replayed(saved: RerankResult) -> RerankResult:
            return saved.model_copy(update={"usage": _replayed(saved.usage)})

        return self._call("rerank", request, RerankResult, live, replayed)
