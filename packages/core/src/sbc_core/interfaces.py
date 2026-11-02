"""The two ways runtime code reads SBC data (PLAN.md section 10, module boundaries).

Tools and retrieval read structured facts only through :class:`FactsReader` and search text only through
:class:`SearchIndex`. Production uses the SQLite facts bundle and Qdrant; tests use the same bundle code on a
small synthetic bundle and an in-memory search index.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from sbc_core.types import ChunkContext, Fact, PlanSummary, SearchHit, SearchQuery


class FactsReader(Protocol):
    @property
    def bundle_version(self) -> str: ...

    def plans(self, plan_keys: Sequence[str]) -> dict[str, PlanSummary]:
        """Summaries for the plan keys that exist; unknown keys are left out."""
        ...

    def find_plans_by_name(
        self,
        text: str,
        *,
        plan_year: int | None = None,
        states: Sequence[str] = (),
        markets: Sequence[str] = (),
        exclude_plan_statuses: Sequence[str] = (),
        exclude_exchanges: Sequence[str] = (),
        limit: int = 10,
    ) -> list[PlanSummary]:
        """Plans whose SBC or catalog name, or contract code, contains ``text``. Best match first."""
        ...

    def find_plans_by_identifier(self, identifier: str, *, plan_year: int | None = None) -> list[PlanSummary]:
        """Plans whose contract code, SBC id or HIOS plan id equals ``identifier`` exactly.

        A HIOS id without its ``-NN`` variant suffix matches every variant, so it can return several plans.
        """
        ...

    def benefit_facts(
        self, plan_key: str, service_ids: Sequence[str] = (), tier_ids: Sequence[str] = ()
    ) -> list[Fact]:
        """Benefit rows in SBC order; empty filters mean all services or all tiers."""
        ...

    def question_facts(self, plan_key: str, question_ids: Sequence[str] = ()) -> list[Fact]:
        """Important Questions answers in template order; deductible and OOP answers carry their parse."""
        ...

    def list_facts(self, plan_key: str, list_ids: Sequence[str] = ()) -> list[Fact]:
        """Excluded Services and Other Covered Services items."""
        ...

    def example_facts(self, plan_key: str, example_ids: Sequence[str] = ()) -> list[Fact]:
        """Every line of the Peg, Joe and Mia coverage examples."""
        ...

    def chunk_context(self, plan_key: str, chunk_hashes: Sequence[str]) -> dict[str, ChunkContext]:
        """Pages and flags of the given chunks in one plan; chunks the plan doesn't contain are left out."""
        ...


class SearchIndex(Protocol):
    def search(self, query: SearchQuery) -> list[SearchHit]:
        """Hybrid search restricted to ``query.plan_keys``. Best match first, at most ``query.limit`` hits.

        Implementations must never return a hit that belongs to none of the requested plans, and must report
        only the requested plans in :attr:`SearchHit.plan_keys`.
        """
        ...
