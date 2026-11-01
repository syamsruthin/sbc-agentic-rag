"""Parse the deductible and out-of-pocket answers of the Important Questions (PLAN.md section 3.2.3).

Like the cost grammar, a parse is ``exact`` only if rendering it back reproduces the answer; otherwise it is
``unparsed`` and the answer is shown verbatim without any computation.
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict

from sbc_core.tiers import LIMIT_PHRASE_TIERS, TierId

LimitKind = Literal["deductible", "oop"]

_AMT = r"[\d,]+(?:\.\d+)?"
_PHRASES = "|".join(re.escape(p) for p in sorted(LIMIT_PHRASE_TIERS, key=len, reverse=True))
_TWO_RE = re.compile(rf"^\$(?P<ind>{_AMT})/(?P<unit>person|single|member) or \$(?P<fam>{_AMT})/family"
                     rf"(?: for (?P<tier>{_PHRASES}))?$")  # fmt: skip
_THREE_RE = re.compile(rf"^\$(?P<ind>{_AMT})/individual, \$(?P<mem>{_AMT})/member or \$(?P<fam>{_AMT})/family"
                       rf" for (?P<tier>{_PHRASES})$")  # fmt: skip
_FLAT_RE = re.compile(rf"^\$(?P<amt>{_AMT})$")
_IHCP_RE = re.compile(
    rf"^\$(?P<amt>{_AMT}) at Indian Health Care Provider \(IHCP\) or with IHCP referral at Non-IHCP$"
)
_NA_RE = re.compile(rf"^Not Applicable(?: for (?P<tier>{_PHRASES}))?$")
_SEPARATE_RE = re.compile(
    rf"^This plan has a separate Out-of-Pocket Maximum of \$(?P<amt>{_AMT})/(?P<unit>member|single)"
    rf" for (?P<tier>{_PHRASES}) for (?P<what>(?:Tier \d )*(?:Tier \d )?Prescription Drugs)$"
)
HRA_NOTE = "An HRA may be available to reimburse you for certain deductible and coinsurance amounts"

_EMBEDDED_WHY = {
    "each family member must meet their own individual deductible": True,
    "the overall family deductible must be met before the plan begins to pay": False,
    "they have to meet their own out-of-pocket limits": True,
    "the overall family out-of-pocket limit must be met": False,
}


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class LimitEntry(_Model):
    tiers: tuple[TierId, ...]
    """Empty when the SBC names no tier (the amount applies plan-wide)."""
    tier_phrase: str | None = None
    individual: Decimal | None = None
    individual_unit: Literal["person", "single", "member", "individual"] | None = None
    per_member: Decimal | None = None
    """The member amount in the three-part "$X/individual, $Y/member or $Z/family" form."""
    family: Decimal | None = None
    ihcp_referral: bool = False
    not_applicable: bool = False


class SeparateLimit(_Model):
    amount: Decimal
    unit: Literal["member", "single"]
    tiers: tuple[TierId, ...]
    tier_phrase: str
    applies_to: str


class LimitParse(_Model):
    kind: LimitKind
    verbatim: str
    status: Literal["exact", "unparsed", "blank"]
    entries: tuple[LimitEntry, ...] = ()
    separate: tuple[SeparateLimit, ...] = ()
    hra_note: bool = False
    embedded: bool | None = None
    """True when each family member has their own limit inside the family limit; None when not stated."""
    reason: str | None = None


def _money(text: str) -> Decimal:
    return Decimal(text.replace(",", ""))


def _sentences(answer: str) -> list[str]:
    text = answer.strip()
    text = text.removesuffix(".")
    return [s.strip() for s in re.split(r"\.\s+", text) if s.strip()]


def _parse_sentence(s: str, out: list[LimitEntry], separate: list[SeparateLimit]) -> bool:
    """Parse one sentence; returns True for the HRA note. Raises ValueError for anything unknown."""
    if s == HRA_NOTE:
        return True
    if m := _SEPARATE_RE.match(s):
        separate.append(
            SeparateLimit(
                amount=_money(m["amt"]), unit=m["unit"], tiers=LIMIT_PHRASE_TIERS[m["tier"]],  # type: ignore[arg-type]
                tier_phrase=m["tier"], applies_to=m["what"],
            )
        )  # fmt: skip
        return False
    # "$N at IHCP ...; or $X/person or $Y/family for Non-IHCP In-Network Providers"
    for piece in s.split("; or "):
        if m := _IHCP_RE.match(piece):
            out.append(LimitEntry(tiers=("ihcp",), individual=_money(m["amt"]), ihcp_referral=True))
        elif m := _THREE_RE.match(piece):
            out.append(
                LimitEntry(
                    tiers=LIMIT_PHRASE_TIERS[m["tier"]], tier_phrase=m["tier"], individual=_money(m["ind"]),
                    individual_unit="individual", per_member=_money(m["mem"]), family=_money(m["fam"]),
                )
            )  # fmt: skip
        elif m := _TWO_RE.match(piece):
            tier = m["tier"]
            out.append(
                LimitEntry(
                    tiers=LIMIT_PHRASE_TIERS[tier] if tier else (), tier_phrase=tier,
                    individual=_money(m["ind"]), individual_unit=m["unit"], family=_money(m["fam"]),  # type: ignore[arg-type]
                )
            )  # fmt: skip
        elif m := _FLAT_RE.match(piece):
            out.append(LimitEntry(tiers=(), individual=_money(m["amt"])))
        elif m := _NA_RE.match(piece):
            tier = m["tier"]
            out.append(
                LimitEntry(
                    tiers=LIMIT_PHRASE_TIERS[tier] if tier else (), tier_phrase=tier, not_applicable=True
                )
            )
        else:
            raise ValueError(f"no rule for {piece!r}")
    return False


def _render(parsed: LimitParse) -> str:
    def amt(d: Decimal | None) -> str:
        return f"${d:,}"

    def entry(e: LimitEntry) -> str:
        suffix = f" for {e.tier_phrase}" if e.tier_phrase else ""
        if e.not_applicable:
            return "Not Applicable" + suffix
        if e.ihcp_referral:
            return (
                f"{amt(e.individual)} at Indian Health Care Provider (IHCP) or with IHCP referral at Non-IHCP"
            )
        if e.per_member is not None:
            three = f"{amt(e.individual)}/individual, {amt(e.per_member)}/member or {amt(e.family)}/family"
            return three + suffix
        if e.family is not None:
            return f"{amt(e.individual)}/{e.individual_unit} or {amt(e.family)}/family{suffix}"
        return amt(e.individual)

    parts: list[str] = []
    entries = list(parsed.entries)
    while entries:
        e = entries.pop(0)
        text = entry(e)
        if e.ihcp_referral and entries:
            text += "; or " + entry(entries.pop(0))
        parts.append(text)
    for s in parsed.separate:
        parts.append(
            f"This plan has a separate Out-of-Pocket Maximum of {amt(s.amount)}/{s.unit} for {s.tier_phrase}"
            f" for {s.applies_to}"
        )
    if parsed.hra_note:
        parts.append(HRA_NOTE)
    return ". ".join(parts)


def _canonical(answer: str) -> str:
    return re.sub(r"\s+", " ", answer).strip().removesuffix(".")


def embedded_from_why(why: str) -> bool | None:
    return next((value for phrase, value in _EMBEDDED_WHY.items() if phrase in why), None)


def parse_limit(kind: LimitKind, answer: str, why: str) -> LimitParse:
    if not answer.strip():
        return LimitParse(
            kind=kind, verbatim=answer, status="blank", reason="answer is blank in the Markdown"
        )
    entries: list[LimitEntry] = []
    separate: list[SeparateLimit] = []
    hra = False
    try:
        for sentence in _sentences(answer):
            hra = _parse_sentence(sentence, entries, separate) or hra
    except ValueError as exc:
        return LimitParse(kind=kind, verbatim=answer, status="unparsed", reason=str(exc))
    parsed = LimitParse(
        kind=kind, verbatim=answer, status="exact", entries=tuple(entries), separate=tuple(separate),
        hra_note=hra, embedded=embedded_from_why(why),
    )  # fmt: skip
    if _render(parsed) != _canonical(answer):
        return LimitParse(
            kind=kind, verbatim=answer, status="unparsed", reason=f"re-render: {_render(parsed)!r}"
        )
    return parsed
