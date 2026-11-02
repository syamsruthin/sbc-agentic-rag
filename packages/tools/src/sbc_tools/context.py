"""What every tool call can see: the facts, the bot profile, the plans this turn may use, and the turn's IDs.

**Plan identity is enforced here, not by the model (PLAN.md 3.4.1).** A tool only accepts ``plan_key``s that
the turn may use: the plans the resolver resolved before the agent ran, plus plans a ``find_plans`` call
listed without ambiguity. A key the model invented, or a plan outside the bot's scope, is refused.

**Per-turn IDs (PLAN.md 3.7).** Facts get ``F1``, ``F2``…, text chunks ``S1``…, and plans ``P1``…. Compose
refers to values only through these placeholders, so the same value always keeps one ID within a turn.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict

from sbc_core.config import BotProfile, Retrieval
from sbc_core.interfaces import FactsReader
from sbc_core.taxonomy import Taxonomy
from sbc_core.types import ChunkContext, ChunkType, Fact, PlanSummary, SearchHit

ErrorCode = Literal[
    "invalid_arguments",
    "plan_not_allowed",
    "out_of_scope",
    "state_required",
    "unknown_aspect",
    "search_unavailable",
    "limit_reached",
    "tool_not_available",
]


class ToolError(Exception):
    """A refused or failed tool call. The message goes back to the model, so it never holds secrets."""

    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.code: ErrorCode = code
        self.message = message


class SearchResult(Protocol):
    hits: list[SearchHit]
    degraded: list[str]


class TextSearch(Protocol):
    """Plan-filtered text search (``sbc_retrieval.text_search.TextSearcher``), wired in by the agent."""

    def search(
        self, plan_keys: Sequence[str], query: str, *, chunk_types: Sequence[ChunkType] = ()
    ) -> SearchResult: ...


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ClarifyOption(_Model):
    label: str
    plan_key: str | None = None


class Clarification(_Model):
    """A question for the user; the graph pauses the turn on it (PLAN.md 3.5.2)."""

    question: str
    options: tuple[ClarifyOption, ...] = ()
    open_plan_picker: bool = False


@dataclass
class Turn:
    """Per-turn state the graph creates before the agent runs and reads afterwards."""

    resolved_plan_keys: tuple[str, ...] = ()
    """Plans the resolver resolved in code for this turn (IDs, UI selection, unambiguous names)."""
    user_state: str | None = None
    """The state a public user chose for the session; bots without cross-state search are held to it."""
    clarifications_in_a_row: int = 0
    """Clarifying questions asked in the previous turns without an answer in between."""
    facts: dict[str, Fact] = field(default_factory=dict)
    chunks: dict[str, tuple[SearchHit, ChunkContext | None, str]] = field(default_factory=dict)
    """``S`` id -> (hit, its context in the plan, plan_key)."""
    plans: dict[str, PlanSummary] = field(default_factory=dict)
    clarification: Clarification | None = None
    listed_plan_keys: set[str] = field(default_factory=set)
    """Plans an unambiguous ``find_plans`` call listed this turn; usable like resolved plans."""
    _fact_ids: dict[tuple[str, str], str] = field(default_factory=dict)
    _chunk_ids: dict[tuple[str, str], str] = field(default_factory=dict)
    _plan_ids: dict[str, str] = field(default_factory=dict)

    def fact_id(self, fact: Fact) -> str:
        key = (fact.plan_key, fact.key)
        if key not in self._fact_ids:
            self._fact_ids[key] = f"F{len(self._fact_ids) + 1}"
            self.facts[self._fact_ids[key]] = fact
        return self._fact_ids[key]

    def chunk_id(self, hit: SearchHit, plan_key: str, context: ChunkContext | None) -> str:
        key = (plan_key, hit.chunk_hash)
        if key not in self._chunk_ids:
            self._chunk_ids[key] = f"S{len(self._chunk_ids) + 1}"
            self.chunks[self._chunk_ids[key]] = (hit, context, plan_key)
        return self._chunk_ids[key]

    def plan_id(self, plan: PlanSummary) -> str:
        if plan.plan_key not in self._plan_ids:
            self._plan_ids[plan.plan_key] = f"P{len(self._plan_ids) + 1}"
            self.plans[self._plan_ids[plan.plan_key]] = plan
        return self._plan_ids[plan.plan_key]


@dataclass
class ToolContext:
    reader: FactsReader
    profile: BotProfile
    taxonomy: Taxonomy
    retrieval: Retrieval
    turn: Turn
    search: TextSearch | None = None
    plan_year: int | None = None

    def visible(self, plan: PlanSummary) -> bool:
        """The bot may see this plan: inside its scope, and in the user's state when it can't cross states."""
        if not self.profile.plan_scope.allows(plan):
            return False
        if self.plan_year is not None and plan.plan_year != self.plan_year:
            return False
        if not self.profile.allow_cross_state and self.turn.user_state is not None:
            return plan.state == self.turn.user_state
        return True

    def require_plans(self, plan_keys: Iterable[str]) -> list[PlanSummary]:
        """The plans for ``plan_keys``, in order, if this turn may use every one of them; else ToolError."""
        keys = list(dict.fromkeys(plan_keys))
        allowed = set(self.turn.resolved_plan_keys) | self.turn.listed_plan_keys
        unknown = [k for k in keys if k not in allowed]
        if unknown:
            raise ToolError(
                "plan_not_allowed",
                f"plan_keys {unknown} were not resolved for this turn. Use the plans given in the context or "
                "listed by find_plans; if the user's plan is unclear, call ask_clarifying_question.",
            )
        found = self.reader.plans(keys)
        plans = [found[k] for k in keys if k in found]
        hidden = [k for k in keys if k not in found or not self.visible(found[k])]
        if hidden:
            raise ToolError("out_of_scope", f"plan_keys {hidden} are not available to this assistant.")
        return plans
