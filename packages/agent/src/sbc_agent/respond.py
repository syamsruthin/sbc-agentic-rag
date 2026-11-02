"""Answering a turn once its plans are resolved (PLAN.md 3.5.2-3.5.6): fast path or agent loop, then compose,
verify and render, with the template fallback.

- **Fast path.** A lookup (or a follow-up lookup on a known plan) whose every requested item maps to the
  taxonomy: facts are fetched in code with ``get_plan_facts``, then one compose call writes the answer.
- **Agent loop.** Everything else: the agent model calls tools, at most ``budget.max_llm_calls`` model calls
  in all, and its final message is the draft.
- **Verify.** A draft that fails the firewall gets one retry with the violations as feedback; a second
  failure, or running out of calls, gives the template answer built from the turn's facts.

Facts live in a :class:`~sbc_tools.context.Turn` that exists only while this runs; nothing the model typed is
shown before ``verify`` passes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal

from sbc_agent.answer.fallback import template_answer
from sbc_agent.answer.render import RenderedAnswer, render
from sbc_agent.answer.verify import VerifierReport, verify
from sbc_agent.classify import ChatModel, Classification
from sbc_agent.prompts import load
from sbc_core.config import AppConfig, BotName
from sbc_core.interfaces import FactsReader
from sbc_core.llm import ChatResult, GatewayError, Usage
from sbc_core.taxonomy import Taxonomy
from sbc_tools.context import Clarification, TextSearch, ToolContext, Turn
from sbc_tools.toolbox import Toolbox

IN_NETWORK_TIERS = ("ihcp", "preferred", "in_net", "in_net_t1", "in_net_t2", "rx_level1")

STYLE = {
    BotName.BROKER: "Write for insurance brokers: concise, plan names by contract code, and a Markdown table "
    "(one row per item, one column per plan) when comparing plans.",
    BotName.PUBLIC: "Write in plain language a general reader understands, in short sentences, and explain "
    "an insurance term briefly the first time you use it.",
}
RECOMMENDATION = {
    "rank_by_stated_criteria": "You may rank plans by criteria the user states; name the criterion.",
    "tradeoffs_only": "Never recommend a plan or say which one is better for the user. Describe the "
    "trade-offs and suggest a licensed agent or marketplace navigator for personal advice.",
}
TITLE = {BotName.BROKER: "Broker Assistant", BotName.PUBLIC: "Plan Shopper"}
MEDICAL_PREFIX = "I can't advise on medical care; your doctor can help with that. Here's what the SBC says."
RECOMMENDATION_PREFIX = "I can't recommend a plan, but here's what each plan's SBC says."


@dataclass
class Deps:
    reader: FactsReader
    chat: ChatModel
    config: AppConfig
    taxonomy: Taxonomy
    search: TextSearch | None = None
    plan_year: int | None = None


@dataclass
class Trace:
    path: Literal["fast", "agent"] = "agent"
    usage: list[Usage] = field(default_factory=list)
    tools: list[dict[str, Any]] = field(default_factory=list)
    verifier: list[list[str]] = field(default_factory=list)
    """Violations of each rejected draft."""
    fallback_reason: str | None = None
    prompt_versions: dict[str, str] = field(default_factory=dict)


@dataclass
class Outcome:
    answer: RenderedAnswer | str | None
    """The rendered answer, a plain message, or None when the turn asks a clarifying question."""
    clarification: Clarification | None
    trace: Trace


def _system(name: str, deps: Deps) -> tuple[str, str]:
    text, version = load(name)
    bot = deps.config.bot
    year = str(deps.plan_year or "plan-year")
    for key, value in {
        "{bot_title}": TITLE[bot.name], "{plan_year}": year, "{style}": STYLE[bot.name],
        "{recommendation_rule}": RECOMMENDATION[bot.plan_recommendation_policy],
    }.items():  # fmt: skip
        text = text.replace(key, value)
    return text, version


def _tool_call_message(result: ChatResult) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": result.content or "",
        "tool_calls": [
            {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": c.arguments}}
            for c in result.tool_calls
        ],
    }


def _plans_context(turn: Turn) -> str:
    lines = [
        f"- {pid}: plan_key {p.plan_key}, contract code {p.contract_code}" for pid, p in turn.plans.items()
    ]
    if not lines:
        return "No plan is resolved for this turn. Use find_plans if the question needs one."
    return "Plans resolved for this turn (write their names as {{P1.name}}):\n" + "\n".join(lines)


class Responder:
    def __init__(self, deps: Deps, turn: Turn, classification: Classification) -> None:
        self.deps = deps
        self.turn = turn
        self.cls = classification
        self.trace = Trace()
        ctx = ToolContext(
            reader=deps.reader, profile=deps.config.bot, taxonomy=deps.taxonomy,
            retrieval=deps.config.retrieval, turn=turn, search=deps.search, plan_year=deps.plan_year,
        )  # fmt: skip
        self.toolbox = Toolbox(ctx, deps.config.budget)
        found = deps.reader.plans(turn.resolved_plan_keys)
        for key in turn.resolved_plan_keys:  # P1, P2... in resolution order, before any tool runs
            if key in found:
                turn.plan_id(found[key])
        self.calls_left = deps.config.budget.max_llm_calls

    # ------------------------------------------------------------------ model calls

    def _chat(self, messages: list[dict[str, Any]], *, tools: bool) -> ChatResult:
        role = self.deps.config.models.agent
        self.calls_left -= 1
        result = self.deps.chat.chat(
            role.model, messages, tools=self.toolbox.schemas() if tools else (), fallbacks=role.fallbacks,
            effort=role.effort, max_tokens=1500,
        )  # fmt: skip
        self.trace.usage.append(result.usage)
        return result

    def _finish(self, draft: str, *, require_values: bool) -> tuple[Outcome | None, VerifierReport]:
        report = verify(draft, self.turn, self.deps.config.bot, require_values=require_values)
        if report.ok:
            return Outcome(render(self._prefixed(draft), self.turn), None, self.trace), report
        self.trace.verifier.append([v.detail for v in report.violations])
        return None, report

    def _prefixed(self, draft: str) -> str:
        if self.cls.medical_advice:
            return f"{MEDICAL_PREFIX}\n\n{draft}"
        if (
            self.cls.plan_recommendation
            and self.deps.config.bot.plan_recommendation_policy == "tradeoffs_only"
        ):
            return f"{RECOMMENDATION_PREFIX}\n\n{draft}"
        return draft

    def _fallback(self, reason: str) -> Outcome:
        self.trace.fallback_reason = reason
        return Outcome(template_answer(self.turn, self.deps.config.bot), None, self.trace)

    def _record_tools(self) -> None:
        self.trace.tools = [
            {"name": c.name, "arguments": c.arguments, "ok": c.ok, "error": c.error}
            for c in self.toolbox.calls
        ]

    # ------------------------------------------------------------------ paths

    def fast_path_args(self) -> dict[str, Any] | None:
        """``get_plan_facts`` arguments when the turn qualifies for the fast path, else None."""
        c = self.cls
        if (
            c.intent not in ("lookup", "follow_up")
            or not self.turn.resolved_plan_keys
            or c.medical_advice
            or c.plan_recommendation
        ):
            return None
        services: list[str] = []
        drug_tags: list[str] = []
        for term in c.service_terms:
            match = self.deps.taxonomy.match_term(term)
            if not match or match.ambiguous:
                return None  # an unmapped or ambiguous term needs the agent
            services += match.service_ids
            drug_tags += [t for t in match.drug_tags if t != "any_drug"]
            if "any_drug" in match.drug_tags:
                services += self.deps.taxonomy.drug_service_ids
        if not (services or drug_tags or c.question_ids):
            return None
        tiers = (IN_NETWORK_TIERS if "in_net" in c.tiers else ()) + (
            ("out_net",) if "out_net" in c.tiers else ()
        )
        return {
            "plan_keys": list(self.turn.resolved_plan_keys), "question_ids": list(c.question_ids),
            "service_ids": list(dict.fromkeys(services)), "drug_tags": list(dict.fromkeys(drug_tags)),
            "tier_ids": list(tiers),
        }  # fmt: skip

    def run(self, message: str, history: list[dict[str, str]]) -> Outcome:
        try:
            args = self.fast_path_args()
            if args is not None:
                result = self.toolbox.call("get_plan_facts", args)
                if result.ok:
                    self.trace.path = "fast"
                    return self._compose(message, history, result.content())
            return self._agent(message, history)
        except GatewayError as exc:
            return self._fallback(f"model_unavailable: {exc}")
        finally:
            self._record_tools()

    def _compose(self, message: str, history: list[dict[str, str]], facts_json: str) -> Outcome:
        system, version = _system("compose", self.deps)
        self.trace.prompt_versions["compose"] = version
        facts = json.loads(facts_json)
        require_values = bool(facts.get("facts"))
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system},
            *history[-4:],
            {"role": "user", "content": f"{message}\n\nFacts (JSON):\n{facts_json}"},
        ]
        for _ in range(2):
            if self.calls_left <= 0:
                break
            draft = self._chat(messages, tools=False).content or ""
            outcome, report = self._finish(draft, require_values=require_values)
            if outcome is not None:
                return outcome
            messages += [
                {"role": "assistant", "content": draft},
                {"role": "user", "content": report.feedback()},
            ]
        return self._fallback("verifier_failed" if self.trace.verifier else "llm_call_limit")

    def _agent(self, message: str, history: list[dict[str, str]]) -> Outcome:
        system, version = _system("agent", self.deps)
        self.trace.prompt_versions["agent"] = version
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system},
            {"role": "system", "content": _plans_context(self.turn)},
            *history[-4:],
            {"role": "user", "content": message},
        ]
        rejected = 0
        while self.calls_left > 0:
            result = self._chat(messages, tools=True)
            if result.tool_calls:
                messages.append(_tool_call_message(result))
                for call in result.tool_calls:
                    tool = self.toolbox.call(call.name, call.arguments)
                    messages.append({"role": "tool", "tool_call_id": call.id, "content": tool.content()})
                if self.turn.clarification is not None:
                    return Outcome(None, self.turn.clarification, self.trace)
                continue
            draft = result.content or ""
            outcome, report = self._finish(draft, require_values=False)
            if outcome is not None:
                return outcome
            rejected += 1
            if rejected >= 2:
                return self._fallback("verifier_failed")
            messages += [
                {"role": "assistant", "content": draft},
                {"role": "user", "content": report.feedback()},
            ]
        return self._fallback("llm_call_limit")
