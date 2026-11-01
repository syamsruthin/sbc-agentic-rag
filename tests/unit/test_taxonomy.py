"""Service taxonomy: file consistency, row classification and user-term matching."""

from __future__ import annotations

import pytest

from sbc_core.taxonomy import Taxonomy, TaxonomyError, load_taxonomy, normalize

OFFICE = "If you visit a health care provider’s office or clinic"
DRUGS = "If you need drugs to treat your illness or condition"
OUTPATIENT = "If you have outpatient surgery"
HOSPITAL = "If you have a hospital stay"
CHILD = "If your child needs dental or eye care"


@pytest.fixture(scope="module")
def tax() -> Taxonomy:
    return load_taxonomy()


def test_taxonomy_covers_all_47_sbc_labels(tax: Taxonomy) -> None:
    medical = {label for s in tax.services.values() for label in s.labels}
    assert len(medical) + len(tax.drug_labels) == 47
    assert len(tax.events) == 10


def test_physician_fees_resolve_by_event(tax: Taxonomy) -> None:
    assert tax.classify_row(OUTPATIENT, "Physician/surgeon fees").service_id == "outpatient_surgeon"
    assert tax.classify_row(HOSPITAL, "Physician/surgeon fees").service_id == "inpatient_physician"


def test_drug_rows_get_tier_ids_and_plan_specific_tags(tax: Taxonomy) -> None:
    generic_t2 = tax.classify_row(DRUGS, "Typically Generic (Tier 2)")
    brand_t2 = tax.classify_row(DRUGS, "Typically Preferred Brand (Tier 2)")
    assert generic_t2.service_id == brand_t2.service_id == "rx_tier_2"
    assert generic_t2.kind == "drug"
    assert generic_t2.drug_tags == ("generic",)
    assert brand_t2.drug_tags == ("preferred_brand",)
    assert tax.classify_row(DRUGS, "Typically Lower Cost Generic (Tier 1a)").service_id == "rx_tier_1a"


def test_curly_apostrophe_labels_classify(tax: Taxonomy) -> None:
    assert tax.classify_row(CHILD, "Children’s glasses").service_id == "child_glasses"


@pytest.mark.parametrize(
    ("event", "label"),
    [
        (OFFICE, "Chiropractic visit"),
        (DRUGS, "Specialty drugs (Tier 9)"),
        ("If you travel abroad", "Specialist visit"),
        (OFFICE, "Physician/surgeon fees"),
    ],
)
def test_unknown_labels_fail_loudly(tax: Taxonomy, event: str, label: str) -> None:
    with pytest.raises(TaxonomyError):
        tax.classify_row(event, label)


@pytest.mark.parametrize(
    ("term", "expected"),
    [
        ("MRI", ("imaging",)),
        ("  ER  ", ("emergency_room",)),
        ("Children’s glasses", ("child_glasses",)),
        ("physical therapy", ("rehabilitation",)),
        ("PCP visit?", ("primary_care_visit",)),
        ("labor and delivery", ("childbirth_facility",)),
    ],
)
def test_synonyms_match_after_normalization(tax: Taxonomy, term: str, expected: tuple[str, ...]) -> None:
    match = tax.match_term(term)
    assert match.service_ids == expected
    assert not match.ambiguous


def test_ambiguous_terms_return_every_candidate(tax: Taxonomy) -> None:
    match = tax.match_term("office visit")
    assert match.ambiguous
    assert set(match.service_ids) == {"primary_care_visit", "specialist_visit"}


def test_drug_wording_maps_to_tags(tax: Taxonomy) -> None:
    assert tax.match_term("generic drugs").drug_tags == ("generic",)
    assert tax.match_term("Rx").drug_tags == ("any_drug",)
    assert tax.match_term("generic drugs").service_ids == ()


def test_unmatched_term_is_falsy(tax: Taxonomy) -> None:
    assert not tax.match_term("acupuncture")


def test_every_synonym_resolves_to_its_own_service(tax: Taxonomy) -> None:
    for service in tax.services.values():
        for term in service.synonyms:
            assert tax.match_term(term).service_ids == (service.id,), term


def test_normalize() -> None:
    assert normalize("  Children’s  Dental Check-Up. ") == "children's dental check-up"
