"""Synthesize both stacks once for the template tests (no AWS account or credentials needed)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import aws_cdk as cdk
import pytest
from aws_cdk.assertions import Template

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sbc_infra import config  # noqa: E402
from sbc_infra.stack import AppStack, FoundationStack  # noqa: E402

CONTEXT = {
    **json.loads((ROOT / "cdk.json").read_text())["context"],
    "apiImageTag": "abc123-2027.2",
    "alarmEmail": "alerts@example.com",
    "warmer": "true",
    "demoUserIds": '["11111111-2222-3333-4444-555555555555"]',
}


@pytest.fixture(scope="session")
def stacks() -> dict[str, Any]:
    app = cdk.App(context=CONTEXT)
    settings = config.load(app.node)
    env = cdk.Environment(account="123456789012", region=config.REGION)
    foundation = FoundationStack(app, "SbcFoundation-test", settings=settings, env=env)
    app_stack = AppStack(app, "SbcApp-test", settings=settings, foundation=foundation, env=env)
    return {"foundation": Template.from_stack(foundation), "app": Template.from_stack(app_stack)}
