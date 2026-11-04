"""SqliteFactsReader against a synthetic facts bundle."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from sbc_core.facts import BundleError, SqliteFactsReader
from sbc_core.interfaces import FactsReader
from tests.unit.test_bundle import GOLD_SHA, SILVER_SHA, make_bundle

PLAN = "2027:100000000001"
GOLD = "2027:100000000002"


@pytest.fixture
def reader(tmp_path: Path) -> Iterator[SqliteFactsReader]:
    with SqliteFactsReader(make_bundle(tmp_path)) as r:
        yield r


def test_satisfies_the_interface(reader: SqliteFactsReader) -> None:
    facts: FactsReader = reader
    assert facts.bundle_version == "test.1"


def test_missing_or_foreign_file_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(BundleError, match="not found"):
        SqliteFactsReader(tmp_path / "nope.sqlite")
    junk = tmp_path / "junk.sqlite"
    junk.write_bytes(b"not sqlite at all" * 100)
    with pytest.raises(BundleError, match="not a facts bundle"):
        SqliteFactsReader(junk)


def test_bundle_is_opened_read_only(reader: SqliteFactsReader) -> None:
    with pytest.raises(Exception, match="readonly"):
        reader._db.execute("DELETE FROM plan")


def test_plans_skips_unknown_keys(reader: SqliteFactsReader) -> None:
    plans = reader.plans([GOLD, "2027:999999999999", PLAN])
    assert list(plans) == [GOLD, PLAN]
    assert plans[PLAN].contract_code == "ZZ01"
    assert plans[PLAN].market == "Individual"
    assert plans[GOLD].plan_name == "Example Gold"


def test_find_plans_by_name(reader: SqliteFactsReader) -> None:
    assert [p.plan_key for p in reader.find_plans_by_name("gold")] == [GOLD]
    assert [p.plan_key for p in reader.find_plans_by_name("Silver PPO")] == [PLAN]
    assert len(reader.find_plans_by_name("Example")) == 2
    assert reader.find_plans_by_name("Example", states=["ZZ"]) == []
    assert reader.find_plans_by_name("Example", markets=["Individual"], plan_year=2026) == []
    assert reader.find_plans_by_name("ex") == []  # too short for trigram search
    assert reader.find_plans_by_name('"; DROP TABLE plan; --') == []


def test_benefit_fact_carries_verbatim_parse_limitations_and_citation(reader: SqliteFactsReader) -> None:
    (fact,) = reader.benefit_facts(PLAN, ["specialist_visit"], ["in_net"])
    assert fact.key == "benefit:specialist_visit:in_net"
    assert fact.verbatim == "$60/visit, deductible does not apply"
    assert fact.computable
    assert fact.parsed is not None
    assert fact.parsed["parts"][0]["channels"][0]["steps"][0]["kind"] == "copay"
    assert (fact.limitations, fact.limitations_kind) == ("none", "none")
    assert (
        fact.citation.section_path
        == "If you visit a health care provider’s office or clinic > Specialist visit"
    )
    assert fact.citation.label == (
        "Example Silver PPO 3000 (ZZ01), 2027 SBC, "
        "If you visit a health care provider’s office or clinic › Specialist visit, p. 2"
    )
    assert fact.citation.url == "https://example.org/sbc.pdf#page=2"


def test_benefit_facts_are_in_sbc_order(reader: SqliteFactsReader) -> None:
    keys = [f.key for f in reader.benefit_facts(PLAN)]
    assert keys[:2] == ["benefit:specialist_visit:in_net", "benefit:specialist_visit:out_net"]
    assert len(keys) == 4
    assert reader.benefit_facts("2027:999999999999") == []


def test_question_facts_attach_limit_parses(reader: SqliteFactsReader) -> None:
    facts = {f.key: f for f in reader.question_facts(PLAN)}
    assert len(facts) == 7
    deductible = facts["question:overall_deductible"]
    assert deductible.verbatim == "$3,000/person or $6,000/family for In-Network Providers."
    assert deductible.parse_status == "exact"
    assert deductible.parsed is not None
    assert deductible.parsed["entries"][0]["family"] == "6000"
    assert deductible.why_this_matters == "Synthetic."
    assert deductible.citation.label.endswith("What is the overall deductible?, pp. 1-2")
    assert facts["question:specialist_referral"].parsed is None
    only = reader.question_facts(PLAN, ["oop_limit"])
    assert [f.key for f in only] == ["question:oop_limit"]


def test_list_and_example_facts(reader: SqliteFactsReader) -> None:
    excluded = reader.list_facts(PLAN, ["excluded"])
    assert [f.verbatim for f in excluded] == ["Cosmetic surgery", "Long-term care"]
    assert excluded[0].citation.section_path == "Excluded Services"
    peg = {f.label: f.verbatim for f in reader.example_facts(PLAN, ["peg"])}
    assert peg["Total Example Cost"] == "$12,700"
    assert peg["Coinsurance"] == "$2,700"
    assert any(label.startswith("The total Peg would pay is") for label in peg)


def test_chunk_context(reader: SqliteFactsReader) -> None:
    (fact,) = reader.benefit_facts(PLAN, ["specialist_visit"], ["in_net"])
    hashes = list(reader._db.execute("SELECT chunk_hash FROM chunk ORDER BY chunk_id LIMIT 2"))
    found = reader.chunk_context(PLAN, [h for (h,) in hashes] + ["missing"])
    assert set(found) == {h for (h,) in hashes}
    assert all(c.plan_key == PLAN for c in found.values())
    assert reader.chunk_context(PLAN, []) == {}
    assert fact.carried_note is False


def test_find_plans_by_name_applies_scope_exclusions(reader: SqliteFactsReader) -> None:
    assert len(reader.find_plans_by_name("Example", exclude_plan_statuses=["Discontinued"])) == 2
    assert len(reader.find_plans_by_name("Example", exclude_exchanges=["Purchased Prior to 01/01/2014"])) == 2


def test_find_plans_by_identifier(reader: SqliteFactsReader) -> None:
    assert [p.plan_key for p in reader.find_plans_by_identifier("ZZ01")] == [PLAN, GOLD]
    assert [p.plan_key for p in reader.find_plans_by_identifier("100000000002")] == [GOLD]
    assert reader.find_plans_by_identifier("100000000002", plan_year=2026) == []
    assert reader.find_plans_by_identifier("ZZ0") == []
    assert reader.find_plans_by_identifier(" ") == []


# ------------------------------------------------------------------ PDF sources (WP3)


def test_pdf_source_has_the_preview_form_fields(reader: SqliteFactsReader) -> None:
    silver = reader.pdf_source(PLAN)
    assert silver is not None
    assert silver.preview_only
    assert (silver.sbc_id, silver.contract_code, silver.doc_sys_id, silver.dps_unique_id) == (
        "100000000001", "ZZ01", "D1", "U1",
    )  # fmt: skip
    assert (silver.state, silver.market_unit, silver.language, silver.source_system) == (
        "XX", "Individual", "English", "EPDG",
    )  # fmt: skip
    assert silver.pdf_sha256 == SILVER_SHA
    gold = reader.pdf_source(GOLD)
    assert gold is not None
    assert not gold.preview_only
    assert gold.pdf_sha256 == GOLD_SHA
    assert reader.pdf_source("2027:999999999999") is None


def test_bundles_without_plan_source_still_open(tmp_path: Path) -> None:
    import sqlite3
    from contextlib import closing

    path = make_bundle(tmp_path)
    with closing(sqlite3.connect(path)) as db:
        db.execute("DROP TABLE plan_source")
        db.commit()
    with SqliteFactsReader(path, pdf_proxy_base="/api/sbc/") as old:
        assert old.pdf_source(PLAN) is None
        assert old.plans([PLAN])[PLAN].source_url == "https://example.org/sbc.pdf"


def test_without_a_proxy_base_source_urls_are_unchanged(reader: SqliteFactsReader) -> None:
    assert reader.plans([PLAN])[PLAN].source_url == "https://example.org/sbc.pdf"


def test_preview_only_plans_link_to_the_pdf_proxy(tmp_path: Path) -> None:
    with SqliteFactsReader(make_bundle(tmp_path), pdf_proxy_base="/api/sbc/") as r:
        plans = r.plans([PLAN, GOLD])
        assert plans[PLAN].source_url == "/api/sbc/2027%3A100000000001.pdf"
        assert plans[GOLD].source_url == "https://example.org/sbc.pdf"  # has a public link: unchanged
        (fact, *_) = r.question_facts(PLAN, ["overall_deductible"])
        assert fact.citation.url == "/api/sbc/2027%3A100000000001.pdf#page=1"
