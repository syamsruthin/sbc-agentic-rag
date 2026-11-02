"""The tools a bot may call in one turn, their JSON schemas for the model, and the per-turn call limits.

Only tools that are both implemented and listed in the bot profile are offered; any other name is refused, so
the public bot can never reach a broker-only tool (PLAN.md 3.8, L3). Arguments arrive as the model's JSON and
are validated before a tool runs. Every call, refused or not, is recorded for the trace and the
tool-selection metric.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ValidationError

from sbc_core.config import TurnBudget
from sbc_tools import tools as t
from sbc_tools.context import ToolContext, ToolError


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_model: type[BaseModel]
    run: Callable[[ToolContext, Any], BaseModel]

    def schema(self) -> dict[str, Any]:
        """OpenAI-style function definition (OpenRouter passes it on to the model)."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_model.model_json_schema(),
            },
        }


TOOLS: dict[str, Tool] = {
    tool.name: tool
    for tool in (
        Tool(
            "find_plans",
            "Find plans by name, contract code, HIOS id or SBC id, or list plans by state, market, plan type "
            "and metal tier. If a name matches several plans, don't choose one: ask the user.",
            t.FindPlansInput,
            t.find_plans,
        ),
        Tool(
            "get_plan_facts",
            "Exact SBC values for resolved plans: Important Questions (deductible, out-of-pocket limit, "
            "referrals...), benefit rows by service ID or drug tag with their limitations, coverage examples "
            "and the Excluded / Other Covered Services lists. Use it first for anything in the SBC template.",
            t.GetPlanFactsInput,
            t.get_plan_facts,
        ),
        Tool(
            "compare_plans",
            "One comparison table of the same aspects across 2 or more resolved plans, in one call.",
            t.ComparePlansInput,
            t.compare_plans,
        ),
        Tool(
            "search_sbc_text",
            "Search the SBC text of resolved plans for anything outside the fixed template fields: "
            "conditions, "
            "footnotes, rights and appeals, whether something is mentioned. Something not found is not "
            "'not covered'; say the SBC doesn't mention it.",
            t.SearchSbcTextInput,
            t.search_sbc_text,
        ),
        Tool(
            "ask_clarifying_question",
            "Ask the user one question when the plan, state or request is unclear. Never guess a plan.",
            t.AskClarifyingQuestionInput,
            t.ask_clarifying_question,
        ),
    )
}


@dataclass(frozen=True)
class ToolResult:
    name: str
    arguments: dict[str, Any]
    output: dict[str, Any] | None = None
    error: dict[str, str] | None = None

    @property
    def ok(self) -> bool:
        return self.error is None

    def content(self) -> str:
        """The tool message for the model."""
        return json.dumps(self.output if self.ok else self.error, ensure_ascii=False, separators=(",", ":"))


@dataclass
class Toolbox:
    ctx: ToolContext
    budget: TurnBudget
    calls: list[ToolResult] = field(default_factory=list)
    _seen: Counter[str] = field(default_factory=Counter)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(n for n in self.ctx.profile.tools if n in TOOLS)

    def schemas(self) -> list[dict[str, Any]]:
        return [TOOLS[n].schema() for n in self.names]

    def call(self, name: str, arguments: str | dict[str, Any]) -> ToolResult:
        try:
            loaded = json.loads(arguments) if isinstance(arguments, str) else arguments
        except json.JSONDecodeError:
            loaded = None
        if isinstance(loaded, dict):
            result = self._run(name, dict(loaded))
        else:
            result = self._error(name, {}, ToolError("invalid_arguments", "arguments must be a JSON object"))
        self.calls.append(result)
        return result

    def _run(self, name: str, args: dict[str, Any]) -> ToolResult:
        try:
            if name not in self.names:
                raise ToolError("tool_not_available", f"{name} is not available to this assistant")
            if len(self.calls) >= self.budget.max_tool_calls:
                raise ToolError(
                    "limit_reached", "tool-call limit for this turn reached; answer with what you have"
                )
            signature = name + json.dumps(args, sort_keys=True)
            if self._seen[signature] >= self.budget.max_identical_tool_calls:
                raise ToolError("limit_reached", "this exact call was already made; use its result")
            self._seen[signature] += 1
            tool = TOOLS[name]
            try:
                parsed = tool.input_model.model_validate(args)
            except ValidationError as exc:
                problems = "; ".join(
                    f"{'.'.join(str(p) for p in e['loc']) or 'arguments'}: {e['msg']}" for e in exc.errors()
                )
                raise ToolError("invalid_arguments", problems) from None
            output = tool.run(self.ctx, parsed)
        except ToolError as exc:
            return self._error(name, args, exc)
        return ToolResult(name, args, output=output.model_dump(mode="json"))

    @staticmethod
    def _error(name: str, args: dict[str, Any], exc: ToolError) -> ToolResult:
        return ToolResult(name, args, error={"error": exc.code, "message": exc.message})
