"""Domain types."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from sbc_core.types import Citation, Fact, SearchQuery, SparseVector, make_plan_key, split_plan_key

CITE = Citation(plan_key="2027:1", plan_name="P", contract_code="C", plan_year=2027, section_path="A > B",
                pages=(3, 4, 5), source_url="https://x/sbc.pdf")  # fmt: skip


def test_plan_key_round_trip() -> None:
    assert make_plan_key(2027, "100000000001") == "2027:100000000001"
    assert split_plan_key("2027:100000000001") == (2027, "100000000001")
    with pytest.raises(ValueError, match="plan key"):
        make_plan_key(2027, "")


def test_citation_label_and_url() -> None:
    assert CITE.label == "P (C), 2027 SBC, A › B, pp. 3-5"
    assert CITE.url == "https://x/sbc.pdf#page=3"
    assert CITE.model_copy(update={"pages": (1, 3)}).label.endswith("pp. 1, 3")
    assert CITE.model_copy(update={"pages": ()}).url == "https://x/sbc.pdf"


def test_fact_rules() -> None:
    with pytest.raises(ValidationError, match="limitations"):
        Fact(kind="benefit", key="benefit:a:b", plan_key="2027:1", label="x", verbatim="y", citation=CITE)
    with pytest.raises(ValidationError, match="different plan"):
        Fact(kind="question", key="question:q", plan_key="2027:2", label="x", verbatim="y", citation=CITE)
    fact = Fact(kind="question", key="question:q", plan_key="2027:1", label="x", verbatim="y",
                parsed={"a": 1}, parse_status="unparsed", citation=CITE)  # fmt: skip
    assert not fact.computable


def test_search_query_needs_plans_and_a_vector() -> None:
    sparse = SparseVector(indices=(1,), values=(1.0,))
    with pytest.raises(ValidationError):
        SearchQuery(plan_keys=(), text="x", sparse=sparse)
    with pytest.raises(ValidationError, match="dense or a sparse"):
        SearchQuery(plan_keys=("2027:1",), text="x")
    with pytest.raises(ValidationError, match="plan key"):
        SearchQuery(plan_keys=("anything",), text="x", sparse=sparse)
    with pytest.raises(ValidationError, match="duplicate"):
        SparseVector(indices=(1, 1), values=(1.0, 2.0))
