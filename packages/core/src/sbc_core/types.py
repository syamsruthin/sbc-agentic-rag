"""Domain types shared by the tools, retrieval and agent packages (PLAN.md sections 3.5.3 and 3.7).

A :class:`Fact` is one value from the facts bundle: the SBC's own wording, its parse when one exists, the
flags an answer must disclose, and a :class:`Citation`. A :class:`SearchHit` is one text chunk returned by a
plan-filtered search. Tools give facts and chunks per-turn IDs (``F1``, ``S1``) that compose refers to as
placeholders; the types here carry no such IDs, because the same fact can appear in several turns.
"""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal, Self

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

_PLAN_KEY_RE = re.compile(r"^(?P<year>20\d\d):(?P<sbc_id>[0-9A-Za-z]+)$")


def _check_plan_key(value: str) -> str:
    if not _PLAN_KEY_RE.match(value):
        raise ValueError(f"not a plan key (expected '<year>:<sbc_id>'): {value!r}")
    return value


PlanKey = Annotated[str, AfterValidator(_check_plan_key)]
"""``"{plan_year}:{sbc_id}"``, e.g. ``"2027:100000000001"``. Unique per SBC document and plan year."""


def make_plan_key(plan_year: int, sbc_id: str) -> str:
    return _check_plan_key(f"{plan_year}:{sbc_id}")


def split_plan_key(plan_key: str) -> tuple[int, str]:
    m = _PLAN_KEY_RE.match(plan_key)
    if not m:
        raise ValueError(f"not a plan key: {plan_key!r}")
    return int(m["year"]), m["sbc_id"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PlanSummary(_Model):
    """Identity and listing attributes of one plan, enough to name it, scope it and cite it.

    ``state``, ``market``, ``network``, ``exchange``, ``plan_status`` and ``metal_tier`` come from the portal
    catalog or the plan name, not from SBC text; show them only with a source label (PLAN.md 3.3, rule 3).
    """

    plan_key: PlanKey
    plan_year: int
    sbc_id: str
    contract_code: str
    plan_name: str
    issuer: str = ""
    plan_type: str = ""
    coverage_period: str = ""
    coverage_for: str = ""
    state: str | None = None
    market: str | None = None
    network: str | None = None
    exchange: str | None = None
    plan_status: str | None = None
    metal_tier: str | None = None
    metal_tier_source: str | None = None
    source_url: str = ""
    eoc_url: str = ""


class Citation(_Model):
    """Where a value is printed: plan, SBC section and page(s)."""

    plan_key: PlanKey
    plan_name: str
    contract_code: str
    plan_year: int
    section_path: str
    """e.g. "Important Questions > What is the overall deductible?"."""
    pages: tuple[int, ...] = ()
    source_url: str = ""

    @property
    def label(self) -> str:
        """``Plan (CODE), 2027 SBC, Section › Sub, p. 1``. PLAN.md 3.7 item 6."""
        section = self.section_path.replace(" > ", " › ")
        text = f"{self.plan_name} ({self.contract_code}), {self.plan_year} SBC, {section}"
        if self.pages:
            text += (", p. " if len(self.pages) == 1 else ", pp. ") + _page_range(self.pages)
        return text

    @property
    def url(self) -> str:
        """The SBC PDF, opened at the first cited page when there is one."""
        if not self.source_url or not self.pages:
            return self.source_url
        return f"{self.source_url}#page={self.pages[0]}"


def _page_range(pages: tuple[int, ...]) -> str:
    if len(pages) > 1 and list(pages) == list(range(pages[0], pages[-1] + 1)):
        return f"{pages[0]}-{pages[-1]}"
    return ", ".join(str(p) for p in pages)


FactKind = Literal["benefit", "question", "list_item", "example"]
ParseStatus = Literal["exact", "unparsed", "blank"]
LimitationsKind = Literal["text", "none", "blank"]


class Fact(_Model):
    """One value from the facts bundle.

    ``key`` names the value independently of any turn: ``benefit:<service_id>:<tier_id>``,
    ``question:<question_id>``, ``list:<list_id>:<position>`` or ``example:<example_id>:<line label>``.
    Display always uses ``verbatim``; ``parsed`` is for computation and ranking only, and only when
    ``parse_status == "exact"`` (PLAN.md 3.3, rule 1).
    """

    kind: FactKind
    key: str = Field(pattern=r"^(benefit|question|list|example):.+$")
    plan_key: PlanKey
    label: str
    """The SBC's own label for the value, e.g. "Specialist visit" or "Total Example Cost"."""
    verbatim: str
    parsed: dict[str, Any] | None = None
    parse_status: ParseStatus | None = None
    tier_id: str | None = None
    tier_label: str | None = None
    limitations: str | None = None
    """Benefit facts only: the row's limitations, which must travel with the cost (PLAN.md 3.3, rule 2)."""
    limitations_kind: LimitationsKind | None = None
    why_this_matters: str | None = None
    """Question facts only: the SBC's "Why This Matters" text for the answer."""
    carried_note: bool = False
    event_inferred: bool = False
    citation: Citation

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.citation.plan_key != self.plan_key:
            raise ValueError("citation is for a different plan")
        if self.kind == "benefit" and (self.limitations is None or self.limitations_kind is None):
            raise ValueError("a benefit fact needs its limitations")
        if self.parsed is not None and self.parse_status is None:
            raise ValueError("a parsed value needs a parse status")
        return self

    @property
    def computable(self) -> bool:
        return self.parsed is not None and self.parse_status == "exact"


class SparseVector(_Model):
    """Sparse term weights, e.g. BM25 term frequencies keyed by hashed token."""

    indices: tuple[int, ...]
    values: tuple[float, ...]

    @model_validator(mode="after")
    def _check(self) -> Self:
        if len(self.indices) != len(self.values):
            raise ValueError("indices and values differ in length")
        if len(set(self.indices)) != len(self.indices):
            raise ValueError("duplicate sparse indices")
        return self


ChunkType = Literal["header", "iq", "service_row", "list", "coverage_example", "narrative"]


class SearchQuery(_Model):
    """A plan-filtered text search. The plan filter is mandatory and is set by code, never by the LLM."""

    plan_keys: tuple[PlanKey, ...] = Field(min_length=1)
    text: str = Field(min_length=1)
    dense: tuple[float, ...] | None = None
    """Query embedding; None when the embedding call failed and search runs on BM25 only."""
    sparse: SparseVector | None = None
    chunk_types: tuple[ChunkType, ...] = ()
    """Restrict to these chunk types; empty means all."""
    limit: int = Field(default=20, gt=0, le=100)

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.dense is None and self.sparse is None:
            raise ValueError("a search needs a dense or a sparse query vector")
        return self


class SearchHit(_Model):
    """One unique chunk returned by a search, with the requested plans it belongs to."""

    chunk_hash: str
    chunk_type: ChunkType
    section_path: str
    text: str
    plan_keys: tuple[PlanKey, ...] = Field(min_length=1)
    """Only the requested plans that contain this chunk; never other plans."""
    score: float


class ChunkContext(_Model):
    """Per-plan details of a shared chunk. Identical text can sit on different pages in different plans,
    and only some of them may carry a flag, so these come from the facts bundle, not the search index."""

    plan_key: PlanKey
    chunk_hash: str
    pages: tuple[int, ...]
    carried_note: bool = False
    event_inferred: bool = False
