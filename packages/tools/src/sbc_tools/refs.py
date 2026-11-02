"""What tools return to the model: plans, facts and text chunks, each under its per-turn ID.

The model sees the SBC's own wording so it can choose and explain values, but it writes answers with the IDs
(``{{F3}}``) and code substitutes the wording (PLAN.md 3.7). Plan listing data (state, market, network,
metal tier…) is not SBC text and is grouped under ``listing`` so it is never presented as such (PLAN.md 3.3).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from sbc_core.types import ChunkContext, ChunkType, Fact, FactKind, PlanSummary, SearchHit
from sbc_tools.context import Turn

LISTING_SOURCE = "plan listing (insurer portal or CMS data), not SBC text"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Listing(_Model):
    source: str = LISTING_SOURCE
    state: str | None = None
    market: str | None = None
    network: str | None = None
    exchange: str | None = None
    plan_status: str | None = None
    metal_tier: str | None = None


class PlanRef(_Model):
    id: str
    plan_key: str
    plan_name: str
    contract_code: str
    plan_year: int
    plan_type: str
    coverage_period: str
    listing: Listing

    @classmethod
    def of(cls, plan: PlanSummary, turn: Turn) -> PlanRef:
        return cls(
            id=turn.plan_id(plan), plan_key=plan.plan_key, plan_name=plan.plan_name,
            contract_code=plan.contract_code, plan_year=plan.plan_year, plan_type=plan.plan_type,
            coverage_period=plan.coverage_period,
            listing=Listing(
                state=plan.state, market=plan.market, network=plan.network, exchange=plan.exchange,
                plan_status=plan.plan_status, metal_tier=plan.metal_tier,
            ),
        )  # fmt: skip


class FactRef(_Model):
    id: str
    plan: str
    """The plan's ``P`` id."""
    kind: FactKind
    label: str
    verbatim: str
    tier_label: str | None = None
    limitations: str | None = None
    """The row's limitations; an answer citing this cost must show them (PLAN.md 3.3 rule 2)."""
    why_this_matters: str | None = None
    carried_note: bool = False
    """The limitations continue from the previous page's box; an answer must say so (PLAN.md 3.2.2)."""
    event_inferred: bool = False
    """The SBC doesn't print the event heading beside this row; never present it as SBC text."""
    section: str
    pages: tuple[int, ...]

    @classmethod
    def of(cls, fact: Fact, plan_id: str, turn: Turn) -> FactRef:
        return cls(
            id=turn.fact_id(fact), plan=plan_id, kind=fact.kind, label=fact.label, verbatim=fact.verbatim,
            tier_label=fact.tier_label,
            limitations=fact.limitations if fact.limitations_kind == "text" else None,
            why_this_matters=fact.why_this_matters, carried_note=fact.carried_note,
            event_inferred=fact.event_inferred, section=fact.citation.section_path, pages=fact.citation.pages,
        )  # fmt: skip


class ChunkRef(_Model):
    id: str
    plan: str
    chunk_type: ChunkType
    section: str
    text: str
    pages: tuple[int, ...] = ()
    carried_note: bool = False
    event_inferred: bool = False

    @classmethod
    def of(cls, hit: SearchHit, plan: PlanSummary, context: ChunkContext | None, turn: Turn) -> ChunkRef:
        return cls(
            id=turn.chunk_id(hit, plan.plan_key, context), plan=turn.plan_id(plan), chunk_type=hit.chunk_type,
            section=hit.section_path, text=hit.text, pages=context.pages if context else (),
            carried_note=context.carried_note if context else False,
            event_inferred=context.event_inferred if context else False,
        )  # fmt: skip


class NotInSbc(_Model):
    """Something asked for that this plan's SBC doesn't have. Absence isn't exclusion (PLAN.md 3.3 rule 4)."""

    plan: str
    item: str
    note: str = "Not found in this plan's SBC. The SBC isn't a complete list; check the plan document."
