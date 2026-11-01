"""Plan resolution in code (PLAN.md section 3.4.1). The LLM may propose plan mentions; it never decides.

Order, first match wins for each way a plan can be named:

1. **Explicit IDs** in the user's text: contract code, HIOS plan id, SBC id, each looked up exactly.
2. **UI-selected plans** (picker chips), authoritative for the turn.
3. Session references ("the silver one"): needs the classifier's reference spec, added with the graph.
4. **Name search** for each plan mention, inside the bot's scope.

A mention that matches more than one plan is never guessed: the result is ``ambiguous`` with up to
``max_options`` candidates for a clarifying question. Plans outside the bot's scope are invisible here, even
by exact ID, so the public bot can never resolve a broker-only plan.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal

from sbc_core.config import PlanScope
from sbc_core.interfaces import FactsReader
from sbc_core.types import PlanSummary

ResolutionStatus = Literal["resolved", "ambiguous", "not_found", "missing"]
PlanSource = Literal["id", "ui", "search"]

# PLAN.md 3.4.1. Contract codes are typed in upper case; a lower-case word is never read as one. A token next
# to "$", "%", "/", "," or "." is part of an amount ("$1300", "1,300", "1300.00"), not a code.
_NOT_AMOUNT_BEFORE = r"(?<![\w$.,/-])"
_NOT_AMOUNT_AFTER = r"(?![\w%/-]|[.,]\d)"
_HIOS_RE = re.compile(_NOT_AMOUNT_BEFORE + r"(\d{5}[A-Z]{2}\d{7}(?:-\d{2})?)" + _NOT_AMOUNT_AFTER)
_SBC_ID_RE = re.compile(_NOT_AMOUNT_BEFORE + r"(\d{12})" + _NOT_AMOUNT_AFTER)
_CONTRACT_RE = re.compile(_NOT_AMOUNT_BEFORE + r"([0-9A-Z]{4,5})" + _NOT_AMOUNT_AFTER)


@dataclass(frozen=True)
class Ambiguity:
    """One way of naming a plan that matched several plans."""

    mention: str
    candidates: tuple[PlanSummary, ...]
    more: bool = False
    """True when more plans matched than are listed."""


@dataclass(frozen=True)
class Resolution:
    status: ResolutionStatus
    plan_keys: tuple[str, ...] = ()
    """Plans resolved with certainty. With ``ambiguous`` or ``not_found`` these are partial: the turn still
    needs a clarifying question before answering."""
    sources: dict[str, PlanSource] = field(default_factory=dict)
    ambiguities: tuple[Ambiguity, ...] = ()
    not_found: tuple[str, ...] = ()
    """Mentions that matched no plan in scope."""

    @property
    def resolved(self) -> bool:
        return self.status == "resolved"


def id_tokens(text: str) -> list[str]:
    """Strings in ``text`` shaped like a HIOS id, SBC id or contract code, in order, without duplicates."""
    found: list[str] = []
    taken: list[tuple[int, int]] = []
    for pattern in (_HIOS_RE, _SBC_ID_RE, _CONTRACT_RE):
        for m in pattern.finditer(text):
            span = m.span(1)
            if any(span[0] < end and start < span[1] for start, end in taken):
                continue  # part of a longer id already found, e.g. the digits of a HIOS id
            taken.append(span)
            found.append(m[1])
    order = sorted(range(len(found)), key=lambda i: taken[i][0])
    return list(dict.fromkeys(found[i] for i in order))


class PlanResolver:
    def __init__(self, reader: FactsReader, scope: PlanScope, *, max_options: int = 5) -> None:
        self.reader = reader
        self.scope = scope
        self.max_options = max_options

    def in_scope(
        self, plan: PlanSummary, *, plan_year: int | None = None, states: Sequence[str] = ()
    ) -> bool:
        s = self.scope
        return (
            (not s.markets or plan.market in s.markets)
            and (plan.plan_status or "") not in s.exclude_plan_statuses
            and (plan.exchange or "") not in s.exclude_exchange
            and (plan_year is None or plan.plan_year == plan_year)
            and (not states or plan.state in states)
        )

    def resolve(
        self,
        text: str,
        *,
        ui_selected: Sequence[str] = (),
        mentions: Sequence[str] = (),
        plan_year: int | None = None,
        states: Sequence[str] = (),
    ) -> Resolution:
        """Resolve the plans one user turn refers to.

        ``text`` is the user's message (searched for IDs only). ``mentions`` are plan names the classifier
        found in it. ``states`` narrows name search and IDs, e.g. to the public user's state.
        """
        keys: dict[str, PlanSource] = {}
        ambiguities: list[Ambiguity] = []
        not_found: list[str] = []

        for token in id_tokens(text):
            every = self.reader.find_plans_by_identifier(token, plan_year=plan_year)
            matches = [p for p in every if self.in_scope(p, plan_year=plan_year, states=states)]
            if len(matches) == 1:
                keys.setdefault(matches[0].plan_key, "id")
            elif len(matches) > 1:
                ambiguities.append(self._ambiguity(token, matches))
            elif every:
                not_found.append(token)  # a real plan, but outside this bot's scope
            # A token that matches no plan at all is an ordinary word or number, not a failed lookup.

        if ui_selected:
            found = self.reader.plans(ui_selected)
            for key in ui_selected:
                plan = found.get(key)
                if plan is not None and self.in_scope(plan, plan_year=plan_year):
                    keys.setdefault(key, "ui")
                else:
                    not_found.append(key)  # a selection outside scope is never honored

        for mention in mentions:
            limit = self.max_options + 1
            matches = self.reader.find_plans_by_name(
                mention,
                plan_year=plan_year,
                states=states,
                markets=self.scope.markets,
                exclude_plan_statuses=self.scope.exclude_plan_statuses,
                exclude_exchanges=self.scope.exclude_exchange,
                limit=limit,
            )
            if any(m.plan_key in keys for m in matches):
                continue  # the mention names a plan already resolved by ID or selection
            if len(matches) == 1:
                keys.setdefault(matches[0].plan_key, "search")
            elif matches:
                ambiguities.append(self._ambiguity(mention, matches))
            else:
                not_found.append(mention)

        if ambiguities:
            status: ResolutionStatus = "ambiguous"
        elif not_found:
            status = "not_found"
        elif keys:
            status = "resolved"
        else:
            status = "missing"
        return Resolution(
            status=status,
            plan_keys=tuple(keys),
            sources=keys,
            ambiguities=tuple(ambiguities),
            not_found=tuple(not_found),
        )

    def _ambiguity(self, mention: str, matches: Sequence[PlanSummary]) -> Ambiguity:
        return Ambiguity(
            mention=mention,
            candidates=tuple(matches[: self.max_options]),
            more=len(matches) > self.max_options,
        )
