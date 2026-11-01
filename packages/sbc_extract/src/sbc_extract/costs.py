"""Deterministic grammar for SBC cost-sharing cells (PLAN.md section 3.2.3).

A cell such as ``$250/visit deductible does not apply then 30% coinsurance deductible does not apply`` is
parsed into structured terms. A parse only counts as ``exact`` if rendering those terms back gives the cell's
own text (after formatting-only normalization: whitespace, comma placement before "deductible", and the
``then`` separator). Anything else is ``unparsed``: the cell is still shown verbatim, but nothing computes
from it.
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict

from sbc_core.sublabels import KNOWN_SUBLABELS

GRAMMAR_VERSION = 1
BLANK = "(blank in the SBC)"

TermKind = Literal[
    "copay",
    "copay_up_to_allowed_amount",
    "coinsurance",
    "greater_of",
    "no_charge",
    "not_covered",
    "not_applicable",
    "same_as_in_network",
    "covered_as_in_network",
    "included_in_facility_fee",
    "reimbursed_up_to",
    "amount_above_reimbursement",
]
Channel = Literal["all", "retail", "home_delivery", "retail_and_home_delivery", "retail_only"]
Deductible = Literal["applies", "does_not_apply", "not_stated"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CostTerm(_Model):
    kind: TermKind
    amount: Decimal | None = None
    """Copay or reimbursement dollars; for greater_of, the dollar floor."""
    unit: str | None = None
    pct: int | None = None
    cap_amount: Decimal | None = None
    cap_unit: Literal["admission", "prescription", "maximum"] | None = None
    max_days_per_admission: int | None = None
    deductible: Deductible = "not_stated"
    deductible_name: str | None = None
    """Which deductible, when the SBC names one (e.g. "Prescription Drug")."""
    same_as_tier: str | None = None
    first_visits: int | None = None
    """This term applies to the first N visits only; the next term in the chain applies after that."""
    first_visits_wording: str | None = None
    """The SBC's own phrasing of that limit, e.g. "for the first 3 visits" or "first 3 visits"."""
    wording: str | None = None
    """Exact wording of a variant atom, e.g. "No charge/benefit"."""
    for_physician_services: bool = False
    greater_of_style: Literal["whichever", "greater_of"] | None = None


class ChannelCost(_Model):
    channel: Channel
    steps: tuple[CostTerm, ...]


class CostPart(_Model):
    sublabel: str | None
    channels: tuple[ChannelCost, ...]


class CostParse(_Model):
    verbatim: str
    status: Literal["exact", "unparsed", "blank"]
    parts: tuple[CostPart, ...] = ()
    reason: str | None = None
    grammar_version: int = GRAMMAR_VERSION


# ---------------------------------------------------------------- normalization

_LABELS = "|".join(re.escape(label) for label in sorted(KNOWN_SUBLABELS, key=len, reverse=True))
_PART_SPLIT_RE = re.compile(rf"; (?=(?:{_LABELS}): )")
_PART_LABEL_RE = re.compile(rf"^({_LABELS}): (.+)$")
_DED_NAMES = r"(?:Prescription Drug |Home Health Care |vision |Durable Medical Equipment )"
_MISSING_COMMA_RE = re.compile(
    r"(?<=[^,;])(?<!Prescription Drug)(?<!Home Health Care)(?<!vision)(?<!Durable Medical Equipment)"
    rf" (?={_DED_NAMES}?deductible (?:does not apply|applies))"
)


def canonical(text: str) -> str:
    """Formatting-only normalization shared by the parser and the re-render check."""
    text = re.sub(r"\s+", " ", text).strip()
    text = text.replace("prescriptio n", "prescription")  # word wrapped mid-word in some PDFs
    text = text.replace("; deductible does not apply", ", deductible does not apply")
    text = _MISSING_COMMA_RE.sub(", ", text)
    text = re.sub(r"(?:,|;)? then ", ", then ", text)
    return text


# ---------------------------------------------------------------- grammar

_AMT = r"[\d,]+(?:\.\d+)?"
_UNITS = r"visit|prescription|admission|day|trip|pregnancy|unit|procedure|service|test"
_TERM_RE = re.compile(
    rf"""^(?:
        \$(?P<copay>{_AMT})/(?P<unit>{_UNITS})
            (?:\ to\ a\ maximum\ of\ \$(?P<adm_cap>{_AMT})/admission)?
            (?:\ up\ to\ (?P<days>\d+)\ days/admission)?
      | \$(?P<allowed>{_AMT})\ copayment\ up\ to\ plan's\ Maximum\ Allowed\ Amount
      | (?P<pct>\d+)%\ coinsurance(?:\ up\ to\ \$(?P<rx_cap>{_AMT})/prescription)?
      | \$(?P<w_amt>{_AMT})/prescription\ or\ (?P<w_pct>\d+)%\ coinsurance,\ whichever\ is\ greater
      | Greater\ of\ \$(?P<g_amt>{_AMT})\ or\ (?P<g_pct>\d+)%\ coinsurance
            (?:\ up\ to\ \$(?P<g_cap>{_AMT})/prescription)?
      | (?P<atom>No\ charge|Not\ covered|Not\ Applicable|Covered\ as\ In-Network|Included\ in\ facility\ fee)
      | Same\ as\ In-Network(?:\ Tier\ (?P<tier>\w+))?
      | Reimbursed\ Up\ to\ \$(?P<reimb>{_AMT})
      | Amount\ above\ \$(?P<above>{_AMT})\ Reimbursement
    )
    (?:,\ (?P<ded_name>{_DED_NAMES.replace(" ", "\\ ")})?deductible\ (?P<ded>does\ not\ apply|applies))?
    (?:,\ up\ to\ a\ \$(?P<max>{_AMT})\ maximum)?
    (?P<physician>\ for\ physician\ services)?
    $""",
    re.VERBOSE,
)
_ATOMS: dict[str, TermKind] = {
    "No charge": "no_charge",
    "Not covered": "not_covered",
    "Not Applicable": "not_applicable",
    "Covered as In-Network": "covered_as_in_network",
    "Included in facility fee": "included_in_facility_fee",
}
_VISITS = r"(?P<phrase>(?:for the )?first (?:visit|(?P<n>\d+) visits))"
_FIRST_VISITS_RE = [
    re.compile(rf"^(?P<nc>No charge(?:/benefit)?) {_VISITS}$"),
    re.compile(rf"^(?P<base>.+?) {_VISITS}(?:, deductible (?P<ded>does not apply))?$"),
]
_CHANNEL_RES: list[tuple[re.Pattern[str], tuple[Channel, ...]]] = [
    (re.compile(r"^(.+?) \(retail\) and (.+) \(home delivery\)$"), ("retail", "home_delivery")),
    (re.compile(r"^(.+) \(retail and home delivery\)$"), ("retail_and_home_delivery",)),
    (re.compile(r"^(.+) \(retail only\)$"), ("retail_only",)),
]


class _UnparsedError(ValueError):
    pass


def _money(text: str | None) -> Decimal | None:
    return Decimal(text.replace(",", "")) if text else None


def _term(text: str) -> CostTerm:
    m = _TERM_RE.match(text)
    if not m:
        raise _UnparsedError(f"no grammar rule for {text!r}")
    g = m.groupdict()
    ded_map: dict[str, Deductible] = {"does not apply": "does_not_apply", "applies": "applies"}
    deductible: Deductible = ded_map.get(g["ded"] or "", "not_stated")
    common = {
        "deductible": deductible,
        "deductible_name": g["ded_name"].strip() if g["ded_name"] else None,
        "for_physician_services": bool(g["physician"]),
    }
    if g["max"]:
        common |= {"cap_amount": _money(g["max"]), "cap_unit": "maximum"}
    if g["copay"]:
        extra: dict[str, object] = {}
        if g["adm_cap"]:
            extra = {"cap_amount": _money(g["adm_cap"]), "cap_unit": "admission"}
        if g["days"]:
            extra["max_days_per_admission"] = int(g["days"])
        return CostTerm(kind="copay", amount=_money(g["copay"]), unit=g["unit"], **common, **extra)  # type: ignore[arg-type]
    if g["allowed"]:
        return CostTerm(kind="copay_up_to_allowed_amount", amount=_money(g["allowed"]), **common)  # type: ignore[arg-type]
    if g["pct"]:
        cap = {"cap_amount": _money(g["rx_cap"]), "cap_unit": "prescription"} if g["rx_cap"] else {}
        return CostTerm(kind="coinsurance", pct=int(g["pct"]), **common, **cap)  # type: ignore[arg-type]
    if g["w_amt"]:
        return CostTerm(
            kind="greater_of", amount=_money(g["w_amt"]), unit="prescription", pct=int(g["w_pct"]),
            greater_of_style="whichever", **common,  # type: ignore[arg-type]
        )  # fmt: skip
    if g["g_amt"]:
        cap = {"cap_amount": _money(g["g_cap"]), "cap_unit": "prescription"} if g["g_cap"] else {}
        return CostTerm(
            kind="greater_of", amount=_money(g["g_amt"]), pct=int(g["g_pct"]), greater_of_style="greater_of",
            **common, **cap,  # type: ignore[arg-type]
        )  # fmt: skip
    if g["atom"]:
        return CostTerm(kind=_ATOMS[g["atom"]], **common)  # type: ignore[arg-type]
    if g["reimb"]:
        return CostTerm(kind="reimbursed_up_to", amount=_money(g["reimb"]), **common)  # type: ignore[arg-type]
    if g["above"]:
        return CostTerm(kind="amount_above_reimbursement", amount=_money(g["above"]), **common)  # type: ignore[arg-type]
    return CostTerm(kind="same_as_in_network", same_as_tier=g["tier"], **common)  # type: ignore[arg-type]


def _step(text: str) -> CostTerm:
    for rule in _FIRST_VISITS_RE:
        m = rule.match(text)
        if m:
            n = int(m["n"]) if m["n"] else 1
            groups = m.groupdict()
            limit = {"first_visits": n, "first_visits_wording": m["phrase"]}
            if "base" not in groups:
                wording = m["nc"] if m["nc"] != "No charge" else None
                return CostTerm(kind="no_charge", wording=wording, **limit)  # type: ignore[arg-type]
            term = _term(m["base"])
            if groups.get("ded"):
                if term.deductible != "not_stated":
                    raise _UnparsedError(f"two deductible clauses in {text!r}")
                term = term.model_copy(update={"deductible": "does_not_apply"})
            return term.model_copy(update=limit)
    return _term(text)


def _channels(text: str) -> tuple[ChannelCost, ...]:
    for rule, channels in _CHANNEL_RES:
        m = rule.match(text)
        if m:
            return tuple(ChannelCost(channel=c, steps=_chain(m[i + 1])) for i, c in enumerate(channels))
    return (ChannelCost(channel="all", steps=_chain(text)),)


def _chain(text: str) -> tuple[CostTerm, ...]:
    return tuple(_step(s) for s in text.split(", then "))


def parse_cost(verbatim: str) -> CostParse:
    if verbatim.strip() == BLANK:
        return CostParse(verbatim=verbatim, status="blank")
    try:
        parts = []
        for chunk in _PART_SPLIT_RE.split(verbatim.strip()):
            m = _PART_LABEL_RE.match(chunk)
            label, body = (m[1], m[2]) if m else (None, chunk)
            parts.append(CostPart(sublabel=label, channels=_channels(canonical(body))))
        parsed = CostParse(verbatim=verbatim, status="exact", parts=tuple(parts))
    except _UnparsedError as exc:
        return CostParse(verbatim=verbatim, status="unparsed", reason=str(exc))
    expected = canonical(verbatim)
    if render(parsed) != expected:
        reason = f"re-render mismatch: {render(parsed)!r}"
        return CostParse(verbatim=verbatim, status="unparsed", reason=reason)
    return parsed


# ---------------------------------------------------------------- re-render


def _fmt(amount: Decimal | None) -> str:
    if amount is None:
        raise ValueError("missing amount")
    return f"{amount:,}"


def render_term(t: CostTerm) -> str:
    if t.kind == "copay":
        s = f"${_fmt(t.amount)}/{t.unit}"
        if t.cap_unit == "admission":
            s += f" to a maximum of ${_fmt(t.cap_amount)}/admission"
        if t.max_days_per_admission is not None:
            s += f" up to {t.max_days_per_admission} days/admission"
    elif t.kind == "copay_up_to_allowed_amount":
        s = f"${_fmt(t.amount)} copayment up to plan's Maximum Allowed Amount"
    elif t.kind == "coinsurance":
        s = f"{t.pct}% coinsurance"
        if t.cap_unit == "prescription":
            s += f" up to ${_fmt(t.cap_amount)}/prescription"
    elif t.kind == "greater_of" and t.greater_of_style == "whichever":
        s = f"${_fmt(t.amount)}/prescription or {t.pct}% coinsurance, whichever is greater"
    elif t.kind == "greater_of":
        s = f"Greater of ${_fmt(t.amount)} or {t.pct}% coinsurance"
        if t.cap_unit == "prescription":
            s += f" up to ${_fmt(t.cap_amount)}/prescription"
    elif t.kind == "same_as_in_network":
        s = "Same as In-Network" + (f" Tier {t.same_as_tier}" if t.same_as_tier else "")
    elif t.kind == "reimbursed_up_to":
        s = f"Reimbursed Up to ${_fmt(t.amount)}"
    elif t.kind == "amount_above_reimbursement":
        s = f"Amount above ${_fmt(t.amount)} Reimbursement"
    else:
        s = t.wording or next(text for text, kind in _ATOMS.items() if kind == t.kind)
    if t.first_visits_wording:
        s += f" {t.first_visits_wording}"
    if t.deductible != "not_stated":
        name = f"{t.deductible_name} " if t.deductible_name else ""
        s += f", {name}deductible " + ("does not apply" if t.deductible == "does_not_apply" else "applies")
    if t.cap_unit == "maximum":
        s += f", up to a ${_fmt(t.cap_amount)} maximum"
    if t.for_physician_services:
        s += " for physician services"
    return s


def _render_channels(channels: tuple[ChannelCost, ...]) -> str:
    def chain(c: ChannelCost) -> str:
        return ", then ".join(render_term(t) for t in c.steps)

    if len(channels) == 2:
        return f"{chain(channels[0])} (retail) and {chain(channels[1])} (home delivery)"
    (only,) = channels
    suffix = {"retail_and_home_delivery": " (retail and home delivery)", "retail_only": " (retail only)"}
    return chain(only) + suffix.get(only.channel, "")


def render(parsed: CostParse) -> str:
    """Canonical text of a parse; equals ``canonical(verbatim)`` for every exact parse."""
    out = []
    for part in parsed.parts:
        body = _render_channels(part.channels)
        out.append(f"{part.sublabel}: {body}" if part.sublabel else body)
    return canonical("; ".join(out))
