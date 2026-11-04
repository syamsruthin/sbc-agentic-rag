"""An API on the fixture bundle with scripted models and in-process stores (no network, no AWS)."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver

from sbc_api.app import create_app
from sbc_api.deps import ApiState, build_state, memory_stores
from sbc_core.config import BotName, EnvSettings, Settings
from tests.unit.test_graph import ScriptedChat

REPO = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def bundle(tmp_path_factory: pytest.TempPathFactory) -> Path:
    from tests.unit.test_bundle import make_bundle

    return make_bundle(tmp_path_factory.mktemp("bundle"))


class Harness:
    """Builds app instances that share only what Lambda instances share: the checkpoints and the stores."""

    def __init__(self, bundle: Path) -> None:
        self.env = EnvSettings.model_validate({"app_env": "local", "facts_bundle_path": str(bundle)})
        self.savers = {bot: InMemorySaver() for bot in BotName}
        self.stores = memory_stores()
        self.chats: dict[BotName, ScriptedChat] = {}

    def state(self, **chats: ScriptedChat) -> ApiState:
        self.chats = {BotName(k): v for k, v in chats.items()}

        def chat_for(bot: BotName, settings: Settings) -> ScriptedChat:
            return self.chats.get(bot) or ScriptedChat()

        return build_state(
            self.env, config_dir=REPO / "config", chat_factory=chat_for,
            saver_factory=lambda bot, s: self.savers[bot], stores=self.stores,
        )  # fmt: skip

    def client(self, **chats: ScriptedChat) -> TestClient:
        return TestClient(create_app(self.state(**chats)))


@pytest.fixture
def harness(bundle: Path) -> Harness:
    return Harness(bundle)


def parse_sse(text: str) -> list[tuple[str, dict[str, Any]]]:
    """``[(event, data), ...]``; keepalive comments are returned as ``(":", {})``."""
    out: list[tuple[str, dict[str, Any]]] = []
    for block in text.split("\n\n"):
        if not block.strip():
            continue
        if block.startswith(":"):
            out.append((":", {}))
            continue
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        out.append((lines["event"], json.loads(lines["data"])))
    return out


Post = Callable[..., list[tuple[str, dict[str, Any]]]]


def post_sse(client: TestClient, path: str, body: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    response = client.post(path, json=body)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    return parse_sse(response.text)
