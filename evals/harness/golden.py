"""Golden dataset records (PLAN.md section 6.3), loading, and a consistency check against a facts bundle.

Gold values are verified by a person against the SBC PDF. The bundle check is not a substitute: it flags
disagreements between gold and the extracted facts so someone looks at the PDF, which keeps the gold from
silently inheriting an extractor bug.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Category = Literal[
    "single_field_lookup",
    "multi_field",
    "comparison",
    "cost_scenario",
    "exclusions_limitations",
    "network_tiers",
    "coverage_examples",
    "discovery_ranking",
    "ambiguous",
    "out_of_scope",
    "adversarial",
    "multi_turn",
    "review_flags",
]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Turn(_Model):
    role: Literal["user", "assistant"]
    content: str


class ExpectedValue(_Model):
    plan_key: str
    fact: str = Field(pattern=r"^(benefit:[a-z0-9_]+:[a-z0-9_]+|question:[a-z_]+|example:(peg|joe|mia):.+)$")
    """benefit:<service_id>:<tier_id> | question:<question_id> | example:<example_id>:<line label>."""
    verbatim: str


class ExpectedTool(_Model):
    tool: str
    args_subset: dict[str, object] = Field(default_factory=dict)


class Gold(_Model):
    plan_year: int | None = None
    plan_keys: tuple[str, ...] = ()
    expected_behavior: Literal["answer", "clarify", "refuse", "partial"]
    expected_values: tuple[ExpectedValue, ...] = ()
    sections: tuple[str, ...] = ()
    chunk_hashes: tuple[str, ...] = ()
    expected_tools: tuple[ExpectedTool, ...] = ()
    forbidden_tools: tuple[str, ...] = ()
    must_include: tuple[str, ...] = ()
    must_not_include: tuple[str, ...] = ()
    clarify_options_must_include: tuple[str, ...] = ()
    reference_answer: str = ""


class Provenance(_Model):
    method: Literal["hand", "synthetic", "mined"]
    author: str
    verified_against: Literal["pdf", "pending"]
    pdf_pages: tuple[int, ...] = ()
    reviewed_by: str | None = None
    reviewed_at: str | None = None


class GoldenItem(_Model):
    id: str = Field(pattern=r"^gs-\d{6}$")
    dataset_version: str
    bot: Literal["broker", "public", "both"]
    category: Category
    subcategory: str = ""
    difficulty: Literal[1, 2, 3]
    turns: tuple[Turn, ...] = Field(min_length=1)
    context: dict[str, object] = Field(default_factory=dict)
    gold: Gold
    provenance: Provenance
    tags: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _check(self) -> GoldenItem:
        if self.turns[-1].role != "user":
            raise ValueError("the last turn must be the user's")
        needs_evidence = self.gold.expected_behavior == "answer" and self.category not in (
            "out_of_scope",
            "adversarial",
        )
        if needs_evidence and not (
            self.gold.expected_values or self.gold.must_include or self.gold.reference_answer
        ):
            raise ValueError("an 'answer' item needs expected values, must_include or a reference answer")
        missing = {v.plan_key for v in self.gold.expected_values} - set(self.gold.plan_keys)
        if missing:
            raise ValueError(f"expected_values use plan keys not in gold.plan_keys: {sorted(missing)}")
        return self

    @property
    def gating(self) -> bool:
        """Only PDF-verified, reviewed items may gate merges (PLAN.md section 6.1)."""
        return self.provenance.verified_against == "pdf" and self.provenance.reviewed_by is not None


def load_golden(directory: Path) -> list[GoldenItem]:
    items: list[GoldenItem] = []
    for path in sorted(directory.glob("*.jsonl")):
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if line.strip():
                try:
                    items.append(GoldenItem.model_validate_json(line))
                except ValueError as exc:
                    raise ValueError(f"{path.name}:{n}: {exc}") from None
    ids = [i.id for i in items]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate golden item ids")
    return items


def _bundle_value(db: sqlite3.Connection, plan_key: str, fact: str) -> str | None:
    kind, *rest = fact.split(":", 2)
    if kind == "benefit":
        row = db.execute(
            """SELECT c.verbatim FROM plan_benefit b JOIN plan p USING (plan_id)
               JOIN cost_string c USING (cost_id)
               WHERE p.plan_key = ? AND b.service_id = ? AND b.tier_id = ?""",
            (plan_key, rest[0], rest[1]),
        ).fetchone()
    elif kind == "question":
        row = db.execute(
            """SELECT t.text FROM plan_question q JOIN plan p USING (plan_id) JOIN text_string t
               ON t.text_id = q.answer_id WHERE p.plan_key = ? AND q.question_id = ?""",
            (plan_key, rest[0]),
        ).fetchone()
    else:
        found = db.execute(
            """SELECT t.text FROM plan_example e JOIN plan p USING (plan_id) JOIN text_string t
               ON t.text_id = e.data_id WHERE p.plan_key = ? AND e.example_id = ?""",
            (plan_key, rest[0]),
        ).fetchone()
        if not found:
            return None
        data = json.loads(found[0])
        lines = dict(data["plan_terms"]) | dict(data["cost_sharing"]) | dict(data["not_covered"])
        return lines.get(rest[1])
    return row[0] if row else None


def check_against_bundle(items: list[GoldenItem], bundle: Path) -> list[str]:
    """Disagreements between gold values and the bundle, for a person to resolve against the PDF."""
    problems = []
    with closing(sqlite3.connect(bundle)) as db:
        for item in items:
            for value in item.gold.expected_values:
                found = _bundle_value(db, value.plan_key, value.fact)
                if found is None:
                    problems.append(f"{item.id}: {value.fact} not in bundle for {value.plan_key}")
                elif found != value.verbatim:
                    problems.append(f"{item.id}: {value.fact} gold={value.verbatim!r} bundle={found!r}")
    return problems
