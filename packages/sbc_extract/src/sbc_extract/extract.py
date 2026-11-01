"""Turn one verified SBC Markdown file into facts-bundle rows and search chunks (PLAN.md section 3.2.3).

Every value kept is the SBC's own text (``*_verbatim``); parsed forms sit beside it. A plan that can't be
extracted safely is returned with ``quarantine_reasons`` instead of rows, and never reaches the index.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

from sbc_core.taxonomy import TaxonomyError, load_taxonomy
from sbc_core.tiers import TierId, column_tier
from sbc_extract.costs import CostParse, parse_cost
from sbc_extract.limits import LimitParse, parse_limit
from sbc_extract.markdown import (
    LIMITATIONS_LABEL,
    MarkdownFormatError,
    ParsedSbc,
    parse_markdown,
)
from sbc_extract.review import ReviewItem, classify, load_allowlist

# Wording the converter adds to a carried-over limitations cell; it is not SBC text.
CARRIED_NOTE = (
    "(In the SBC this row's box is blank at the top of a page; this note is from the box that continues from "
    "the previous page.)"
)
QUESTION_IDS = (
    "overall_deductible",
    "services_before_deductible",
    "other_deductibles",
    "oop_limit",
    "not_in_oop",
    "network_pays_less",
    "specialist_referral",
)
EXAMPLE_IDS = {"Peg": "peg", "Joe": "joe", "Mia": "mia"}
_METAL_RE = re.compile(r"\b(Bronze|Silver|Gold|Platinum|Catastrophic)\b")


@dataclass(frozen=True, slots=True)
class PlanRow:
    plan_key: str
    plan_year: int
    sbc_id: str
    contract_code: str
    plan_name: str
    issuer: str
    plan_type: str
    coverage_period: str
    coverage_for: str
    hios_ids: str
    eoc_url: str
    source_url: str
    source_pdf: str
    catalog: dict[str, str]
    """Portal index fields (state, market, network, ...). Not SBC text; label them as plan-listing data."""
    metal_tier: str | None
    metal_tier_source: str | None
    verification_status: str
    review_classes: tuple[str, ...]
    chart_notes: tuple[str, ...]
    md_sha1: str


@dataclass(frozen=True, slots=True)
class QuestionRow:
    plan_key: str
    question_id: str
    question: str
    answer_verbatim: str
    why_verbatim: str
    pages: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class BenefitRow:
    plan_key: str
    row_order: int
    event_id: str
    event_label: str
    service_id: str
    service_label: str
    kind: str
    drug_tags: tuple[str, ...]
    tier_id: TierId
    tier_label: str
    cost_verbatim: str
    limitations_verbatim: str
    limitations_kind: str
    """"text", "none" (the SBC prints --------none--------) or "blank" (empty cell)."""
    carried_note: bool
    event_inferred: bool
    pages: tuple[int, ...]
    chunk_hash: str


@dataclass(frozen=True, slots=True)
class ListItemRow:
    plan_key: str
    list_id: str
    position: int
    item_verbatim: str
    pages: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class ExampleRow:
    plan_key: str
    example_id: str
    title: str
    subtitle: str
    plan_terms: tuple[tuple[str, str], ...]
    services: tuple[str, ...]
    total_cost: str
    cost_sharing: tuple[tuple[str, str], ...]
    not_covered: tuple[tuple[str, str], ...]
    total_pay: str
    pages: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class Chunk:
    chunk_hash: str
    chunk_type: str
    section_path: str
    text: str


@dataclass(frozen=True, slots=True)
class PlanChunk:
    plan_key: str
    chunk_hash: str
    position: int
    pages: tuple[int, ...]


@dataclass
class Extracted:
    plan: PlanRow | None = None
    questions: list[QuestionRow] = field(default_factory=list)
    limits: dict[str, LimitParse] = field(default_factory=dict)
    benefits: list[BenefitRow] = field(default_factory=list)
    costs: dict[str, CostParse] = field(default_factory=dict)
    list_items: list[ListItemRow] = field(default_factory=list)
    examples: list[ExampleRow] = field(default_factory=list)
    narrative: list[tuple[str, str, tuple[int, ...]]] = field(default_factory=list)
    chunks: dict[str, Chunk] = field(default_factory=dict)
    plan_chunks: list[PlanChunk] = field(default_factory=list)
    quarantine_reasons: list[str] = field(default_factory=list)
    review_items: list[ReviewItem] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.plan is not None and not self.quarantine_reasons


def _hash(chunk_type: str, text: str) -> str:
    return hashlib.sha256(f"{chunk_type}\n{text}".encode()).hexdigest()[:20]


def _add_chunk(out: Extracted, chunk_type: str, section_path: str, body: str, pages: tuple[int, ...]) -> str:
    text = f"{section_path}\n{body}"
    h = _hash(chunk_type, text)
    out.chunks.setdefault(h, Chunk(h, chunk_type, section_path, text))
    out.plan_chunks.append(PlanChunk("", h, len(out.plan_chunks), pages))
    return h


def _limitations(raw: str) -> tuple[str, str, bool]:
    carried = raw.endswith(" " + CARRIED_NOTE)
    text = raw.removesuffix(" " + CARRIED_NOTE)
    if text == "none":
        return "none", "none", carried
    if text == "(blank in the SBC)":
        return "", "blank", carried
    return text, "text", carried


def _metal(plan_name: str) -> str | None:
    m = _METAL_RE.search(plan_name)
    return m[1] if m else None


def extract(md_text: str, verification: dict[str, str]) -> Extracted:
    """Rows for one SBC. ``verification`` is its row from verification_report_<year>.csv."""
    out = Extracted()
    try:
        sbc = parse_markdown(md_text)
    except MarkdownFormatError as exc:
        out.quarantine_reasons.append(f"markdown: {exc}")
        return out

    status = verification.get("status", "")
    if status not in ("PASS", "REVIEW"):
        out.quarantine_reasons.append(f"verification status {status or 'missing'}")
    out.review_items = classify(verification.get("needs_review", ""))
    allow = load_allowlist().classes
    for item in out.review_items:
        if item.cls is None or item.cls not in allow:
            out.quarantine_reasons.append(f"review reason not allowlisted: {item.text[:120]}")

    _build(out, sbc, md_text, status)
    if out.plan is not None:
        out.plan_chunks = [
            PlanChunk(out.plan.plan_key, pc.chunk_hash, pc.position, pc.pages) for pc in out.plan_chunks
        ]
        _round_trip(out, md_text)
    return out


def _build(out: Extracted, sbc: ParsedSbc, md_text: str, status: str) -> None:
    fm = sbc.front_matter
    period = fm.get("coverage_period", "")
    year_match = re.search(r"\b(20\d\d)\b", period)
    if not year_match or not fm.get("catalog_sbc_id"):
        out.quarantine_reasons.append("missing coverage period year or sbc id")
        return
    plan_year = int(year_match[1])
    plan_key = f"{plan_year}:{fm['catalog_sbc_id']}"
    metal = _metal(fm.get("plan_name", ""))
    out.plan = PlanRow(
        plan_key=plan_key,
        plan_year=plan_year,
        sbc_id=fm["catalog_sbc_id"],
        contract_code=fm.get("catalog_contract_code", ""),
        plan_name=fm.get("plan_name", ""),
        issuer=fm.get("issuer", ""),
        plan_type=fm.get("plan_type", ""),
        coverage_period=period,
        coverage_for=fm.get("coverage_for", ""),
        hios_ids=fm.get("hios_plan_ids", ""),
        eoc_url=fm.get("evidence_of_coverage_url", ""),
        source_url=fm.get("source_url", ""),
        source_pdf=fm.get("source_pdf", ""),
        catalog={k.removeprefix("catalog_"): v for k, v in fm.items() if k.startswith("catalog_")},
        metal_tier=metal,
        metal_tier_source="plan_name" if metal else None,
        verification_status=status,
        review_classes=tuple(sorted({i.cls for i in out.review_items if i.cls})),
        chart_notes=sbc.chart_notes,
        md_sha1=hashlib.sha1(md_text.encode()).hexdigest(),  # noqa: S324 - content fingerprint, not security
    )
    header = " | ".join(f"{k}: {v}" for k, v in sbc.header.items())
    _add_chunk(out, "header", "Plan header", header, sbc.header_pages)
    _add_chunk(out, "narrative", "About This Summary", sbc.about, sbc.about_pages)

    for qid, q in zip(QUESTION_IDS, sbc.questions, strict=True):  # order is checked by the parser
        out.questions.append(QuestionRow(plan_key, qid, q.question, q.answer, q.why, sbc.question_pages))
        body = f"Answer: {q.answer}\nWhy This Matters: {q.why}"
        _add_chunk(out, "iq", f"Important Questions > {q.question}", body, sbc.question_pages)
    by_id = {r.question_id: r for r in out.questions}
    out.limits["deductible"] = parse_limit(
        "deductible", by_id["overall_deductible"].answer_verbatim, by_id["overall_deductible"].why_verbatim
    )
    out.limits["oop"] = parse_limit(
        "oop", by_id["oop_limit"].answer_verbatim, by_id["oop_limit"].why_verbatim
    )
    for kind, parsed in out.limits.items():
        if parsed.status == "blank":
            out.quarantine_reasons.append(f"{kind} answer is blank")

    _benefits(out, sbc, plan_key)
    for list_id, section in (("excluded", sbc.excluded), ("other_covered", sbc.other_covered)):
        for pos, item in enumerate(section.items):
            out.list_items.append(ListItemRow(plan_key, list_id, pos, item, section.pages))
        title = "Excluded Services" if list_id == "excluded" else "Other Covered Services"
        body = section.header + "\n" + "\n".join(f"- {i}" for i in section.items)
        _add_chunk(out, "list", title, body, section.pages)
    for n in sbc.narrative:
        out.narrative.append((n.title, n.text, n.pages))
        _add_chunk(out, "narrative", n.title, n.text, n.pages)
    for ex in sbc.examples:
        example_id = next(
            (v for k, v in EXAMPLE_IDS.items() if ex.title.startswith(k) or f"{k}’s" in ex.title), None
        )
        if example_id is None:
            out.quarantine_reasons.append(f"unknown coverage example {ex.title!r}")
            continue
        out.examples.append(
            ExampleRow(
                plan_key, example_id, ex.title, ex.subtitle, ex.plan_terms, ex.services, ex.total_cost,
                ex.cost_sharing, ex.not_covered, ex.total_pay, sbc.examples_pages,
            )
        )  # fmt: skip
        lines = [f"({ex.subtitle})"] + [f"{k}: {v}" for k, v in ex.plan_terms]
        lines += [f"- {s}" for s in ex.services] + [f"Total Example Cost: {ex.total_cost}"]
        lines += [f"{k}: {v}" for k, v in (*ex.cost_sharing, *ex.not_covered)]
        lines += [f"{ex.total_pay_label}: {ex.total_pay}"]
        _add_chunk(
            out, "coverage_example", f"Coverage Examples > {ex.title}", "\n".join(lines), sbc.examples_pages
        )


def _benefits(out: Extracted, sbc: ParsedSbc, plan_key: str) -> None:
    taxonomy = load_taxonomy()
    inferred = {(i.service, i.event) for i in out.review_items if i.cls == "event_inferred"}
    try:
        tiers = [column_tier(c.label) for c in sbc.columns]
    except KeyError as exc:
        out.quarantine_reasons.append(str(exc))
        return
    if "unlabeled" in tiers:
        out.quarantine_reasons.append("benefit table has an unlabeled 'Provider' column")
    for order, svc in enumerate(sbc.services):
        try:
            cls = taxonomy.classify_row(svc.event_label, svc.label)
        except TaxonomyError as exc:
            out.quarantine_reasons.append(f"taxonomy: {exc}")
            continue
        lim_text, lim_kind, carried = _limitations(svc.limitations)
        values = "\n".join(f"{label}: {value}" for label, value in svc.values)
        body = f"{values}\n{LIMITATIONS_LABEL}: {lim_text if lim_kind != 'blank' else ''}".rstrip()
        chunk = _add_chunk(out, "service_row", f"{svc.event_label} > {svc.label}", body, svc.pages)
        for (label, value), tier in zip(svc.values, tiers, strict=True):
            out.costs.setdefault(value, parse_cost(value))
            out.benefits.append(
                BenefitRow(
                    plan_key=plan_key, row_order=order, event_id=cls.event_id,
                    event_label=svc.event_label, service_id=cls.service_id, service_label=svc.label,
                    kind=cls.kind, drug_tags=cls.drug_tags, tier_id=tier, tier_label=label,
                    cost_verbatim=value, limitations_verbatim=lim_text, limitations_kind=lim_kind,
                    carried_note=carried, event_inferred=(svc.label, svc.event_label) in inferred,
                    pages=svc.pages, chunk_hash=chunk,
                )
            )  # fmt: skip
    seen = {(b.service_id, b.tier_id) for b in out.benefits}
    if len(seen) != len(out.benefits):
        out.quarantine_reasons.append("duplicate (service_id, tier_id) rows")


def _round_trip(out: Extracted, md_text: str) -> None:
    """Every verbatim value must occur in the Markdown exactly as stored (PLAN.md 3.2.3 check 1)."""
    values = [q.answer_verbatim for q in out.questions] + [q.why_verbatim for q in out.questions]
    values += [b.cost_verbatim for b in out.benefits] + [b.limitations_verbatim for b in out.benefits]
    values += [i.item_verbatim for i in out.list_items] + [e.total_pay for e in out.examples]
    missing = [v for v in values if v and v not in md_text]
    if missing:
        out.quarantine_reasons.append(
            f"round trip: {len(missing)} values not found verbatim, e.g. {missing[0]!r}"
        )
