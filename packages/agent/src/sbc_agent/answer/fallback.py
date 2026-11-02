"""The deterministic template answer (PLAN.md 3.5.2 and 3.5.6).

Used when compose fails verification twice, when the model is unavailable, or when a turn hits its limits.
It lists every fact and excerpt the tools returned this turn, grouped by plan, as a draft made only of
placeholders, then renders it like any other draft. It is plainer than a composed answer but goes through
the same verifier and renderer, so it can't show an unchecked value either.
"""

from __future__ import annotations

from sbc_agent.answer.render import RenderedAnswer, render
from sbc_agent.answer.verify import verify
from sbc_core.config import BotProfile
from sbc_tools.context import Turn

NOTHING_FOUND = (
    "I couldn't answer this from the SBC. Please check the plan's Summary of Benefits and Coverage, or call "
    "the plan."
)


def template_draft(turn: Turn) -> str:
    plan_ids = {plan.plan_key: pid for pid, plan in turn.plans.items()}
    sections: dict[str, list[str]] = {}
    for fid, fact in turn.facts.items():
        tier = f" ({{{{{fid}.tier}}}})" if fact.tier_label else ""
        sections.setdefault(plan_ids[fact.plan_key], []).append(f"- {{{{{fid}.label}}}}{tier}: {{{{{fid}}}}}")
    for sid, (_, _, plan_key) in turn.chunks.items():
        sections.setdefault(plan_ids[plan_key], []).append(f"- {{{{{sid}}}}}")
    blocks = [
        "\n".join([f"{{{{{pid}.name}}}} ({{{{{pid}.code}}}}):", *lines]) for pid, lines in sections.items()
    ]
    return "\n\n".join(blocks)


def template_answer(turn: Turn, profile: BotProfile) -> RenderedAnswer | str:
    """The rendered template answer, or a plain apology when the turn has no facts to show."""
    draft = template_draft(turn)
    if not draft:
        return NOTHING_FOUND
    report = verify(draft, turn, profile)
    if not report.ok:  # a template built from placeholders only can't fail; refuse rather than show it
        raise RuntimeError(f"template answer failed verification: {report.feedback()}")
    return render(draft, turn, mode="template")
