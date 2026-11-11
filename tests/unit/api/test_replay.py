"""LLM_MODE record/replay (sbc_core.replay, docs/local/LOCAL_PLAN.md L7): local runs that cost no credit."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest

from sbc_api.deps import build_state
from sbc_core.config import Env, EnvSettings
from sbc_core.llm import ChatResult, EmbeddingResult, GatewayError, RerankItem, RerankResult, Usage
from sbc_core.replay import ReplayGateway, ReplayMissError, request_key
from tests.unit.api.conftest import REPO


class FakeOpenRouter:
    """Counts calls and answers like OpenRouter would."""

    def __init__(self) -> None:
        self.calls = 0

    def _usage(self, kind: Any) -> Usage:
        return Usage(
            kind=kind, model="m", total_tokens=10, cost_usd=0.002, latency_ms=900, request_id="gen-1"
        )

    def chat(self, model: str, messages: Sequence[Mapping[str, Any]], **kwargs: Any) -> ChatResult:
        self.calls += 1
        return ChatResult(
            content=f"answer to {messages[-1]['content']}", finish_reason="stop", usage=self._usage("chat")
        )

    def embed(self, model: str, texts: Sequence[str], **kwargs: Any) -> EmbeddingResult:
        self.calls += 1
        return EmbeddingResult(vectors=tuple((0.5, 0.25) for _ in texts), usage=(self._usage("embeddings"),))

    def rerank(self, model: str, query: str, documents: Sequence[str]) -> RerankResult:
        self.calls += 1
        return RerankResult(
            results=(RerankItem(index=1, score=0.9), RerankItem(index=0, score=0.1)),
            usage=self._usage("rerank"),
        )


def _ask(gateway: ReplayGateway, text: str) -> ChatResult:
    return gateway.chat("anthropic/claude-haiku-5.5", [{"role": "user", "content": text}], effort="low")


def test_recorded_calls_replay_exactly_at_no_cost(tmp_path: Path) -> None:
    live = FakeOpenRouter()
    recorder = ReplayGateway(live, tmp_path, "record")
    first = _ask(recorder, "deductible?")
    vectors = recorder.embed("baai/bge-m3", ["q"], dimensions=1024, input_type="query")
    ranked = recorder.rerank("qwen/qwen3-reranker-8b", "q", ["a", "b"])
    assert live.calls == 3
    assert len(list(tmp_path.rglob("*.json"))) == 3

    player = ReplayGateway(None, tmp_path, "replay")  # no key, no network
    again = _ask(player, "deductible?")
    assert again.content == first.content
    assert (again.usage.cost_usd, again.usage.request_id, again.usage.latency_ms) == (0.0, "replay", 0)
    assert player.embed("baai/bge-m3", ["q"], dimensions=1024, input_type="query").vectors == vectors.vectors
    assert player.rerank("qwen/qwen3-reranker-8b", "q", ["a", "b"]).results == ranked.results
    assert live.calls == 3


def test_an_unrecorded_request_fails_like_an_outage(tmp_path: Path) -> None:
    player = ReplayGateway(None, tmp_path, "replay")
    with pytest.raises(ReplayMissError, match="LLM_MODE=record") as caught:
        _ask(player, "never recorded")
    assert isinstance(caught.value, GatewayError)  # the agent's safe fallback handles it


def test_request_keys_change_with_any_part_of_the_request() -> None:
    base = {"model": "m", "messages": [{"role": "user", "content": "a"}], "effort": "low"}
    assert request_key("chat", base) == request_key("chat", dict(reversed(list(base.items()))))
    assert request_key("chat", base) != request_key("chat", {**base, "effort": "high"})
    assert request_key("chat", base) != request_key(
        "chat", {**base, "messages": [{"role": "user", "content": "b"}]}
    )
    assert request_key("chat", base) != request_key("rerank", base)


def test_live_mode_and_recording_without_a_key_are_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="live mode"):
        ReplayGateway(FakeOpenRouter(), tmp_path, "live")
    with pytest.raises(ValueError, match="needs a real gateway"):
        ReplayGateway(None, tmp_path, "record")


def _env(bundle: Path, **values: Any) -> EnvSettings:
    return EnvSettings.model_validate({"facts_bundle_path": str(bundle), **values})


def test_replay_mode_needs_no_key_and_shows_in_health(bundle: Path, tmp_path: Path) -> None:
    env = _env(bundle, app_env="local", llm_mode="replay", llm_recordings_dir=str(tmp_path))
    state = build_state(env, config_dir=REPO / "config")
    assert all(rt.agent is not None for rt in state.bots.values())
    assert state.init["llm"] == "replay"


@pytest.mark.parametrize("mode", ["record", "replay"])
def test_record_and_replay_are_refused_outside_local_runs(bundle: Path, mode: str) -> None:
    env = _env(bundle, app_env=Env.DEV, llm_mode=mode)
    with pytest.raises(RuntimeError, match="local runs only"):
        build_state(env, config_dir=REPO / "config")
