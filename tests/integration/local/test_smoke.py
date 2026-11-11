"""The local stack's smoke test (scripts/local/smoke.py) as an integration test. It runs only when the stack
is up with the API in replay mode, so it never spends OpenRouter credit (docs/local/LOCAL_PLAN.md L9).

    scripts/local/up.sh && make api-replay      # the API in another terminal
    uv run pytest -m integration tests/integration/local

It reads the stack's settings from infra/local/stack.env and .local/runtime.env itself (the tests' conftest
clears the app's variables from the environment).
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import httpx
import pytest
from dotenv import dotenv_values

pytestmark = pytest.mark.integration
REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "scripts" / "local" / "smoke.py"
EDGE = f"http://localhost:{os.environ.get('SBC_PORT_EDGE', '8088')}"


def _llm_mode() -> str | None:
    try:
        deep = httpx.get(f"{EDGE}/api/healthz", params={"deep": 1}, timeout=3).json()["deep"]
    except (httpx.HTTPError, KeyError, ValueError):
        return None
    return str(deep.get("llm", "live"))


def test_every_route_and_guard_through_the_edge(monkeypatch: pytest.MonkeyPatch) -> None:
    mode = _llm_mode()
    if mode is None:
        pytest.skip("the local stack isn't up (scripts/local/up.sh, then make api-replay)")
    if mode != "replay":
        pytest.skip(f"the API is in {mode} mode; the smoke test runs only in replay mode (no cost)")
    runtime = REPO / ".local" / "runtime.env"
    if not runtime.exists():
        pytest.skip(".local/runtime.env is missing: run scripts/local/up.sh")
    for path in (REPO / "infra" / "local" / "stack.env", runtime):
        for name, value in dotenv_values(path).items():
            if value is not None:
                monkeypatch.setenv(name, value)
    spec = importlib.util.spec_from_file_location("local_smoke", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    smoke = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, smoke)  # its dataclasses look their module up
    spec.loader.exec_module(smoke)
    assert smoke.main([]) == 0
