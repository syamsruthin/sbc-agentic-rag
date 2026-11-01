"""Independent check of golden values against the SBC PDF (PLAN.md section 6.1).

Golden values are drafted from the facts bundle, which comes from the converter's Markdown (pdfplumber). To
avoid circular gold, this check reads the PDF again with a different engine, poppler (``pdftotext
-bbox-layout``), using only its word coordinates (not pdfplumber's table geometry, which the verifier uses):

- **Benefit values**: the cell in the tier's column that belongs to the service's row must spell the value.
  Columns are split at the gutters between the column headers (carried over to continuation pages without
  a header row). A
  cell is a run of the column's text lines with no vertical gap (poppler sets a cell's lines flush; cells are
  separated by padding). It belongs to the row whose zone holds its center, the zone reaching halfway to
  the neighboring row labels, since SBC tables center cell and label vertically. Lines starting in the first
  column and crossing others (headings, footnotes) are ignored; other crossing lines are split at the column
  edges. Every way this can go wrong makes a match fail, never succeed falsely.
- **Important Questions**: the same, with the question text as the row and the "Answers" column; these
  cells are top-aligned, so a row runs from its question's top to the next question's top.
- **Coverage example lines**: the value must follow its label on the same line, inside that example's panel.

Statuses, strongest first: ``cell_exact`` (same text), ``cell_wrapped`` (same text except where a word wrapped
at a slash or hyphen), ``cell_markup`` (same apart from the converter's own ``Label: `` and ``; `` markup
around sub-labels), ``text_only`` (the text is on the page but its cell couldn't be located), ``not_found``.
A person still verifies every item against the PDF; this check decides where to look hardest.

Usage: ``uv run python -m evals.harness.pdf_check --bundle bundles/facts-2027.1.sqlite --golden DIR``
"""

from __future__ import annotations

import argparse
import itertools
import json
import sqlite3
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from contextlib import closing
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Literal

from evals.harness.golden import GoldenItem, load_golden

Status = Literal["cell_exact", "cell_wrapped", "cell_markup", "text_only", "not_found"]
STRONG: frozenset[Status] = frozenset({"cell_exact", "cell_wrapped", "cell_markup"})
_NS = "{http://www.w3.org/1999/xhtml}"
_IQ_HEADERS = ("Important Questions", "Answers", "Why This Matters:")
_EVENT_HEADER = "Common Medical Event"
_SERVICE_HEADER = "Services You May Need"
_LIMITATIONS_HEADER = "Limitations, Exceptions, &"
_CELL_GAP = 0.25
"""Points of vertical gap that separate two cells; a cell's own lines are set flush (gap 0)."""
_EXAMPLE_TITLES = {
    "peg": "Peg is Having a Baby",
    "joe": "Managing Joe’s Type 2 Diabetes",
    "mia": "Mia’s Simple Fracture",
}


@dataclass(frozen=True)
class Word:
    x0: float
    y0: float
    x1: float
    y1: float
    text: str
    line: int = 0
    """Poppler's text line on the page."""

    @property
    def yc(self) -> float:
        return (self.y0 + self.y1) / 2


def _collapse(text: str) -> str:
    return " ".join(text.split())


@lru_cache(maxsize=64)
def pdf_pages(pdf: Path) -> tuple[tuple[Word, ...], ...]:
    """Words of every page in poppler's reading order."""
    out = subprocess.run(  # noqa: S603 - fixed argv, no shell
        ["pdftotext", "-bbox-layout", str(pdf), "-"],  # noqa: S607 - poppler on PATH, like sbc-verify
        capture_output=True, text=True, check=True, timeout=120,
    )  # fmt: skip
    root = ET.fromstring(out.stdout)  # noqa: S314 - poppler's own XHTML, no DTD or entities
    pages = []
    for page in root.iter(f"{_NS}page"):
        words = [
            Word(float(w.get("xMin", 0)), float(w.get("yMin", 0)), float(w.get("xMax", 0)),
                 float(w.get("yMax", 0)), w.text or "", n)
            for n, line in enumerate(page.iter(f"{_NS}line"))
            for w in line.iter(f"{_NS}word")
        ]  # fmt: skip
        pages.append(tuple(words))
    return tuple(pages)


def find_phrase(words: Sequence[Word], phrase: str, start: int = 0) -> list[tuple[int, int]]:
    """(first, last) word indexes of every run of consecutive words whose characters, ignoring spaces,
    spell ``phrase``. Spaces are ignored so a phrase wrapped at a slash still matches."""
    target = "".join(phrase.split())
    found = []
    for i in range(start, len(words)):
        if not target.startswith(words[i].text):
            continue
        acc = ""
        for j in range(i, len(words)):
            acc += words[j].text
            if acc == target:
                found.append((i, j))
                break
            if not target.startswith(acc):
                break
    return found


def _box(words: Sequence[Word], span: tuple[int, int]) -> Word:
    ws = words[span[0] : span[1] + 1]
    return Word(min(w.x0 for w in ws), min(w.y0 for w in ws), max(w.x1 for w in ws), max(w.y1 for w in ws),
                " ".join(w.text for w in ws))  # fmt: skip


Columns = list[tuple[str, float, float]]


def columns_from_headers(words: Sequence[Word], headers: Sequence[str]) -> Columns | None:
    """Column x-bands for the given header texts; None if a header is missing.

    Between two adjacent header centers the boundary is the middle of the widest run of x positions that the
    fewest words of the table (at or below the header row) cover: the gutter. Lines wider than a third of the
    page (headings, footnotes) don't count.
    """
    centers = []
    header_top = 1e9
    for header in headers:
        spans = find_phrase(words, header)
        if not spans:
            return None
        box = _box(words, spans[0])
        centers.append(((box.x0 + box.x1) / 2, header))
        header_top = min(header_top, box.y0)
    centers.sort()
    page_width = max((w.x1 for w in words), default=0.0)
    by_line: dict[int, list[Word]] = {}
    for w in words:
        by_line.setdefault(w.line, []).append(w)
    coverage = [0] * (int(page_width) + 2)
    for ws in by_line.values():
        if (
            min(w.y0 for w in ws) < header_top - 1
            or max(w.x1 for w in ws) - min(w.x0 for w in ws) > page_width / 3
        ):
            continue
        for w in ws:
            for x in range(int(w.x0), min(int(w.x1) + 1, len(coverage))):
                coverage[x] += 1
    bounds = []
    for (left, _), (right, _) in itertools.pairwise(centers):
        xs = range(int(left), int(right) + 1)
        low = min(coverage[x] for x in xs)
        best: tuple[int, int] | None = None
        run: tuple[int, int] | None = None
        for x in xs:
            if coverage[x] == low:
                run = (run[0], x) if run else (x, x)
                if best is None or run[1] - run[0] > best[1] - best[0]:
                    best = run
            else:
                run = None
        assert best is not None  # noqa: S101 - xs is never empty
        bounds.append((best[0] + best[1]) / 2)
    edges = [0.0, *bounds, 1e9]
    return [(header, edges[k], edges[k + 1]) for k, (_, header) in enumerate(centers)]


@dataclass
class Grid:
    """Column x-bands and the row labels found in the first column of one page.

    ``align`` is how cells sit in their rows: benefit tables center cells vertically, the Important Questions
    table top-aligns them.
    """

    columns: Columns
    rows: list[tuple[str, Word]]
    align: Literal["center", "top"]
    wide_line_tops: list[float]
    """Tops of lines that cross columns (headings, footnotes); a top-aligned last row ends at the next one."""

    @classmethod
    def build(
        cls,
        words: Sequence[Word],
        columns: Columns,
        row_labels: Sequence[str],
        align: Literal["center", "top"] = "center",
        label_column: str | None = None,
    ) -> Grid:
        """``label_column`` names the column holding ``row_labels`` (default: the first column). Labels are
        matched among that column's words only, so a neighboring column's words can't split them."""
        label_col = next((c for c in columns if c[0] == label_column), columns[0])
        in_col = [w for w in words if label_col[1] <= (w.x0 + w.x1) / 2 < label_col[2]]
        rows = []
        for label in dict.fromkeys(row_labels):
            for span in find_phrase(in_col, label):
                rows.append((label, _box(in_col, span)))
        wide = [min(w.y0 for w in ws) for ws in _lines(words).values() if not _within_one(ws, columns)]
        return cls(columns, rows, align, sorted(wide))

    def zones(self) -> list[tuple[float, float]]:
        """Each row's vertical zone.

        Centered: halfway to the neighboring labels' centers, mirrored at the ends. Top-aligned: from the
        label's top to the next label's top; the last row ends at the next wide line, or after 200 points.
        """
        zones = [(0.0, 0.0)] * len(self.rows)
        if self.align == "top":
            tops = [box.y0 - 2 for _, box in self.rows]
            order = sorted(range(len(tops)), key=lambda k: tops[k])
            for n, k in enumerate(order):
                if n + 1 < len(order):
                    bottom = tops[order[n + 1]]
                else:
                    bottom = next((y for y in self.wide_line_tops if y > tops[k] + 4), tops[k] + 200)
                zones[k] = (tops[k], bottom)
            return zones
        centers = [box.yc for _, box in self.rows]
        order = sorted(range(len(centers)), key=lambda k: centers[k])
        for n, k in enumerate(order):
            up = (centers[order[n - 1]] + centers[k]) / 2 if n else None
            down = (centers[k] + centers[order[n + 1]]) / 2 if n + 1 < len(order) else None
            half = (centers[k] - up) if up is not None else (down - centers[k]) if down is not None else 60.0
            top = up if up is not None else centers[k] - half
            bottom = down if down is not None else centers[k] + (centers[k] - top)
            zones[k] = (top, bottom)
        return zones

    def cell(self, words: Sequence[Word], row: str, column: str) -> list[str]:
        """Texts of each candidate cell (a label can occur twice on a page, e.g. "Physician/surgeon fees")."""
        col = next((c for c in self.columns if c[0] == column), None)
        if col is None or not self.rows:
            return []
        # A line wholly inside the column counts. A line that crosses columns counts with its words in this
        # column when it starts right of the first column (poppler joins neighboring cells set on the same
        # baseline); one that starts in the first column is a heading or footnote and is ignored.
        first = self.columns[0]
        inside = []
        for ws in _lines(words).values():
            if _within(ws, col):
                inside.append(ws)
            elif min(w.x0 for w in ws) >= first[2]:
                part = [w for w in ws if col[1] <= (w.x0 + w.x1) / 2 < col[2]]
                if part:
                    inside.append(part)
        ordered = sorted(inside, key=lambda ws: min(w.y0 for w in ws))
        if self.align == "top":
            units = ordered  # each line by its own top
        else:
            units = []
            bottom = -1e9
            for ws in ordered:
                top = min(w.y0 for w in ws)
                if not units or top - bottom > _CELL_GAP:
                    units.append([])
                units[-1].extend(ws)
                bottom = max(bottom, max(w.y1 for w in ws))
        zones = self.zones()
        cells: dict[int, list[Word]] = {}
        for unit in units:
            top, bottom = min(w.y0 for w in unit), max(w.y1 for w in unit)
            y = top if self.align == "top" else (top + bottom) / 2
            for k, (z0, z1) in enumerate(zones):
                if z0 <= y < z1:
                    cells.setdefault(k, []).extend(unit)
        out = []
        for k, (label, _) in enumerate(self.rows):
            if label == row and k in cells:
                ws = sorted(cells[k], key=lambda w: (round(w.y0), w.x0))
                out.append(" ".join(w.text for w in ws))
        return out


def _lines(words: Sequence[Word]) -> dict[int, list[Word]]:
    by_line: dict[int, list[Word]] = {}
    for w in words:
        by_line.setdefault(w.line, []).append(w)
    return by_line


def _within(ws: Sequence[Word], col: tuple[str, float, float]) -> bool:
    return all(col[1] <= (w.x0 + w.x1) / 2 < col[2] for w in ws)


def _within_one(ws: Sequence[Word], columns: Columns) -> bool:
    return any(_within(ws, col) for col in columns)


def compare(found: str, verbatim: str) -> Status | None:
    if _collapse(found) == _collapse(verbatim):
        return "cell_exact"
    if "".join(found.split()) == "".join(verbatim.split()):
        return "cell_wrapped"
    strip = str.maketrans("", "", ":;")
    if "".join(found.split()).translate(strip) == "".join(verbatim.split()).translate(strip):
        return "cell_markup"
    return None


def _best(cells: Sequence[str], verbatim: str) -> Status | None:
    order: tuple[Status, ...] = ("cell_exact", "cell_wrapped", "cell_markup")
    statuses = {s for c in cells if (s := compare(c, verbatim))}
    return next((s for s in order if s in statuses), None)


def _on_page(words: Sequence[Word], verbatim: str) -> bool:
    return bool(find_phrase(words, verbatim))


# ------------------------------------------------------------------------------------------- facts


@dataclass
class ValueCheck:
    item_id: str
    plan_key: str
    fact: str
    verbatim: str
    status: Status
    page: int | None
    pdf: str
    note: str = ""


@dataclass
class PlanContext:
    pdf: Path
    tier_labels: list[str]
    service_rows: dict[str, tuple[str, str, tuple[int, ...]]]
    """service_id -> (event label, service label, pages)."""
    tiers: dict[str, str]
    """tier_id -> column label printed in this SBC."""
    question_pages: tuple[int, ...]
    example_pages: tuple[int, ...]
    page_services: dict[int, list[str]] = field(default_factory=dict)


def plan_context(db: sqlite3.Connection, plan_key: str, root: Path) -> PlanContext | None:
    row = db.execute("SELECT plan_id, source_pdf FROM plan WHERE plan_key = ?", (plan_key,)).fetchone()
    if row is None:
        return None
    plan_id, source_pdf = row
    rows = db.execute(
        """SELECT DISTINCT b.service_id, b.row_order, sl.text, b.pages, tl.text, b.tier_id, ch.section_path
           FROM plan_benefit b JOIN text_string sl ON sl.text_id = b.service_label_id
           JOIN text_string tl ON tl.text_id = b.tier_label_id JOIN chunk ch ON ch.chunk_id = b.chunk_id
           WHERE b.plan_id = ? ORDER BY b.row_order""",
        (plan_id,),
    ).fetchall()
    services: dict[str, tuple[str, str, tuple[int, ...]]] = {}
    tiers: dict[str, str] = {}
    page_services: dict[int, list[str]] = {}
    for service_id, _, label, pages, tier_label, tier_id, path in rows:
        p = tuple(int(x) for x in pages.split(",") if x)
        services[service_id] = (path.split(" > ")[0], label, p)
        tiers[tier_id] = tier_label
        for page in p:
            if label not in page_services.setdefault(page, []):
                page_services[page].append(label)
    q = db.execute("SELECT pages FROM plan_question WHERE plan_id = ? LIMIT 1", (plan_id,)).fetchone()
    e = db.execute("SELECT pages FROM plan_example WHERE plan_id = ? LIMIT 1", (plan_id,)).fetchone()
    return PlanContext(
        pdf=root / source_pdf, tier_labels=list(dict.fromkeys(tiers.values())), service_rows=services,
        tiers=tiers, question_pages=tuple(int(x) for x in (q[0] if q else "").split(",") if x),
        example_pages=tuple(int(x) for x in (e[0] if e else "").split(",") if x),
        page_services=page_services,
    )  # fmt: skip


def check_benefit(
    ctx: PlanContext, service_id: str, tier_id: str, verbatim: str
) -> tuple[Status, int | None]:
    if service_id not in ctx.service_rows or tier_id not in ctx.tiers:
        return "not_found", None
    _, label, pages = ctx.service_rows[service_id]
    words_by_page = pdf_pages(ctx.pdf)
    headers = [_SERVICE_HEADER, *ctx.tier_labels, _LIMITATIONS_HEADER]
    text_page = None
    for page in pages:
        words = words_by_page[page - 1]
        # The event column is split off when its header is printed, so event words never join a label.
        columns = _columns_for(words_by_page, page, [_EVENT_HEADER, *headers]) or _columns_for(
            words_by_page, page, headers
        )
        if columns is not None:
            grid = Grid.build(
                words, columns, ctx.page_services.get(page, [label]), label_column=_SERVICE_HEADER
            )
            status = _best(grid.cell(words, label, ctx.tiers[tier_id]), verbatim)
            if status:
                return status, page
        if text_page is None and _on_page(words, verbatim):
            text_page = page
    return ("text_only", text_page) if text_page else ("not_found", None)


def _columns_for(
    words_by_page: Sequence[Sequence[Word]], page: int, headers: Sequence[str]
) -> Columns | None:
    """The page's own header columns, or those of the nearest earlier page (a continuation page)."""
    for p in range(page, 0, -1):
        columns = columns_from_headers(words_by_page[p - 1], headers)
        if columns is not None:
            return columns
    return None


def check_question(ctx: PlanContext, question: str, verbatim: str) -> tuple[Status, int | None]:
    from sbc_core.template import QUESTIONS

    words_by_page = pdf_pages(ctx.pdf)
    text_page = None
    for page in ctx.question_pages or (1, 2):
        words = words_by_page[page - 1]
        columns = _columns_for(words_by_page, page, _IQ_HEADERS)
        if columns is not None:
            grid = Grid.build(words, columns, list(QUESTIONS.values()), align="top")
            status = _best(grid.cell(words, question, "Answers"), verbatim)
            if status:
                return status, page
        if text_page is None and _on_page(words, verbatim):
            text_page = page
    return ("text_only", text_page) if text_page else ("not_found", None)


def check_example_line(
    ctx: PlanContext, example_id: str, label: str, verbatim: str
) -> tuple[Status, int | None]:
    words_by_page = pdf_pages(ctx.pdf)
    for page in ctx.example_pages:
        words = words_by_page[page - 1]
        titles = {k: find_phrase(words, t) for k, t in _EXAMPLE_TITLES.items()}
        if not all(titles.values()):
            continue
        centers = sorted((((_box(words, s[0]).x0 + _box(words, s[0]).x1) / 2), k) for k, s in titles.items())
        bounds = {}
        for n, (center, key) in enumerate(centers):
            left = (centers[n - 1][0] + center) / 2 if n else 0.0
            right = (center + centers[n + 1][0]) / 2 if n + 1 < len(centers) else 1e9
            bounds[key] = (left, right)
        left, right = bounds[example_id]
        for span in find_phrase(words, label):
            box = _box(words, span)
            if not left <= box.x0 < right:
                continue
            after = [w for w in words[span[1] + 1 :] if abs(w.yc - box.yc) < 3 and box.x1 < w.x0 < right]
            status = compare(" ".join(w.text for w in after), verbatim)
            if status:
                return status, page
    return "not_found", None


def check_item(item: GoldenItem, db: sqlite3.Connection, root: Path) -> list[ValueCheck]:
    out = []
    for value in item.gold.expected_values:
        ctx = plan_context(db, value.plan_key, root)
        if ctx is None or not ctx.pdf.is_file():
            out.append(ValueCheck(item.id, value.plan_key, value.fact, value.verbatim, "not_found", None,
                                  str(ctx.pdf if ctx else ""), "plan or PDF missing"))  # fmt: skip
            continue
        kind, *rest = value.fact.split(":", 2)
        if kind == "benefit":
            status, page = check_benefit(ctx, rest[0], rest[1], value.verbatim)
        elif kind == "question":
            from sbc_core.template import QUESTIONS

            status, page = check_question(ctx, QUESTIONS[rest[0]], value.verbatim)  # type: ignore[index]
        else:
            status, page = check_example_line(ctx, rest[0], rest[1], value.verbatim)
        note = ""
        if page is not None and item.provenance.pdf_pages and page not in item.provenance.pdf_pages:
            note = f"found on page {page}, not in provenance.pdf_pages {list(item.provenance.pdf_pages)}"
        out.append(ValueCheck(item.id, value.plan_key, value.fact, value.verbatim, status, page, str(ctx.pdf),
                              note))  # fmt: skip
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Check golden values against the SBC PDFs with poppler.")
    ap.add_argument("--bundle", type=Path, required=True)
    ap.add_argument("--golden", type=Path, required=True)
    ap.add_argument("--root", type=Path, default=Path.cwd(), help="repository root (PDF paths are relative)")
    ap.add_argument("--out", type=Path, help="write every check as JSON lines")
    args = ap.parse_args(argv)
    items = load_golden(args.golden)
    checks: list[ValueCheck] = []
    with closing(sqlite3.connect(f"{args.bundle.resolve().as_uri()}?mode=ro", uri=True)) as db:
        for item in items:
            checks += check_item(item, db, args.root)
    counts: dict[str, int] = {}
    for c in checks:
        counts[c.status] = counts.get(c.status, 0) + 1
    print(f"{len(checks)} values in {len(items)} items: {dict(sorted(counts.items()))}")
    for c in checks:
        if c.status not in STRONG or c.note:
            print(f"  {c.status:10} {c.item_id} {c.plan_key} {c.fact} {c.verbatim!r} {c.note}")
    if args.out:
        args.out.write_text("".join(json.dumps(c.__dict__) + "\n" for c in checks), encoding="utf-8")
    return 0 if all(c.status in STRONG for c in checks) else 1


if __name__ == "__main__":
    sys.exit(main())
