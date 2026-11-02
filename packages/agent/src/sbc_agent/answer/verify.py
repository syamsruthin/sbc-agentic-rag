"""The number and citation firewall (PLAN.md 3.7 items 2-3 and 3.8 L4), run on the model's draft.

The draft is checked before any placeholder is expanded, so everything the model typed itself is visible:

- **Typed values.** Once placeholders are removed, any digit, ``$`` or ``%``, or a word that states an amount
  ("hundred", "percent", "free"), is a violation. Values reach the user only through placeholders.
- **Placeholders.** Each must name a fact, chunk or plan a tool returned this turn, with a known field.
- **Links.** The model may not write URLs; citations carry the SBC links.
- **Advice.** A bot that may not recommend plans (``tradeoffs_only``) must not use advice phrasing.

A failing draft goes back to compose once with the violations as feedback, then to the template fallback.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from sbc_agent.answer.placeholders import PLACEHOLDER_RE, find_placeholders, problem
from sbc_core.config import BotProfile
from sbc_tools.context import Turn

ViolationKind = Literal["empty", "typed_value", "placeholder", "url", "advice", "no_values"]

_TYPED_VALUE_RE = re.compile(
    r"\$?\d[\d,.]*%?|[$%]|\b(?:hundreds?|thousands?|millions?|percent|dollars?|zero|free|half)\b",
    re.IGNORECASE,
)
_URL_RE = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
_ADVICE_RE = re.compile(
    r"\b(?:you should (?:choose|pick|buy|enroll|go with|get|select)|I(?: would|'d)? recommend|"
    r"I(?: would|'d)? suggest|(?:the )?best plan for you|you'd be better off)\b",
    re.IGNORECASE,
)
_CONTEXT = 30


@dataclass(frozen=True)
class Violation:
    kind: ViolationKind
    detail: str


@dataclass(frozen=True)
class VerifierReport:
    violations: tuple[Violation, ...]
    placeholders: tuple[str, ...] = ()
    """Every placeholder ID the draft uses, in order of first use."""
    value_ids: tuple[str, ...] = field(default=())
    """The fact and chunk IDs whose values the draft shows."""

    @property
    def ok(self) -> bool:
        return not self.violations

    def feedback(self) -> str:
        """The violations as one message for the retry."""
        return "Your answer was rejected:\n" + "\n".join(f"- {v.detail}" for v in self.violations)


def _snippet(text: str, start: int, end: int) -> str:
    return text[max(0, start - _CONTEXT) : end + _CONTEXT].replace("\n", " ")


def verify(draft: str, turn: Turn, profile: BotProfile, *, require_values: bool = False) -> VerifierReport:
    """Check a compose draft. ``require_values`` fails a draft that shows no fact or chunk at all, for turns
    where tools returned facts that the answer has to use."""
    violations: list[Violation] = []
    if not draft.strip():
        return VerifierReport((Violation("empty", "the answer is empty"),))
    found = find_placeholders(draft)
    for ph in found:
        why = problem(ph, turn)
        if why:
            violations.append(Violation("placeholder", why))
    prose = PLACEHOLDER_RE.sub(" ", draft)
    for m in _URL_RE.finditer(prose):
        violations.append(Violation("url", f"don't write links ({m[0]}); citations carry the SBC links"))
    prose = _URL_RE.sub(" ", prose)
    for m in _TYPED_VALUE_RE.finditer(prose):
        violations.append(
            Violation(
                "typed_value",
                f'typed "{m[0]}" in "…{_snippet(prose, m.start(), m.end())}…"; write values as placeholders',
            )
        )
    if profile.plan_recommendation_policy == "tradeoffs_only":
        for m in _ADVICE_RE.finditer(prose):
            violations.append(Violation("advice", f'"{m[0]}": describe trade-offs, never recommend a plan'))
    ids = tuple(dict.fromkeys(ph.id for ph in found))
    values = tuple(dict.fromkeys(ph.id for ph in found if ph.is_value and problem(ph, turn) is None))
    if require_values and not values:
        violations.append(Violation("no_values", "the answer shows none of the facts the tools returned"))
    return VerifierReport(tuple(violations), ids, values)
