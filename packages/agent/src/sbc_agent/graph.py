"""The turn graph (PLAN.md 3.5.2), wired with LangGraph.

    ingress -> classify -> refuse | chitchat | resolve
    resolve -> clarify (interrupt) | answer
    clarify -> (user's choice) -> resolve
    answer  -> clarify (the agent asked) | END

Nodes are plain functions over :class:`TurnState`. The state holds only serializable values (redacted
text, the classification, resolved plan keys, the reply), so a checkpointer can persist it between turns and
across a clarifying question. Tool results and facts live only inside the answer node.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterator, Mapping
from typing import Any, Literal, TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from sbc_agent.answer.render import RenderedAnswer
from sbc_agent.classify import Classification, classify
from sbc_agent.redact import redact
from sbc_agent.respond import Deps, Outcome, Responder
from sbc_core.types import PlanSummary
from sbc_retrieval.resolver import PlanResolver, Resolution
from sbc_tools.context import Clarification, ClarifyOption, Turn

MAX_MESSAGE_CHARS = 2000
NEEDS_PLAN = frozenset({"lookup", "compare", "coverage_check", "scenario"})

OUT_OF_SCOPE = (
    "I can only answer questions about what health plans' Summaries of Benefits and Coverage (SBCs) say: "
    "deductibles, copays and coinsurance, what's covered or excluded, coverage examples, and comparisons "
    "between plans. SBCs don't include premiums, and I can't help with claims, eligibility, enrollment, or "
    "provider and drug lookups. For those, please contact the plan."
)
ABUSE = "I can't help with that. I can answer questions about what a health plan's SBC says."
CHITCHAT = (
    "Hi! Ask me about a plan's deductible, out-of-pocket limit, copays, what it covers, or how plans compare."
)
TOO_LONG = "That message is too long for me. Please ask a shorter question about a plan's SBC."

ReplyKind = Literal["answer", "clarify", "message"]


class TurnState(TypedDict, total=False):
    # Session (kept across turns of a thread)
    history: list[dict[str, str]]
    last_plan_keys: list[str]
    clarifications_in_a_row: int
    # This turn
    message: str
    pii_found: list[str]
    ui_selected: list[str]
    user_state: str | None
    classification: dict[str, Any]
    resolved: list[str]
    clarification: dict[str, Any] | None
    reply: dict[str, Any]
    trace: dict[str, Any]


def _option_label(plan: PlanSummary) -> str:
    parts = [plan.plan_name, plan.contract_code, plan.network, plan.exchange, plan.state, plan.market]
    return " · ".join(p for p in parts if p)


def _clarification_for(resolution: Resolution, n_plans_needed: bool) -> Clarification:
    if resolution.ambiguities:
        amb = resolution.ambiguities[0]
        options = tuple(ClarifyOption(label=_option_label(p), plan_key=p.plan_key) for p in amb.candidates)
        more = " Or open the plan list to filter." if amb.more else ""
        return Clarification(
            question=f'I found several plans matching "{amb.mention}". Which one do you mean?{more}',
            options=options,
            open_plan_picker=amb.more,
        )
    if resolution.not_found:
        missing = ", ".join(f'"{m}"' for m in resolution.not_found)
        return Clarification(
            question=f"I couldn't find {missing} among the plans I can answer about. Which plan do you mean?",
            open_plan_picker=True,
        )
    what = "plans" if n_plans_needed else "plan"
    return Clarification(question=f"Which {what} would you like me to look at?", open_plan_picker=True)


def _message_reply(text: str) -> dict[str, Any]:
    return {"kind": "message", "markdown": text, "citations": [], "plan_keys": []}


def _answer_reply(answer: RenderedAnswer | str) -> dict[str, Any]:
    if isinstance(answer, str):
        return _message_reply(answer)
    return {
        "kind": "answer", "markdown": answer.markdown, "mode": answer.mode,
        "citations": [dataclasses.asdict(c) for c in answer.citations], "plan_keys": list(answer.plan_keys),
        "value_ids": list(answer.value_ids),
    }  # fmt: skip


def build_graph(deps: Deps, checkpointer: BaseCheckpointSaver[Any] | None = None) -> Any:
    bot = deps.config.bot
    resolver = PlanResolver(deps.reader, bot.plan_scope)

    def ingress(state: TurnState) -> dict[str, Any]:
        cleaned = redact(state.get("message", "")[: MAX_MESSAGE_CHARS + 1])
        return {
            "message": cleaned.text, "pii_found": list(cleaned.found), "classification": {}, "resolved": [],
            "clarification": None, "reply": {}, "trace": {"pii_found": list(cleaned.found)},
        }  # fmt: skip

    def classify_node(state: TurnState) -> Command[Literal["refuse", "resolve"]]:
        if len(state["message"]) > MAX_MESSAGE_CHARS:
            return Command(goto="refuse", update={"reply": _message_reply(TOO_LONG)})
        result, call, error = classify(deps.chat, deps.config.models.classify, state["message"],
                                       state.get("history", []))  # fmt: skip
        trace = {**state.get("trace", {}), "classify_error": error}
        if call is not None:
            trace["classify_usage"] = call.usage.model_dump()
        update: dict[str, Any] = {"classification": result.model_dump(mode="json"), "trace": trace}
        if result.abuse or result.intent in ("out_of_scope", "chitchat"):
            text = ABUSE if result.abuse else OUT_OF_SCOPE if result.intent == "out_of_scope" else CHITCHAT
            return Command(goto="refuse", update={**update, "reply": _message_reply(text)})
        return Command(goto="resolve", update=update)

    def refuse(state: TurnState) -> dict[str, Any]:
        return {}

    def resolve(state: TurnState) -> Command[Literal["clarify", "answer"]]:
        cls = Classification.model_validate(state["classification"])
        user_state = state.get("user_state")
        states = [user_state] if user_state and not bot.allow_cross_state else []
        resolution = resolver.resolve(
            state["message"], ui_selected=state.get("ui_selected", []), mentions=cls.plan_mentions,
            plan_year=deps.plan_year, states=states,
        )  # fmt: skip
        keys = list(resolution.plan_keys)
        if (
            resolution.status == "missing"
            and cls.intent == "follow_up"
            and len(state.get("last_plan_keys", [])) == 1
        ):
            keys = list(state["last_plan_keys"])  # a follow-up about the one plan of the previous turn
        elif resolution.status in ("ambiguous", "not_found") or (
            resolution.status == "missing" and cls.intent in NEEDS_PLAN
        ):
            clarification = _clarification_for(resolution, cls.intent == "compare")
            return Command(goto="clarify", update={"clarification": clarification.model_dump(mode="json")})
        return Command(goto="answer", update={"resolved": keys})

    def clarify(state: TurnState) -> Command[Literal["resolve"]]:
        clarification = Clarification.model_validate(state["clarification"])
        in_a_row = state.get("clarifications_in_a_row", 0)
        if in_a_row >= 2 and not clarification.open_plan_picker:
            clarification = clarification.model_copy(update={"open_plan_picker": True})
        choice: dict[str, Any] = interrupt(clarification.model_dump(mode="json"))
        update: dict[str, Any] = {"clarification": None, "clarifications_in_a_row": in_a_row + 1}
        if choice.get("plan_key"):
            update["ui_selected"] = [*state.get("ui_selected", []), str(choice["plan_key"])]
        if choice.get("text"):
            update["message"] = f"{state['message']}\n{redact(str(choice['text'])).text}"
        return Command(goto="resolve", update=update)

    def answer(state: TurnState) -> Command[Literal["clarify", "__end__"]]:
        cls = Classification.model_validate(state["classification"])
        turn = Turn(
            resolved_plan_keys=tuple(state.get("resolved", [])), user_state=state.get("user_state"),
            clarifications_in_a_row=state.get("clarifications_in_a_row", 0),
        )  # fmt: skip
        outcome: Outcome = Responder(deps, turn, cls).run(state["message"], state.get("history", []))
        trace = {**state.get("trace", {}), **dataclasses.asdict(outcome.trace)}
        trace["usage"] = [u.model_dump() for u in outcome.trace.usage]
        if outcome.clarification is not None:
            return Command(
                goto="clarify",
                update={"clarification": outcome.clarification.model_dump(mode="json"), "trace": trace},
            )
        reply = _answer_reply(outcome.answer or "")
        history = [
            *state.get("history", []),
            {"role": "user", "content": state["message"]},
            {"role": "assistant", "content": reply["markdown"]},
        ][-8:]
        return Command(
            goto="__end__",
            update={
                "reply": reply, "trace": trace, "history": history, "clarifications_in_a_row": 0,
                "last_plan_keys": reply.get("plan_keys") or state.get("resolved", []),
            },
        )  # fmt: skip

    graph = StateGraph(TurnState)
    graph.add_node("ingress", ingress)
    graph.add_node("classify", classify_node)
    graph.add_node("refuse", refuse)
    graph.add_node("resolve", resolve)
    graph.add_node("clarify", clarify)
    graph.add_node("answer", answer)
    graph.add_edge(START, "ingress")
    graph.add_edge("ingress", "classify")
    graph.add_edge("refuse", END)
    return graph.compile(checkpointer=checkpointer)


@dataclasses.dataclass(frozen=True)
class Reply:
    kind: ReplyKind
    markdown: str
    citations: tuple[dict[str, Any], ...] = ()
    plan_keys: tuple[str, ...] = ()
    clarification: Clarification | None = None
    trace: dict[str, Any] = dataclasses.field(default_factory=dict)


class Agent:
    """One bot's agent. ``ask`` runs a turn; ``choose`` answers a clarifying question in the same thread."""

    def __init__(self, deps: Deps, checkpointer: BaseCheckpointSaver[Any]) -> None:
        self.deps = deps
        self.graph = build_graph(deps, checkpointer)

    def _config(self, thread_id: str) -> dict[str, Any]:
        return {
            "configurable": {"thread_id": thread_id},
            "recursion_limit": self.deps.config.budget.recursion_limit,
        }

    def _reply(self, result: dict[str, Any]) -> Reply:
        interrupts = result.get("__interrupt__") or ()
        trace = result.get("trace", {})
        if interrupts:
            clarification = Clarification.model_validate(interrupts[0].value)
            return Reply("clarify", clarification.question, clarification=clarification, trace=trace)
        reply = result.get("reply") or _message_reply("")
        return Reply(
            reply["kind"], reply["markdown"], tuple(reply.get("citations", [])),
            tuple(reply.get("plan_keys", [])), trace=trace,
        )  # fmt: skip

    def ask(
        self,
        thread_id: str,
        message: str,
        *,
        ui_selected: tuple[str, ...] = (),
        user_state: str | None = None,
    ) -> Reply:
        state: TurnState = {"message": message, "ui_selected": list(ui_selected), "user_state": user_state}
        return self._reply(self.graph.invoke(state, self._config(thread_id)))

    def choose(self, thread_id: str, *, plan_key: str | None = None, text: str | None = None) -> Reply:
        """Answer the pending clarifying question with a listed plan or free text."""
        resume = {"plan_key": plan_key, "text": text}
        return self._reply(self.graph.invoke(Command(resume=resume), self._config(thread_id)))

    def stream(
        self,
        thread_id: str,
        message: str | None = None,
        *,
        choice: Mapping[str, str | None] | None = None,
        ui_selected: tuple[str, ...] = (),
        user_state: str | None = None,
    ) -> Iterator[Progress | Reply]:
        """Run a turn (``message``) or answer a pending clarifying question (``choice``, with ``plan_key`` or
        ``text``), yielding a :class:`Progress` as each node finishes and the :class:`Reply` last.

        The reply is the same one ``ask``/``choose`` return; nothing the model typed is yielded before the
        verifier has passed it (only node names are), so the API can stream progress safely."""
        if (message is None) == (choice is None):
            raise ValueError("pass exactly one of message or choice")
        payload: Any
        if choice is not None:
            payload = Command(resume={"plan_key": choice.get("plan_key"), "text": choice.get("text")})
        else:
            payload = {"message": message, "ui_selected": list(ui_selected), "user_state": user_state}
        final: dict[str, Any] = {}
        interrupts: list[Any] = []
        for mode, chunk in self.graph.stream(
            payload, self._config(thread_id), stream_mode=["updates", "values"]
        ):
            if mode == "values":
                final = dict(chunk)
                continue
            for node, update in chunk.items():
                if node == "__interrupt__":
                    interrupts.extend(update)
                else:
                    yield Progress(node)
        if interrupts:
            final["__interrupt__"] = tuple(interrupts)
        yield self._reply(final)


@dataclasses.dataclass(frozen=True)
class Progress:
    """A graph node finished (``ingress``, ``classify``, ``refuse``, ``resolve``, ``clarify``, ``answer``)."""

    node: str
