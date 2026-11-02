"""The answer path: placeholders, the number and citation firewall, the renderer and the template fallback."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from sbc_agent.answer.fallback import NOTHING_FOUND, template_answer, template_draft
from sbc_agent.answer.placeholders import find_placeholders, problem
from sbc_agent.answer.render import CARRIED_NOTE_LABEL, INFERRED_HEADING_LABEL, NOT_AFFILIATED, render
from sbc_agent.answer.verify import verify
from sbc_core.config import load_config
from sbc_core.facts import SqliteFactsReader
from sbc_core.types import Citation, Fact
from sbc_tools import tools as t
from sbc_tools.context import Turn
from tests.unit.test_bundle import make_bundle
from tests.unit.test_tools import GOLD, SILVER, make_ctx

REPO = Path(__file__).resolve().parents[2]
BROKER = load_config("broker", "local", REPO / "config").bot
PUBLIC = load_config("public", "local", REPO / "config").bot


@pytest.fixture(scope="module")
def bundle(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return make_bundle(tmp_path_factory.mktemp("bundle"))


@pytest.fixture
def turn(bundle: Path) -> Iterator[Turn]:
    """A turn where get_plan_facts returned the Silver plan's deductible (F1) and specialist row (F2, F3)."""
    with SqliteFactsReader(bundle) as reader:
        ctx = make_ctx(reader, resolved=(SILVER, GOLD))
        t.get_plan_facts(
            ctx,
            t.GetPlanFactsInput(
                plan_keys=(SILVER,), service_ids=("specialist_visit",), question_ids=("overall_deductible",)
            ),
        )
        yield ctx.turn


def _add_benefit(
    turn: Turn, *, limitations: str, carried: bool = False, inferred: bool = False, tier: str = "in_net"
) -> str:
    """Register a benefit fact with text limitations (the fixture's rows have none) and return its ID."""
    plan = next(iter(turn.plans.values()))
    citation = Citation(
        plan_key=plan.plan_key, plan_name=plan.plan_name, contract_code=plan.contract_code, plan_year=2027,
        section_path="If you need immediate medical attention > Emergency room care", pages=(3,),
        source_url=plan.source_url,
    )  # fmt: skip
    fact = Fact(
        kind="benefit", key=f"benefit:emergency_room:{tier}", plan_key=plan.plan_key,
        label="Emergency room care",
        verbatim="$250/visit then 30% coinsurance", tier_id=tier,
        tier_label="In-Network Provider" if tier == "in_net" else "Out-of-Network Provider",
        limitations=limitations, limitations_kind="text", carried_note=carried, event_inferred=inferred,
        citation=citation,
    )  # fmt: skip
    return turn.fact_id(fact)


# ------------------------------------------------------------------ placeholders


def test_placeholders_must_name_this_turns_ids(turn: Turn) -> None:
    found = find_placeholders("{{F1}} and {{ F2.tier }} for {{P1.name}}, {{P1}}, {{F9}}, {{P1.state}}")
    assert [(p.id, p.field) for p in found] == [
        ("F1", None), ("F2", "tier"), ("P1", "name"), ("P1", None), ("F9", None), ("P1", "state"),
    ]  # fmt: skip
    problems = [problem(p, turn) for p in found]
    assert problems[:3] == [None, None, None]
    assert "needs a field" in (problems[3] or "")
    assert "not returned by a tool" in (problems[4] or "")
    assert "not a field" in (problems[5] or "")


# ------------------------------------------------------------------ verify


def test_a_placeholder_only_draft_passes(turn: Turn) -> None:
    report = verify(
        "The specialist copay on {{P1.name}} is {{F1}}; out of network it's {{F2}}.", turn, PUBLIC
    )
    assert report.ok
    assert report.value_ids == ("F1", "F2")


@pytest.mark.parametrize(
    ("draft", "kind"),
    [
        ("The specialist copay is $60 per visit.", "typed_value"),
        ("You pay 50% after the deductible {{F1}}.", "typed_value"),
        ("It costs a few hundred dollars {{F1}}.", "typed_value"),
        ("The ER is free on {{P1.name}}.", "typed_value"),
        ("Plan option 52 has {{F1}}.", "typed_value"),
        ("See https://evil.example/sbc for {{F1}}.", "url"),
        ("{{F7}} is the copay.", "placeholder"),
        ("   ", "empty"),
    ],
)
def test_typed_values_unknown_ids_and_links_are_rejected(turn: Turn, draft: str, kind: str) -> None:
    report = verify(draft, turn, BROKER)
    assert not report.ok
    assert kind in {v.kind for v in report.violations}
    assert report.feedback().startswith("Your answer was rejected:")


def test_advice_is_rejected_only_for_bots_that_may_not_recommend(turn: Turn) -> None:
    draft = "I recommend {{P1.name}} because its specialist copay is {{F1}}."
    assert {v.kind for v in verify(draft, turn, PUBLIC).violations} == {"advice"}
    assert verify(draft, turn, BROKER).ok


def test_an_answer_without_values_can_be_required(turn: Turn) -> None:
    assert verify("{{P1.name}} has a specialist copay.", turn, BROKER).ok
    report = verify("{{P1.name}} has a specialist copay.", turn, BROKER, require_values=True)
    assert [v.kind for v in report.violations] == ["no_values"]


# ------------------------------------------------------------------ render


def test_render_substitutes_wording_cites_pages_and_adds_the_disclaimer(turn: Turn) -> None:
    answer = render("On {{P1.name}} a specialist visit is {{F2}}. Out of network: {{F3}}.", turn)
    md = answer.markdown
    assert md.startswith(
        "**Example Silver PPO 3000 (ZZ01) · 2027 SBC · coverage period 01/01/2027 - 12/31/2027**"
    )
    assert (
        "a specialist visit is $60/visit, deductible does not apply [1]. Out of network: 50% coinsurance [1]."
        in md
    )
    assert len(answer.citations) == 1  # both values are on the same row and page
    citation = answer.citations[0]
    assert citation.url.startswith("https://example.org/sbc.pdf#page=")
    assert "Example Silver PPO 3000 (ZZ01), 2027 SBC" in citation.label
    assert "this plan's 2027 Summary of Benefits and Coverage" in md
    assert NOT_AFFILIATED in md
    assert answer.value_ids == ("F2", "F3")
    assert answer.plan_keys == (SILVER,)


def test_limitations_are_added_unless_quoted(turn: Turn) -> None:
    fid = _add_benefit(turn, limitations="Copayment waived if admitted.")
    added = render(f"The ER is {{{{{fid}}}}}.", turn).markdown
    note = "Note from the SBC (Emergency room care, In-Network Provider): “Copayment waived if admitted.”"
    assert note in added
    quoted = render(f"The ER is {{{{{fid}}}}}; the SBC adds: copayment waived if admitted.", turn).markdown
    assert "Note from the SBC" in quoted  # paraphrase isn't the SBC's wording
    exact = render(f"The ER is {{{{{fid}}}}}. Copayment waived if admitted.", turn).markdown
    assert "Note from the SBC" not in exact


def test_a_limitation_shared_by_tiers_is_noted_once(turn: Turn) -> None:
    a = _add_benefit(turn, limitations="Copayment waived if admitted.")
    b = _add_benefit(turn, limitations="Copayment waived if admitted.", tier="out_net")
    md = render(f"In network {{{{{a}}}}}; out of network {{{{{b}}}}}.", turn).markdown
    assert md.count("Copayment waived if admitted.") == 1
    assert "(Emergency room care, In-Network Provider and Out-of-Network Provider)" in md


def test_an_amount_is_one_violation(turn: Turn) -> None:
    report = verify("The ER costs $1,250.00 or 30% on {{P1.name}}.", turn, BROKER)
    assert [v.detail.split('"')[1] for v in report.violations] == ["$1,250.00", "30%"]


def test_carried_notes_and_inferred_headings_are_labelled(turn: Turn) -> None:
    fid = _add_benefit(turn, limitations="*See Therapy Services section.", carried=True, inferred=True)
    answer = render(f"The ER is {{{{{fid}}}}}.", turn)
    assert f"“*See Therapy Services section.” {CARRIED_NOTE_LABEL}" in answer.markdown
    label = answer.citations[0].label
    assert "If you need immediate medical attention" not in label  # the inferred heading is never shown
    assert label.endswith(f"Emergency room care, p. 3 {INFERRED_HEADING_LABEL}")


def test_two_plans_get_two_headers_and_a_plural_disclaimer(bundle: Path) -> None:
    with SqliteFactsReader(bundle) as reader:
        ctx = make_ctx(reader, resolved=(SILVER, GOLD))
        t.compare_plans(ctx, t.ComparePlansInput(plan_keys=(SILVER, GOLD), aspects=("oop_limit",)))
        answer = render("{{P1.name}}: {{F1}}. {{P2.name}}: {{F2}}.", ctx.turn)
    assert answer.markdown.count("· 2027 SBC ·") == 2
    assert "these plans' 2027 Summary" in answer.markdown
    assert len(answer.citations) == 2


# ------------------------------------------------------------------ fallback


def test_template_answer_lists_every_fact_through_the_same_checks(turn: Turn) -> None:
    draft = template_draft(turn)
    assert draft.startswith("{{P1.name}} ({{P1.code}}):")
    assert "- {{F1.label}}: {{F1}}" in draft  # the deductible answer has no provider column
    assert "- {{F2.label}} ({{F2.tier}}): {{F2}}" in draft
    answer = template_answer(turn, PUBLIC)
    assert not isinstance(answer, str)
    assert answer.mode == "template"
    for fact in turn.facts.values():
        assert fact.verbatim in answer.markdown


def test_template_answer_without_facts_apologizes() -> None:
    assert template_answer(Turn(), BROKER) == NOTHING_FOUND
