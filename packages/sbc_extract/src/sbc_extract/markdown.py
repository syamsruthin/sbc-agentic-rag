"""Parse one converted SBC Markdown file into a typed structure.

This module only reads the layout written by ``sbc_ingest.to_markdown``; it does not interpret values. It
is strict: an unexpected section, heading or line raises :class:`MarkdownFormatError`, so a converter change
can't silently drop content from the facts bundle.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import yaml

from sbc_core.template import QUESTIONS as TEMPLATE_QUESTIONS

LIMITATIONS_LABEL = "Limitations, Exceptions, & Other Important Information"
DRUG_NOTE_PREFIX = "More information about prescription drug coverage"
QUESTIONS = tuple(TEMPLATE_QUESTIONS.values())
NARRATIVE_TITLES = (
    "Your Rights to Continue Coverage",
    "Your Grievance and Appeals Rights",
    "Does this plan provide Minimum Essential Coverage?",
    "Does this plan meet the Minimum Value Standards?",
)
SECTIONS = (
    "About This Summary",
    "Important Questions",
    "Common Medical Events: What You Will Pay",
    "Excluded Services",
    "Other Covered Services",
    *NARRATIVE_TITLES,
    "Coverage Examples",
)

_PAGE_RE = re.compile(r"^\*Source: SBC pages? (\d+)(?:-(\d+))?\*$")
_BULLET_RE = re.compile(r"^- \*\*(.+?):\*\* ?(.*)$")
_COLUMN_RE = re.compile(r"^- \*\*(.+?)\*\*(?: \((.+)\))?$")
_HEADER_FIELD_RE = re.compile(r"\*\*(.+?):\*\* ([^|]*?)(?= \| |$)")


class MarkdownFormatError(ValueError):
    """The Markdown does not follow the converter's layout."""


@dataclass(frozen=True)
class Question:
    question: str
    answer: str
    why: str


@dataclass(frozen=True)
class CostColumn:
    label: str
    hint: str


@dataclass(frozen=True)
class Service:
    event_label: str
    event_note: str | None
    label: str
    values: tuple[tuple[str, str], ...]
    """(tier column label, verbatim cell text) in column order."""
    limitations: str
    pages: tuple[int, ...]


@dataclass(frozen=True)
class ListSection:
    header: str
    items: tuple[str, ...]
    pages: tuple[int, ...]


@dataclass(frozen=True)
class Narrative:
    title: str
    text: str
    pages: tuple[int, ...]


@dataclass(frozen=True)
class CoverageExample:
    title: str
    subtitle: str
    plan_terms: tuple[tuple[str, str], ...]
    services: tuple[str, ...]
    total_cost: str
    cost_sharing: tuple[tuple[str, str], ...]
    not_covered: tuple[tuple[str, str], ...]
    total_pay_label: str
    total_pay: str


@dataclass(frozen=True)
class ParsedSbc:
    front_matter: dict[str, str]
    title: str
    header: dict[str, str]
    header_pages: tuple[int, ...]
    about: str
    about_pages: tuple[int, ...]
    questions: tuple[Question, ...]
    question_pages: tuple[int, ...]
    columns: tuple[CostColumn, ...]
    chart_notes: tuple[str, ...]
    services: tuple[Service, ...]
    excluded: ListSection
    other_covered: ListSection
    narrative: tuple[Narrative, ...]
    examples_intro: str
    examples: tuple[CoverageExample, ...]
    examples_pages: tuple[int, ...]
    examples_notes: tuple[str, ...]


def _pages(line: str) -> tuple[int, ...] | None:
    m = _PAGE_RE.match(line)
    if not m:
        return None
    first = int(m[1])
    return tuple(range(first, int(m[2] or first) + 1))


def _split_sections(body: str) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {}
    current = "_preamble"
    sections[current] = []
    for line in body.split("\n"):
        if line.startswith("## "):
            current = line[3:]
            if current in sections:
                raise MarkdownFormatError(f"duplicate section: {current}")
            sections[current] = []
        else:
            sections[current].append(line)
    unexpected = [s for s in sections if s != "_preamble" and s not in SECTIONS]
    missing = [s for s in SECTIONS if s not in sections]
    if unexpected or missing:
        raise MarkdownFormatError(f"sections: unexpected {unexpected}, missing {missing}")
    return sections


def _content(lines: list[str]) -> tuple[list[str], tuple[int, ...]]:
    """Non-blank lines without page markers, and the pages those markers name."""
    out: list[str] = []
    pages: set[int] = set()
    for line in lines:
        found = _pages(line)
        if found is not None:
            pages.update(found)
        elif line.strip():
            out.append(line)
    return out, tuple(sorted(pages))


def _parse_preamble(lines: list[str]) -> tuple[str, dict[str, str], tuple[int, ...]]:
    content, pages = _content(lines)
    titles = [line[2:] for line in content if line.startswith("# ")]
    if len(titles) != 1:
        raise MarkdownFormatError("expected exactly one '# ' title")
    header_line = next((line for line in content if line.startswith("**Issuer:**")), None)
    if header_line is None:
        raise MarkdownFormatError("missing the Issuer / Coverage Period header line")
    header = {m[1]: m[2].strip() for m in _HEADER_FIELD_RE.finditer(header_line)}
    return titles[0], header, pages


def _parse_questions(lines: list[str]) -> tuple[tuple[Question, ...], tuple[int, ...]]:
    content, pages = _content(lines)
    questions: list[Question] = []
    q: str | None = None
    answer = why = ""
    for line in [*content, "### "]:
        if line.startswith("### "):
            if q is not None:
                questions.append(Question(q, answer, why))
            q, answer, why = line[4:], "", ""
        elif line.startswith("**Answer:** "):
            answer = line[len("**Answer:** ") :]
        elif line.startswith("**Why This Matters:** "):
            why = line[len("**Why This Matters:** ") :]
        else:
            raise MarkdownFormatError(f"unexpected line in Important Questions: {line[:80]!r}")
    found = tuple(x.question for x in questions)
    if found != QUESTIONS:
        raise MarkdownFormatError(f"Important Questions differ from the CMS template: {found}")
    return tuple(questions), pages


def _parse_cme(lines: list[str]) -> tuple[tuple[CostColumn, ...], tuple[Service, ...], tuple[str, ...]]:
    columns: list[CostColumn] = []
    chart_notes: list[str] = []
    services: list[Service] = []
    event: str | None = None
    note: str | None = None
    event_pages: tuple[int, ...] = ()
    label: str | None = None
    values: list[tuple[str, str]] = []
    limitations: str | None = None

    def flush() -> None:
        nonlocal label, values, limitations
        if label is None:
            return
        if event is None or limitations is None:
            raise MarkdownFormatError(f"service {label!r} has no event or no limitations line")
        services.append(Service(event, note, label, tuple(values), limitations, event_pages))
        label, values, limitations = None, [], None

    in_columns = False
    for line in lines:
        if not line.strip():
            continue
        if line == "What You Will Pay columns:":
            in_columns = True
        elif in_columns and (m := _COLUMN_RE.match(line)):
            columns.append(CostColumn(m[1], m[2] or ""))
        elif line.startswith("### "):
            flush()
            in_columns = False
            event, note, event_pages = line[4:], None, ()
        elif (found := _pages(line)) is not None:
            event_pages = found
        elif line.startswith(DRUG_NOTE_PREFIX) and label is None:
            note = line
        elif line.startswith("#### "):
            flush()
            label = line[5:]
        elif label is not None and (m := _BULLET_RE.match(line)):
            if m[1] == LIMITATIONS_LABEL:
                limitations = m[2]
            else:
                values.append((m[1], m[2]))
        elif event is None and label is None:
            chart_notes.append(line)  # e.g. "All copayment and coinsurance costs shown in this chart are ..."
            in_columns = False
        else:
            raise MarkdownFormatError(f"unexpected line in Common Medical Events: {line[:80]!r}")
    flush()
    labels = [c.label for c in columns]
    for service in services:
        if [v[0] for v in service.values] != labels:
            raise MarkdownFormatError(f"service {service.label!r}: tier columns differ from the legend")
    return tuple(columns), tuple(services), tuple(chart_notes)


def _parse_list(lines: list[str], name: str) -> ListSection:
    content, pages = _content(lines)
    if not content or content[0].startswith("- "):
        raise MarkdownFormatError(f"{name}: missing the header sentence")
    items = []
    for line in content[1:]:
        if not line.startswith("- "):
            raise MarkdownFormatError(f"{name}: unexpected line {line[:80]!r}")
        items.append(line[2:])
    return ListSection(content[0], tuple(items), pages)


def _parse_narrative(title: str, lines: list[str]) -> Narrative:
    content, pages = _content(lines)
    return Narrative(title, "\n\n".join(content), pages)


def _parse_examples(
    lines: list[str],
) -> tuple[str, tuple[CoverageExample, ...], tuple[int, ...], tuple[str, ...]]:
    content, pages = _content(lines)
    blocks: list[list[str]] = []
    intro: list[str] = []
    notes: list[str] = []
    for line in content:
        if line.startswith("### "):
            blocks.append([line[4:]])
        elif blocks:
            blocks[-1].append(line)
        else:
            intro.append(line)
    examples = []
    for block in blocks:
        title, rest = block[0], block[1:]
        subtitle = ""
        plan_terms: list[tuple[str, str]] = []
        services: list[str] = []
        cost_sharing: list[tuple[str, str]] = []
        not_covered: list[tuple[str, str]] = []
        total_cost = total_pay = total_pay_label = ""
        stage = "plan"
        for line in rest:
            bullet = _BULLET_RE.match(line)
            if line.startswith("(") and line.endswith(")") and not subtitle:
                subtitle = line[1:-1]
            elif line == "This EXAMPLE event includes services like:":
                stage = "services"
            elif bullet and bullet[1] == "Total Example Cost":
                total_cost = bullet[2]
            elif line.startswith("In this example, ") and line.endswith(" would pay:"):
                stage = "paid"
            elif line == "Cost Sharing:":
                stage = "cost_sharing"
            elif line == "What isn't covered:":
                stage = "not_covered"
            elif m := re.match(r"^\*\*(The total .+ would pay is):\*\* (.+)$", line):
                total_pay_label, total_pay = m[1], m[2]
                stage = "done"
            elif bullet and stage == "plan":
                plan_terms.append((bullet[1], bullet[2]))
            elif bullet and stage == "cost_sharing":
                cost_sharing.append((bullet[1], bullet[2]))
            elif bullet and stage == "not_covered":
                not_covered.append((bullet[1], bullet[2]))
            elif line.startswith("- ") and stage == "services":
                services.append(line[2:])
            elif stage == "done":
                notes.append(line)
            else:
                raise MarkdownFormatError(f"example {title!r}: unexpected line {line[:80]!r}")
        if not (total_cost and total_pay and plan_terms and cost_sharing):
            raise MarkdownFormatError(f"example {title!r} is incomplete")
        examples.append(
            CoverageExample(
                title, subtitle, tuple(plan_terms), tuple(services), total_cost,
                tuple(cost_sharing), tuple(not_covered), total_pay_label, total_pay,
            )
        )  # fmt: skip
    if len(examples) != 3:
        raise MarkdownFormatError(f"expected 3 coverage examples, found {len(examples)}")
    return "\n\n".join(intro), tuple(examples), pages, tuple(notes)


def parse_markdown(text: str) -> ParsedSbc:
    if not text.startswith("---\n") or "\n---\n" not in text:
        raise MarkdownFormatError("missing YAML front matter")
    front, body = text[4:].split("\n---\n", 1)
    raw = yaml.load(front, Loader=yaml.BaseLoader)  # noqa: S506 - BaseLoader builds plain strings only
    if not isinstance(raw, dict):
        raise MarkdownFormatError("front matter is not a mapping")
    front_matter = {str(k): str(v) for k, v in raw.items()}
    sections = _split_sections(body)
    title, header, header_pages = _parse_preamble(sections["_preamble"])
    about_lines, about_pages = _content(sections["About This Summary"])
    questions, question_pages = _parse_questions(sections["Important Questions"])
    columns, services, chart_notes = _parse_cme(sections["Common Medical Events: What You Will Pay"])
    intro, examples, examples_pages, notes = _parse_examples(sections["Coverage Examples"])
    return ParsedSbc(
        front_matter=front_matter,
        title=title,
        header=header,
        header_pages=header_pages,
        about="\n\n".join(about_lines),
        about_pages=about_pages,
        questions=questions,
        question_pages=question_pages,
        columns=columns,
        chart_notes=chart_notes,
        services=services,
        excluded=_parse_list(sections["Excluded Services"], "Excluded Services"),
        other_covered=_parse_list(sections["Other Covered Services"], "Other Covered Services"),
        narrative=tuple(_parse_narrative(t, sections[t]) for t in NARRATIVE_TITLES),
        examples_intro=intro,
        examples=examples,
        examples_pages=examples_pages,
        examples_notes=notes,
    )
