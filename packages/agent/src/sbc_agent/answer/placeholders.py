"""Placeholders the model writes instead of values (PLAN.md 3.7, item 1).

``{{F3}}`` is fact F3's SBC wording; ``{{F3.label}}`` its SBC label and ``{{F3.tier}}`` its column heading.
``{{S2}}`` quotes text chunk S2. ``{{P1.name}}``, ``.code``, ``.year``, ``.period`` and ``.type`` are plan
P1's name, contract code, plan year, coverage period and plan type. Everything a placeholder expands to is
SBC text or the plan's own identifiers, so the model never needs to type a number.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from sbc_tools.context import Turn

PLACEHOLDER_RE = re.compile(r"\{\{\s*([FSP])(\d+)(?:\.([a-z_]+))?\s*\}\}")
FACT_FIELDS = frozenset({"label", "tier"})
PLAN_FIELDS = frozenset({"name", "code", "year", "period", "type"})


@dataclass(frozen=True)
class Placeholder:
    kind: str
    """F, S or P."""
    id: str
    """e.g. F3."""
    field: str | None
    start: int
    end: int

    @property
    def is_value(self) -> bool:
        """A fact's value or a quoted chunk: the things a citation must follow."""
        return self.field is None and self.kind in "FS"


def find_placeholders(text: str) -> list[Placeholder]:
    return [
        Placeholder(m[1], f"{m[1]}{m[2]}", m[3], m.start(), m.end()) for m in PLACEHOLDER_RE.finditer(text)
    ]


def problem(ph: Placeholder, turn: Turn) -> str | None:
    """Why ``ph`` can't be resolved in this turn, or None when it can."""
    known = (
        turn.facts.keys() if ph.kind == "F" else turn.chunks.keys() if ph.kind == "S" else turn.plans.keys()
    )
    if ph.id not in known:
        return f"{{{{{ph.id}}}}} was not returned by a tool this turn"
    allowed = {"F": FACT_FIELDS, "S": frozenset(), "P": PLAN_FIELDS}[ph.kind]
    if ph.kind == "P" and ph.field is None:
        return f"{{{{{ph.id}}}}} needs a field: {sorted(PLAN_FIELDS)}"
    if ph.field is not None and ph.field not in allowed:
        return f"{{{{{ph.id}.{ph.field}}}}} is not a field of {ph.kind} placeholders"
    if ph.kind == "F" and ph.field == "tier" and not turn.facts[ph.id].tier_label:
        return f"{{{{{ph.id}.tier}}}}: this fact has no provider column"
    return None


def expand(ph: Placeholder, turn: Turn) -> str:
    """The text a placeholder stands for. Call :func:`problem` first; this assumes it returned None."""
    if ph.kind == "F":
        fact = turn.facts[ph.id]
        if ph.field == "label":
            return fact.label
        if ph.field == "tier":
            return fact.tier_label or ""
        return fact.verbatim
    if ph.kind == "S":
        return turn.chunks[ph.id][0].text
    plan = turn.plans[ph.id]
    return {
        "name": plan.plan_name,
        "code": plan.contract_code,
        "year": str(plan.plan_year),
        "period": plan.coverage_period,
        "type": plan.plan_type,
    }[ph.field or ""]
