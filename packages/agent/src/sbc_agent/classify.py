"""The classify node (PLAN.md 3.5.2): one small-model call that labels the turn.

The classifier only proposes. Plan mentions go to the resolver, which decides in code; service terms are
mapped by the taxonomy in code; risk flags pick refusals or prefixes. If the call fails or returns something
unreadable, the turn is treated as an unclassified request and goes to the agent loop, which is the safe path.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from sbc_agent.prompts import load
from sbc_core.config import ModelRole
from sbc_core.llm import ChatResult, GatewayError
from sbc_core.template import QuestionId

Intent = Literal[
    "lookup", "compare", "coverage_check", "scenario", "discovery", "explain_term", "follow_up", "other",
    "out_of_scope", "chitchat",
]  # fmt: skip


class ChatModel(Protocol):
    """The part of ``sbc_core.llm.OpenRouterClient`` the agent uses; tests pass a scripted stand-in."""

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


class Classification(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    intent: Intent
    plan_mentions: tuple[str, ...] = ()
    question_ids: tuple[QuestionId, ...] = ()
    service_terms: tuple[str, ...] = ()
    tiers: tuple[Literal["in_net", "out_net"], ...] = ()
    state: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")
    medical_advice: bool = False
    plan_recommendation: bool = False
    injection: bool = False
    abuse: bool = False


UNCLASSIFIED = Classification(intent="other")
"""Used when classification fails: no shortcut and no forced clarification, so the agent loop decides."""


def response_format() -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "classification",
            "strict": True,
            "schema": Classification.model_json_schema(),
        },
    }


def classify(chat: ChatModel, role: ModelRole, message: str, history: list[dict[str, str]]) -> tuple[
    Classification, ChatResult | None, str | None
]:  # fmt: skip
    """The turn's classification, the raw call (for usage) and an error note when it fell back."""
    system, _ = load("classify")
    messages = [{"role": "system", "content": system}, *history[-4:], {"role": "user", "content": message}]
    try:
        result = chat.chat(
            role.model, messages, effort=role.effort, max_tokens=400, response_format=response_format()
        )
    except GatewayError as exc:
        return UNCLASSIFIED, None, f"classify_failed: {exc}"
    try:
        return Classification.model_validate(json.loads(result.content or "")), result, None
    except (json.JSONDecodeError, ValidationError) as exc:
        return UNCLASSIFIED, result, f"classify_unreadable: {type(exc).__name__}"
