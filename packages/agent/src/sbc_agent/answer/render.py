"""Turn a verified draft into the answer users see (PLAN.md 3.7 items 4, 6 and 7).

Code, not the model, adds everything that must be right:

- a header line per plan the answer uses: name, contract code, plan year and coverage period;
- each placeholder expanded to the SBC's own wording, with a numbered citation after every value, linking
  to the SBC PDF at its page (``source_url#page=N``);
- for each benefit value shown, its row's limitations as a "Note from the SBC" unless the answer already
  quotes them, and the carried-note label when the note continues from the previous page (PLAN.md 3.2.2);
- citations of rows whose event heading isn't printed in the SBC name the row only, never the inferred
  heading;
- the disclaimer, on every substantive answer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from sbc_agent.answer.placeholders import expand, find_placeholders
from sbc_core.types import Citation, Fact, PlanSummary
from sbc_tools.context import Turn

CARRIED_NOTE_LABEL = "In the SBC this note appears in a box continuing from the previous page."
INFERRED_HEADING_LABEL = "(the SBC doesn't print a section heading beside this row)"
DISCLAIMER = (
    "Based on {whose} {years} Summary of Benefits and Coverage (SBC). This summary isn't a guarantee of "
    "coverage or cost and doesn't include premiums. Check the official plan documents or call the plan "
    "before making decisions. This tool doesn't give medical, legal, tax or financial advice."
)
NOT_AFFILIATED = "Independent tool; not affiliated with or endorsed by the insurer."

AnswerMode = Literal["composed", "template"]


@dataclass(frozen=True)
class RenderedCitation:
    number: int
    label: str
    url: str


@dataclass(frozen=True)
class RenderedAnswer:
    markdown: str
    citations: tuple[RenderedCitation, ...]
    plan_keys: tuple[str, ...]
    value_ids: tuple[str, ...]
    """Fact and chunk IDs whose values the answer shows, for the audit log and the eval."""
    mode: AnswerMode


def _citation_label(citation: Citation, inferred_heading: bool) -> str:
    if not inferred_heading:
        return citation.label
    service = citation.section_path.rsplit(" > ", 1)[-1]
    row_only = citation.model_copy(update={"section_path": service})
    return f"{row_only.label} {INFERRED_HEADING_LABEL}"


def _chunk_citation(turn: Turn, chunk_id: str) -> tuple[Citation, bool]:
    hit, context, plan_key = turn.chunks[chunk_id]
    plan = next(p for p in turn.plans.values() if p.plan_key == plan_key)
    citation = Citation(
        plan_key=plan.plan_key, plan_name=plan.plan_name, contract_code=plan.contract_code,
        plan_year=plan.plan_year, section_path=hit.section_path, pages=context.pages if context else (),
        source_url=plan.source_url,
    )  # fmt: skip
    return citation, bool(context and context.event_inferred)


def _plan_of(turn: Turn, kind: str, item_id: str) -> PlanSummary:
    if kind == "P":
        return turn.plans[item_id]
    key = turn.facts[item_id].plan_key if kind == "F" else turn.chunks[item_id][2]
    return next(p for p in turn.plans.values() if p.plan_key == key)


def _disclaimer(plans: list[PlanSummary]) -> str:
    years = " and ".join(sorted({str(p.plan_year) for p in plans})) or "plan-year"
    whose = "this plan's" if len(plans) <= 1 else "these plans'"
    return f"*{DISCLAIMER.format(whose=whose, years=years)} {NOT_AFFILIATED}*"


def render(draft: str, turn: Turn, *, mode: AnswerMode = "composed") -> RenderedAnswer:
    """Render a draft that :func:`~sbc_agent.answer.verify.verify` passed."""
    numbers: dict[tuple[str, str, tuple[int, ...]], int] = {}
    citations: list[RenderedCitation] = []

    def cite(citation: Citation, inferred: bool) -> int:
        key = (citation.plan_key, citation.section_path, citation.pages)
        if key not in numbers:
            numbers[key] = len(numbers) + 1
            citations.append(
                RenderedCitation(numbers[key], _citation_label(citation, inferred), citation.url)
            )
        return numbers[key]

    plans: dict[str, PlanSummary] = {}
    shown_facts: dict[str, tuple[Fact, int]] = {}
    value_ids: list[str] = []
    body, last = [], 0
    for ph in find_placeholders(draft):
        body.append(draft[last : ph.start])
        last = ph.end
        plan = _plan_of(turn, ph.kind, ph.id)
        plans.setdefault(plan.plan_key, plan)
        text = expand(ph, turn)
        if ph.kind == "F" and ph.field is None:
            fact = turn.facts[ph.id]
            n = cite(fact.citation, fact.event_inferred)
            shown_facts.setdefault(ph.id, (fact, n))
            text = f"{text} [{n}]"
        elif ph.kind == "S":
            citation, inferred = _chunk_citation(turn, ph.id)
            text = f"“{text}” [{cite(citation, inferred)}]"
        if ph.is_value:
            value_ids.append(ph.id)
        body.append(text)
    body.append(draft[last:])
    text = "".join(body).strip()

    # One note per row and limitation text; tiers that share it (often every column) are named together.
    grouped: dict[tuple[str, str, str, bool, int], list[str]] = {}
    for fact, n in shown_facts.values():
        if fact.kind != "benefit" or fact.limitations_kind != "text" or not fact.limitations:
            continue
        if fact.limitations in text and not fact.carried_note:
            continue
        key = (fact.plan_key, fact.label, fact.limitations, fact.carried_note, n)
        grouped.setdefault(key, []).extend([fact.tier_label] if fact.tier_label else [])
    notes = []
    for (_, label, limitations, carried, n), tiers in grouped.items():
        where = f"{label}, {' and '.join(tiers)}" if tiers else label
        note = f"Note from the SBC ({where}): “{limitations}”"
        notes.append(f"{note} {CARRIED_NOTE_LABEL} [{n}]" if carried else f"{note} [{n}]")

    header = [
        f"**{p.plan_name} ({p.contract_code}) · {p.plan_year} SBC · coverage period {p.coverage_period}**"
        for p in plans.values()
    ]
    parts = ["\n".join(header), text]
    if notes:
        parts.append("\n".join(f"- {note}" for note in notes))
    if citations:
        parts.append(
            "\n".join(
                f"[{c.number}] [{c.label}]({c.url})" if c.url else f"[{c.number}] {c.label}"
                for c in citations
            )
        )
    parts.append(_disclaimer(list(plans.values())))
    return RenderedAnswer(
        markdown="\n\n".join(p for p in parts if p),
        citations=tuple(citations),
        plan_keys=tuple(plans),
        value_ids=tuple(dict.fromkeys(value_ids)),
        mode=mode,
    )
