"""Convert downloaded SBC PDFs into clean, chunk-ready Markdown.

Every SBC follows the CMS Summary of Benefits and Coverage template, so the PDF tables are read directly
(pdfplumber, no OCR) and rebuilt section by section:

  - YAML front matter: catalog metadata (brand, state, market, network, contract code, HIOS IDs, URL) plus
    header fields read from the PDF (coverage period, coverage for, plan type)
  - ## Important Questions      one ### per question with the answer and why it matters
  - ## Common Medical Events     one ### per event, one #### per service, one bullet per cost column
  - ## Excluded Services / ## Other Covered Services   bullet lists
  - ## Your Rights to Continue Coverage, ## Grievance and Appeals Rights, ## Minimum Essential Coverage, ...
  - ## Coverage Examples         one ### per example (Peg, Joe, Mia)

The multilingual "Get help in your language" pages and the nondiscrimination notice are identical boilerplate
across every SBC, so they are left out.

Usage:
  python scripts/sbc_to_markdown.py                         # all PDFs in the catalog -> data/markdown/...
  python scripts/sbc_to_markdown.py --limit 20 --force      # re-convert a few
  python scripts/sbc_to_markdown.py path/to/file.pdf ...    # specific files
"""

import argparse
import csv
import re
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import pdfplumber

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
RAW = DATA / "raw"
OUT = DATA / "markdown"

COST_RE = re.compile(r"\$|\d+%|no charge|not covered|covered|deductible|copay|coinsurance|not applicable|n/a", re.I)
PAY_HINT_RE = re.compile(r"\s*\(\s*You will pay[^)]*\)\s*", re.I)
NONE_RE = re.compile(r"^-{2,}\s*none\s*-{2,}$", re.I)
CARRIED_NOTE = "(In the SBC this row's box is blank at the top of a page; this note is from the box that continues from the previous page.)"
URLISH_RE = re.compile(r"(?:https?://|www\.)\S*$|\.(?:com|gov|org|net)/\S*$", re.I)  # line ends inside a URL
NONE_MARK_RE = re.compile(r"-{2,}\s*none\s*-{2,}\s*", re.I)
FOOTER_RES = [
    re.compile(r"^Page \d+ of \d+$"),
    re.compile(r"^\*\s*For more information about limitations and exceptions"),
    re.compile(r"^[A-Z]{2}/[A-Z]+/.+/(?:\d{2}-\d{2})?$"),  # form id, e.g. CA/IND/<plan>/0JF1/01-27
    re.compile(r"^[A-Z]{2}_SBC_\S"),  # form id, e.g. NH_SBC_ANT_BRZ_PXE_HMO_..._96751NH0150015_02
]
NARRATIVE_HEADINGS = [
    ("Your Rights to Continue Coverage:", "Your Rights to Continue Coverage"),
    ("Your Grievance and Appeals Rights:", "Your Grievance and Appeals Rights"),
    ("Does this plan provide Minimum Essential Coverage?", "Does this plan provide Minimum Essential Coverage?"),
    ("Does this plan meet the Minimum Value Standards?", "Does this plan meet the Minimum Value Standards?"),
    ("Language Access Services:", None),  # multilingual taglines; dropped
]
STOP_MARKERS = ("To see examples of how this plan", "About these Coverage Examples", "Get help in your language")


# ---------------------------------------------------------------- text helpers

def join_lines(text):
    """Collapse a PDF cell's hard line breaks into flowing text."""
    if not text:
        return ""
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    out = ""
    for line in lines:
        if not out:
            out = line
        elif (out.endswith("-") and not out.endswith(" -") or out.endswith("/") and not URLISH_RE.search(out)) \
                and line[:1].isalnum():
            out += line  # "out-of-" + "pocket", "Non-" + "IHCP", "screening/" + "immunization"
        else:
            out += " " + line
    return out.replace(" ", " ").replace("", "").replace("", "").strip()


def repair_urls(text, uris):
    """Undo line-wrapping inside URLs using the PDF's own link annotations."""
    for uri in uris:
        bare = re.sub(r"^https?://", "", uri).split("#")[0].rstrip("/")
        if len(bare) < 12 or bare in text:
            continue
        pattern = r"\s?".join(re.escape(ch) for ch in bare)
        text = re.sub(pattern, bare, text)
    return text


def clean(text, uris=()):
    text = join_lines(text)
    text = repair_urls(text, uris)
    text = re.sub(r"\s+([,.;:)])", r"\1", text)
    text = re.sub(r"\(\s+", "(", text)
    return re.sub(r"\s{2,}", " ", text).strip()


def is_footer(line):
    return any(r.search(line) for r in FOOTER_RES)


def yaml_value(v):
    v = str(v)
    if v == "" or re.search(r"[:#\[\]{}&*!|>'\"%@`,]|^\s|\s$|^[-?]", v) or v.lower() in ("yes", "no", "true", "false", "null"):
        return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return v


# ---------------------------------------------------------------- page model

class Doc:
    def __init__(self, path):
        self.pdf = pdfplumber.open(path)
        # Word exports leave 0.5pt "space" glyphs inside other letters (e.g. after a drop cap), which would
        # split "Missouri" into "M issouri". They carry no text, so drop them.
        self.pages = [p.filter(lambda o: not (o.get("object_type") == "char" and o.get("text") == " "
                                              and o.get("size", 12) < 1)) for p in self.pdf.pages]
        self.uris = sorted({h["uri"] for p in self.pdf.pages for h in p.hyperlinks if h.get("uri")}, key=len,
                           reverse=True)
        self.tables = []  # (page_index, pdfplumber Table, rows of text)
        for i, p in enumerate(self.pages):
            found = p.find_tables()
            for t in found:
                # Skip tables nested inside another table's cell (multi-line cost cells sometimes parse as one).
                if any(o is not t and o.bbox[0] <= t.bbox[0] and o.bbox[1] <= t.bbox[1] and o.bbox[2] >= t.bbox[2]
                       and o.bbox[3] >= t.bbox[3] for o in found):
                    continue
                self.tables.append((i, t, t.extract()))

    def c(self, text):
        return clean(text, self.uris)

    def close(self):
        self.pdf.close()


def first_text(row):
    return next((c for c in row if c and c.strip()), "")


# ---------------------------------------------------------------- header

def parse_header(doc):
    """Title block: left column = brand + plan name; right column = coverage period, coverage for, plan type.

    The right column is right-aligned and can wrap ("Plan Type: PPO +" / "HSA"), so words are split into
    columns by the gap between them, never by line text."""
    page = doc.pages[0]
    first_table_top = min((t.bbox[1] for i, t, _ in doc.tables if i == 0), default=page.height)
    stop = next((l["top"] for l in page.extract_text_lines(strip=True)
                 if l["text"].startswith("The Summary of Benefits and Coverage (SBC)")), page.height)
    words = [w for w in page.extract_words() if w["top"] < min(first_table_top, stop) - 1]
    rows = []
    for w in sorted(words, key=lambda w: (round(w["top"]), w["x0"])):
        if rows and abs(rows[-1][0]["top"] - w["top"]) < 3:
            rows[-1].append(w)
        else:
            rows.append([w])
    left, right = [], []
    for row in rows:
        row.sort(key=lambda w: w["x0"])
        segs = [[row[0]]]
        for prev, w in zip(row, row[1:]):
            if w["x0"] - prev["x1"] > 15:
                segs.append([])
            segs[-1].append(w)
        for seg in segs:
            text = " ".join(w["text"] for w in seg)
            (right if seg[0]["x0"] > page.width * 0.5 else left).append(text)
    rtext = " ".join(right)
    info = {}
    m = re.search(r"Coverage Period:\s*([\d/]+\s*-\s*[\d/]+)", rtext)
    if m:
        info["coverage_period"] = m.group(1)
    m = re.search(r"Coverage for:\s*(.*?)\s*(?:\||Plan Type:|$)", rtext)
    if m:
        info["coverage_for"] = m.group(1).strip() or "(blank in the SBC)"
    m = re.search(r"Plan Type:\s*(.+?)\s*$", rtext)
    if m:
        info["plan_type_sbc"] = m.group(1)
    left = [l for l in left if not l.startswith("Summary of Benefits and Coverage")]
    if left:
        info["brand_sbc"] = left[0].replace("®", "").strip()
        info["plan_name_sbc"] = " ".join(left[1:]).strip()
    return info


HIOS_RE = re.compile(r"(?<![0-9A-Za-z])\d{5}[A-Z]{2}\d{7}(?:[-_]\d{2})?(?![0-9A-Za-z])")


def parse_plan_ids(doc):
    """HIOS plan (variant) IDs some SBCs print outside the tables on page 1, e.g. 48396ME0710124_05."""
    ids = []
    for i, page in enumerate(doc.pages[:2]):
        boxes = [t.bbox for j, t, _ in doc.tables if j == i]

        def outside(obj, boxes=boxes):
            cx, cy = (obj["x0"] + obj["x1"]) / 2, (obj["top"] + obj["bottom"]) / 2
            return not any(b[0] <= cx <= b[2] and b[1] <= cy <= b[3] for b in boxes)

        for m in HIOS_RE.findall(page.filter(outside).extract_text() or ""):
            if m not in ids:
                ids.append(m)
    return ids


# ---------------------------------------------------------------- table classification

def table_kind(rows):
    top = re.sub(r"\s+", " ", " ".join((c or "") for c in rows[0])) if rows else ""
    if "Common Medical Event" in top:
        return "cme"
    if top.startswith("Services Your Plan Generally Does NOT Cover") or "Generally Does NOT Cover" in top:
        return "excluded"
    if top.startswith("Other Covered Services"):
        return "other_covered"
    if "Summary of Benefits and Coverage (SBC) document" in top or any(
        (r[0] or "").startswith("Important Questions") for r in rows if r):
        return "iq"
    return None


# ---------------------------------------------------------------- Important Questions

def parse_intro_and_iq(doc):
    intro, questions, chart_note, pages = "", [], "", set()
    started = done = False
    for i, t, rows in doc.tables:
        kind = table_kind(rows)
        if kind == "cme":
            done = True
        if done:
            break
        if kind not in ("iq", None) or (kind is None and not started):
            continue
        started = True
        pages.add(i)
        for r in rows:
            cells = [c or "" for c in r]
            if "Summary of Benefits and Coverage (SBC) document" in cells[0]:
                intro = doc.c(cells[0])
                continue
            if cells[0].startswith("Important Questions"):
                continue
            if len([c for c in cells if c.strip()]) == 0:
                continue
            banner = next((c for c in cells if join_lines(c).startswith("All copayment and coinsurance costs")), None)
            if banner:  # boxed note above the medical events chart
                chart_note = doc.c(banner)
                continue
            q, a, why = (cells + ["", "", ""])[:3]
            if len(cells) > 3:  # occasional split column: fold extras into "why"
                why = " ".join(c for c in cells[2:] if c)
            if (not q.strip() or q.strip()[:1].islower()) and questions:  # row continued from previous page
                questions[-1][0] += "\n" + q
                questions[-1][1] += "\n" + a
                questions[-1][2] += "\n" + why
            else:
                questions.append([q, a, why])
    if not intro:
        text = doc.pages[0].extract_text() or ""
        m = re.search(r"(The Summary of Benefits and Coverage \(SBC\) document.*?)\n\s*Important Questions", text, re.S)
        if m:
            intro = doc.c(m.group(1))
    return intro, [(doc.c(q), doc.c(a), doc.c(w)) for q, a, w in questions], chart_note, pages


# ---------------------------------------------------------------- Common Medical Events

def cme_columns(table, rows):
    """Return [(x0, x1, kind, label)] using header cell positions, so split/merged cells map by x."""
    r0, r1 = table.rows[0].cells, table.rows[1].cells
    cols, cost_span = [], None
    for bbox, text in zip(r0, rows[0]):
        if bbox is None or text is None:
            continue
        t = join_lines(text)
        if t.startswith("Common Medical Event"):
            cols.append((bbox[0], bbox[2], "event", t))
        elif t.startswith("Services You May Need"):
            cols.append((bbox[0], bbox[2], "service", t))
        elif t.startswith("What You Will Pay"):
            cost_span = (bbox[0], bbox[2])
        elif t.startswith("Limitations"):
            cols.append((bbox[0], table.bbox[2] + 1, "limits", "Limitations, Exceptions, & Other Important Information"))
    if cost_span:
        for bbox, text in zip(r1, rows[1]):
            if bbox is None or not text:
                continue
            cx = (bbox[0] + bbox[2]) / 2
            if cost_span[0] <= cx <= cost_span[1]:
                label = PAY_HINT_RE.sub(" ", join_lines(text)).strip()
                hint = PAY_HINT_RE.search(join_lines(text))
                cols.append((bbox[0], bbox[2], "cost", (label, hint.group(0).strip(" ()") if hint else "")))
    return sorted(cols, key=lambda c: c[0])


def assign(cols, bbox):
    cx = (bbox[0] + bbox[2]) / 2
    for c in cols:
        if c[0] - 1 <= cx <= c[1] + 1:
            return c
    return min(cols, key=lambda c: min(abs(c[0] - cx), abs(c[1] - cx)))


def split_labeled(cells_text, labels):
    """'Office Visit\\nNo charge\\nOther Outpatient\\nNo charge' -> 'Office Visit: No charge; Other Outpatient: No charge'."""
    lines = [l.strip() for l in cells_text.split("\n") if l.strip()]
    if not labels or not any(l in labels for l in lines):
        return None
    parts, cur_label, cur = [], None, []
    for l in lines:
        if l in labels:
            if cur_label is not None or cur:
                parts.append((cur_label, cur))
            cur_label, cur = l, []
        else:
            cur.append(l)
    parts.append((cur_label, cur))
    return parts


def parse_cme(doc):
    events = []  # {"name": str, "services": [{"service", "costs": {label: text}, "limits"}]}
    cost_cols = []  # ordered labels seen in headers
    last_cols = None
    for page_i, table, rows in doc.tables:
        if table_kind(rows) != "cme" or len(table.rows) < 2:
            continue
        cols = cme_columns(table, rows)
        if not any(c[2] == "cost" for c in cols):
            cols = last_cols or cols
        last_cols = cols
        for c in cols:
            if c[2] == "cost" and c[3] not in cost_cols:
                cost_cols.append(c[3])
        first_data = True
        for r_i in range(2, len(rows)):
            row = {"event": None, "service": "", "limits": "", "costs": {}}
            event_cell_exists = False
            for bbox, text in zip(table.rows[r_i].cells, rows[r_i]):
                if bbox is None:
                    continue
                col = assign(cols, bbox)
                text = text or ""
                if col[2] == "event":
                    event_cell_exists = True
                    row["event"] = text
                elif col[2] == "service":
                    row["service"] = (row["service"] + "\n" + text).strip()
                elif col[2] == "limits":
                    row["limits"] = (row["limits"] + "\n" + text).strip()
                elif col[2] == "cost":
                    key = col[3]
                    row["costs"][key] = (row["costs"].get(key, "") + "\n" + text).strip()
            limits_merged = all(
                not (assign(cols, b)[2] == "limits") for b in table.rows[r_i].cells if b is not None)
            ev_text = (row["event"] or "").strip()
            has_service = bool(row["service"].strip())

            # Event label handling
            if ev_text:
                continuation = events and (ev_text[:1].islower() or (first_data and not has_service))
                if continuation and first_data:
                    events[-1]["name"] += "\n" + ev_text
                elif events and first_data and events[-1]["name"] == "":
                    events[-1]["name"] = ev_text  # label printed on the page after its first row
                else:
                    events.append({"name": ev_text, "services": [], "pages": set()})
            elif event_cell_exists and not first_data:
                events.append({"name": "", "services": [], "pages": set()})  # new event whose label is on the next page
            elif not events:
                events.append({"name": "", "services": [], "pages": set()})

            ev = events[-1]
            ev["pages"].add(page_i)
            if not has_service:
                # Continuation of the previous service across a page break.
                if ev["services"] or (len(events) > 1 and events[-2]["services"]):
                    target = ev["services"][-1] if ev["services"] else events[-2]["services"][-1]
                    if row["limits"]:
                        # The open limitations cell may span several rows (merged); every row sharing it gets
                        # the continued text, not just the last one.
                        chain = [s for e in events for s in e["services"]]
                        k = len(chain) - 1
                        while k > 0 and chain[k].get("shared_limits"):
                            k -= 1
                        for shared in chain[k:]:
                            shared["limits"] += "\n" + row["limits"]
                    for k, v in row["costs"].items():
                        if v:
                            target["costs"][k] = target["costs"].get(k, "") + "\n" + v
                first_data = False
                continue
            continues_event = first_data and not (ev_text and not ev_text[:1].islower()) and ev["services"]
            carried = False
            if continues_event and not row["limits"].strip():
                limits_merged = carried = True  # merged limitations cell carried over from the previous page
            svc = {"service": row["service"], "costs": row["costs"], "limits": row["limits"],
                   "shared_limits": limits_merged}
            if limits_merged:
                prev = ev["services"][-1] if ev["services"] else None
                svc["limits"] = prev["limits"] if prev else ""
                # The PDF prints this row's cell blank at the top of a page; the note comes from the box that
                # ends the previous page. Say so, since that link is inferred from layout, not printed.
                svc["carried"] = carried or bool(prev and prev.get("carried"))
                svc["limits_from"] = prev.get("limits_from", prev["service"]) if prev else None
            ev["services"].append(svc)
            first_data = False
    return events, cost_cols


def render_cost(text, labels, doc):
    parts = split_labeled(text, labels)
    if not parts:
        return doc.c(text)
    return "; ".join(f"{doc.c(l)}: {doc.c(chr(10).join(v))}" if l else doc.c("\n".join(v)) for l, v in parts)


def render_limits(text, labels, doc):
    parts = split_labeled(text, labels)
    if not parts:
        t = doc.c(text)
    else:
        bits = []
        for l, v in parts:
            v = doc.c("\n".join(v))
            if NONE_RE.match(v):
                v = "none"
            if l:
                bits.append(f"{doc.c(l)}: {v}")
            elif v:
                bits.append(v)
        t = "; ".join(bits)
    if NONE_RE.match(t):
        return ""
    return NONE_MARK_RE.sub("", t).strip()  # some cells print the marker and then real text


def sublabels(costs):
    """Sub-benefit labels inside cost cells, e.g. 'Office Visit' / 'Other Outpatient' or 'Lab – Office'.

    A label is a short capitalised line with no amount in it, directly followed by an amount line."""
    def looks_like_label(l):
        return len(l) < 40 and l[:1].isupper() and not COST_RE.search(l) and any(ch.isalpha() for ch in l)

    labels = set()
    for text in costs.values():
        lines = [l.strip() for l in (text or "").split("\n") if l.strip()]
        for a, b in zip(lines, lines[1:]):
            if looks_like_label(a) and not a.endswith(("-", ":")) and COST_RE.search(b):
                labels.add(a)
    return labels


# ---------------------------------------------------------------- excluded / other covered

def bullet_columns(page, bbox):
    """Read a multi-column bullet cell column by column, so wrapped items stay intact."""
    region = page.crop(bbox)
    bullets = [ch for ch in region.chars if ch["text"] in "•"]
    if not bullets:
        return [l.strip() for l in region.extract_text().split("\n") if l.strip()]
    xs = sorted({round(b["x0"]) for b in bullets})
    starts = []
    for x in xs:
        if not starts or x - starts[-1] > 20:
            starts.append(x)
    items = []
    for k, x0 in enumerate(starts):
        x1 = starts[k + 1] - 1 if k + 1 < len(starts) else bbox[2]
        col = page.crop((max(bbox[0], x0 - 2), bbox[1], x1, bbox[3])).extract_text()
        cur = None
        for line in col.split("\n"):
            line = line.strip()
            if not line:
                continue
            if line[:1] in "•":
                if cur:
                    items.append(cur)
                cur = line[1:].strip()
            elif cur is not None:
                cur = cur + ("" if cur.endswith("-") else " ") + line
        if cur:
            items.append(cur)
    return items


def parse_service_lists(doc):
    out = {"excluded": [], "other_covered": [], "pages": set()}
    last_kind, last_page, last_x, last_bottom = None, None, None, None

    def spill(from_page):
        """Bullets continued at the top of the next page in a box the table finder doesn't detect."""
        nxt = from_page + 1
        if last_kind is None or nxt >= len(doc.pages):
            return
        # Only a box that runs to the bottom of its page can continue; if anything but the footer follows
        # it on that page, the list ended there.
        after = [l for l in doc.pages[from_page].extract_text_lines(strip=True)
                 if l["top"] > last_bottom and not is_footer(l["text"])]
        if after:
            return
        page = doc.pages[nxt]
        tops = [t.bbox[1] for i, t, _ in doc.tables if i == nxt]
        for line in page.extract_text_lines(strip=True):  # the box ends where the next section starts
            if any(strip_heading(line["text"], raw) is not None for raw, _ in NARRATIVE_HEADINGS) or \
                    any(m in line["text"] for m in STOP_MARKERS):
                tops.append(line["top"] - 2)  # stop above the heading line, not on it
                break
        bottom = min(tops) if tops else page.height * 0.4
        region = (last_x[0], 0, last_x[1], bottom)
        crop = page.crop(region)
        bullets = [ch for ch in crop.chars if ch["text"] in "•\uf0b7"]
        first_bullet = min((b["top"] for b in bullets), default=bottom)
        # Text above the first bullet finishes the previous page's last item ("... or the services are
        # approved in advance by Anthem.").
        pre = page.crop((region[0], 0, region[2], max(first_bullet - 1, 0.1))).extract_text() if first_bullet > 1 else ""
        pre = "\n".join(l for l in (pre or "").split("\n") if l.strip() and not is_footer(l.strip()))
        if pre.strip() and out[last_kind]:
            out[last_kind][-1] = doc.c(out[last_kind][-1] + "\n" + pre)
            out["pages"].add(nxt)
        if bullets:
            out[last_kind] += [doc.c(i) for i in bullet_columns(page, region)]
            out["pages"].add(nxt)

    for page_i, table, rows in doc.tables:
        kind = table_kind(rows)
        first_on_page = page_i != last_page
        if first_on_page and last_page is not None and last_kind:
            spill(last_page)
        last_page = page_i
        start = 1
        if kind is None and last_kind and first_on_page and "•" in " ".join(c or "" for r in rows for c in r):
            kind, start = last_kind, 0  # list continued at the top of the next page, without its header row
        if kind not in ("excluded", "other_covered"):
            last_kind = None
            continue
        last_kind, last_x, last_bottom = kind, (table.bbox[0], table.bbox[2]), table.bbox[3]
        page = doc.pages[page_i]
        out["pages"].add(page_i)
        if start:
            out[kind + "_header"] = doc.c(first_text(rows[0]))
        for row_obj, row in list(zip(table.rows, rows))[start:]:
            cells = [b for b in row_obj.cells if b]
            if not cells:
                continue
            x0 = min(b[0] for b in cells); x1 = max(b[2] for b in cells)
            top = min(b[1] for b in cells); bottom = max(b[3] for b in cells)
            out[kind] += [doc.c(i) for i in bullet_columns(page, (x0, top, x1, bottom))]
    if last_kind:
        spill(last_page)
    return out


# ---------------------------------------------------------------- narrative sections

def page_lines_outside_tables(doc, page_i):
    page = doc.pages[page_i]
    boxes = [t.bbox for i, t, _ in doc.tables if i == page_i]

    def outside(obj):
        cx, cy = (obj["x0"] + obj["x1"]) / 2, (obj["top"] + obj["bottom"]) / 2
        return not any(b[0] <= cx <= b[2] and b[1] <= cy <= b[3] for b in boxes)

    return page.filter(outside).extract_text_lines(strip=True)


def strip_heading(text, raw):
    """If text starts with heading `raw` (ignoring stray spaces such as drop caps, 'D oes'), return the rest."""
    want = re.sub(r"\s", "", raw)
    got = 0
    for k, ch in enumerate(text):
        if not ch.isspace():
            if got >= len(want) or ch != want[got]:
                return None
            got += 1
        if got == len(want):
            return text[k + 1:].strip()
    return None


def parse_narrative(doc):
    start_page = None
    for i, t, rows in doc.tables:
        if table_kind(rows) in ("excluded", "other_covered"):
            start_page = i
            break
    if start_page is None:
        return []
    sections, cur, paras, last_bottom = [], None, [], None
    for page_i in range(start_page, len(doc.pages)):
        for line in page_lines_outside_tables(doc, page_i):
            text = line["text"]
            if any(text.startswith(s) or s in text for s in STOP_MARKERS):
                if cur:
                    sections.append(cur)
                return [s for s in sections if s["title"]]
            if is_footer(text) or text.startswith("Excluded Services & Other Covered Services"):
                continue
            heading = next(((title, rest) for raw, title in NARRATIVE_HEADINGS
                            if (rest := strip_heading(text, raw)) is not None), None)
            if heading:
                if cur:
                    sections.append(cur)
                cur = {"title": heading[0], "paras": [[]], "pages": {page_i}}
                rest = heading[1]
                if rest:
                    cur["paras"][-1].append(rest)
                last_bottom = line["bottom"]
                continue
            if cur is None:
                continue
            gap = line["top"] - last_bottom if last_bottom is not None else 0
            if gap > 6 or gap < -50:  # blank line, or new page
                cur["paras"].append([])
            cur["paras"][-1].append(text)
            cur["pages"].add(page_i)
            last_bottom = line["bottom"]
    if cur:
        sections.append(cur)
    return [s for s in sections if s["title"]]


# ---------------------------------------------------------------- coverage examples

EXAMPLE_FIELDS = [
    ("deductible", r"The plan.s overall deductible\s+(.+)$"),
    ("specialist", r"(Specialist (?:copayment|coinsurance))\s+(.+)$"),
    ("hospital", r"(Hospital \(facility\) (?:copayment|coinsurance))\s+(.+)$"),
    ("other", r"(Other (?:copayment|coinsurance))\s+(.+)$"),
    ("total_cost", r"Total Example Cost\*?\s*(\$[\d,]+)"),
    ("deductibles", r"^Deductibles\*?\s*(\$[\d,]+)"),
    ("copayments", r"^Copayments\*?\s*(\$[\d,]+)"),
    ("coinsurance", r"^Coinsurance\*?\s*(\$[\d,]+)"),
    ("limits", r"^Limits or exclusions\*?\s*(\$[\d,]+)"),
    ("total_pay", r"^The total \w+ would pay is\*?\s*(\$[\d,]+)"),
]


def parse_examples(doc):
    for page_i, page in enumerate(doc.pages):
        text = page.extract_text() or ""
        if "About these Coverage Examples" not in text:
            continue
        lines = page.extract_text_lines(strip=True)
        intro_line = next((l for l in lines if l["text"].startswith("About these Coverage Examples")), None)
        totals = sorted((t.bbox for i, t, rows in doc.tables if i == page_i and rows
                         and (rows[0][0] or "").startswith("Total Example Cost")), key=lambda b: b[0])
        title_line = next((l for l in lines if "Having a Baby" in l["text"] or "Type 2 Diabetes" in l["text"]), None)
        if not title_line:
            return None
        # Column boundaries from the "Total Example Cost" boxes, else thirds of the page.
        if len(totals) == 3:
            bounds = [(totals[k][0] - 4, totals[k + 1][0] - 2 if k < 2 else page.width) for k in range(3)]
        else:
            w = page.width / 3
            bounds = [(k * w, (k + 1) * w) for k in range(3)]
        intro = ""
        if intro_line:
            body = page.crop((0, intro_line["bottom"], page.width, title_line["top"] - 2)).extract_text()
            intro = re.sub(r"^About these Coverage Examples:\s*", "", doc.c(body))
            intro = "About these Coverage Examples: " + intro
        examples = []
        for x0, x1 in bounds:
            col = page.crop((max(0, x0), title_line["top"] - 2, min(page.width, x1), page.height - 40))
            clines = [join_lines(l["text"]) for l in col.extract_text_lines(strip=True)]
            clines = [l.replace("", "").replace("", "").replace("■", "").strip() for l in clines]
            ex = {"title": "", "subtitle": "", "services": []}
            k = 0
            title_parts = []
            while k < len(clines) and not clines[k].startswith("("):
                title_parts.append(clines[k]); k += 1
            ex["title"] = " ".join(title_parts)
            sub = []
            while k < len(clines) and not re.match(r"The plan.s overall deductible", clines[k]):
                sub.append(clines[k]); k += 1
            ex["subtitle"] = join_lines("\n".join(sub)).strip("() ")
            in_services = False
            for line in clines[k:]:
                for key, pat in EXAMPLE_FIELDS:
                    m = re.search(pat, line)
                    if m:
                        ex[key] = m.group(m.lastindex).strip()
                        if m.lastindex == 2:
                            ex[key + "_label"] = m.group(1)
                        in_services = False
                        break
                else:
                    if line.startswith("This EXAMPLE event includes"):
                        in_services = True
                        continue
                    if line == "like:":
                        continue
                    if in_services:
                        if ex["services"] and (line[:1].islower() or ex["services"][-1].count("(") > ex["services"][-1].count(")")):
                            ex["services"][-1] += " " + line
                        else:
                            ex["services"].append(line)
                    m = re.match(r"In this example, (\w+) would pay:", line)
                    if m:
                        ex["person"] = m.group(1)
                        in_services = False
            examples.append(ex)
        # Everything printed below the three example columns (notes, "The plan would be responsible ...").
        totals_bottom = max((t.bbox[3] for i, t, rows in doc.tables if i == page_i and rows
                             and (rows[0][0] or "").startswith("The total")), default=None)
        notes = []
        if totals_bottom is not None:
            below = [l for l in lines if l["top"] > totals_bottom + 1 and not is_footer(l["text"])]
            para = []
            for l in below:
                if para and (l["text"].startswith(("Note", "The plan would be responsible", "*"))):
                    notes.append(join_lines("\n".join(para)))
                    para = []
                para.append(l["text"])
            if para:
                notes.append(join_lines("\n".join(para)))
        return {"intro": intro, "examples": examples, "notes": notes, "page": page_i}
    return None


# ---------------------------------------------------------------- render

def render(pdf_path, meta):
    doc = Doc(pdf_path)
    try:
        header = parse_header(doc)
        plan_ids = parse_plan_ids(doc)
        intro, questions, chart_note, iq_pages = parse_intro_and_iq(doc)
        events, cost_cols = parse_cme(doc)
        lists = parse_service_lists(doc)
        narrative = parse_narrative(doc)
        examples = parse_examples(doc)
    finally:
        doc.close()

    # Everything in the body comes from the PDF itself. Portal catalog fields (which can differ from the
    # PDF, e.g. an older plan name) live only in front matter, prefixed catalog_.
    plan = header.get("plan_name_sbc") or meta.get("plan_name", "")
    brand = header.get("brand_sbc", "")
    eoc = re.search(r"(https?://eoc\.\S+?)[.,]?\s", intro + " ")
    fm = {
        "doc_type": "Summary of Benefits and Coverage",
        "plan_name": plan,
        "issuer": brand,
        "plan_type": header.get("plan_type_sbc", ""),
        "coverage_period": header.get("coverage_period", ""),
        "coverage_for": header.get("coverage_for", ""),
        "hios_plan_ids": ", ".join(plan_ids),
        "evidence_of_coverage_url": eoc.group(1) if eoc else "",
        "source_pdf": meta.get("local_path") or str(Path(pdf_path).resolve().relative_to(ROOT)),
        "source_url": meta.get("url", ""),
        "catalog_plan_name": meta.get("plan_name", ""),
        "catalog_brand": meta.get("brand", ""),
        "catalog_state": meta.get("state", ""),
        "catalog_market": meta.get("market_unit", ""),
        "catalog_network": meta.get("network", ""),
        "catalog_exchange": meta.get("exchange", ""),
        "catalog_plan_status": meta.get("plan_status", ""),
        "catalog_contract_code": meta.get("contract_code", ""),
        "catalog_sbc_id": meta.get("sbc_id", ""),
        "catalog_hios_plan_ids": meta.get("hios_plan_ids", ""),
    }

    def pg(pages):
        pages = sorted(p + 1 for p in pages)
        if not pages:
            return ""
        return f"*Source: SBC page {pages[0]}*" if len(pages) == 1 else f"*Source: SBC pages {pages[0]}-{pages[-1]}*"

    out = ["---"] + [f"{k}: {yaml_value(v)}" for k, v in fm.items() if v != ""] + ["---", ""]
    out += [f"# {plan}", "", "Summary of Benefits and Coverage: What this Plan Covers & What You Pay for Covered Services", ""]
    facts = [f"**{k}:** {fm[f]}" for k, f in [("Issuer", "issuer"), ("Coverage Period", "coverage_period"),
                                              ("Coverage for", "coverage_for"), ("Plan Type", "plan_type")] if fm[f]]
    out += [" | ".join(facts), ""]
    if plan_ids:
        out += ["Plan IDs printed on this SBC: " + ", ".join(plan_ids), ""]
    out += [pg({0}), ""]
    if intro:
        out += ["## About This Summary", "", intro, "", pg({0}), ""]

    if questions:
        out += ["## Important Questions", "", pg(iq_pages), ""]
        for q, a, why in questions:
            out += [f"### {q}", "", f"**Answer:** {a}", ""]
            if why:
                out += [f"**Why This Matters:** {why}", ""]

    if events:
        out += ["## Common Medical Events: What You Will Pay", ""]
        legend = [f"- **{label}**" + (f" ({hint})" if hint else "") for label, hint in cost_cols]
        if legend:
            out += ["What You Will Pay columns:", ""] + legend + [""]
        if chart_note:
            out += [chart_note, ""]
        for ev in events:
            name = doc.c(ev["name"])
            more = ""
            m = re.match(r"(.*?(?:condition|services|care|attention|clinic|surgery|stay|test|pregnant|needs))\s+(More information.*)$", name)
            if m:
                name, more = m.group(1), m.group(2)
            out += [f"### {name or 'Other services'}", "", pg(ev["pages"]), ""]
            if more:
                out += [more, ""]
            for svc in ev["services"]:
                labels = sublabels(svc["costs"])
                out += [f"#### {clean(svc['service'])}", ""]
                for label, hint in cost_cols:
                    if (label, hint) in svc["costs"]:
                        value = render_cost(svc["costs"][(label, hint)], labels, doc)
                        out.append(f"- **{label}:** {value if value else '(blank in the SBC)'}")
                lim = render_limits(svc["limits"], labels, doc)
                if lim and svc.get("carried"):
                    lim += " " + CARRIED_NOTE
                if lim:
                    out.append(f"- **Limitations, Exceptions, & Other Important Information:** {lim}")
                elif NONE_MARK_RE.search(svc["limits"] or ""):
                    out.append("- **Limitations, Exceptions, & Other Important Information:** none")
                else:
                    out.append("- **Limitations, Exceptions, & Other Important Information:** (blank in the SBC)")
                out.append("")

    if lists["excluded"]:
        out += ["## Excluded Services", "", lists.get("excluded_header", ""), ""]
        out += [f"- {i}" for i in lists["excluded"]] + ["", pg(lists["pages"]), ""]
    if lists["other_covered"]:
        out += ["## Other Covered Services", "", lists.get("other_covered_header", ""), ""]
        out += [f"- {i}" for i in lists["other_covered"]] + ["", pg(lists["pages"]), ""]

    for sec in narrative:
        out += [f"## {sec['title']}", ""]
        for p in sec["paras"]:
            t = clean("\n".join(p), doc.uris)
            if t:
                out += [t, ""]
        out += [pg(sec["pages"]), ""]

    if examples:
        out += ["## Coverage Examples", "", pg({examples["page"]}), ""]
        if examples["intro"]:
            out += [examples["intro"], ""]
        for ex in examples["examples"]:
            out += [f"### {ex['title']}", ""]
            if ex["subtitle"]:
                out += [f"({ex['subtitle']})", ""]
            rows = [("The plan's overall deductible", "deductible"), ("Specialist", "specialist"),
                    ("Hospital (facility)", "hospital"), ("Other", "other")]
            out += [f"- **{ex.get(k + '_label', lab)}:** {ex[k]}" for lab, k in rows if ex.get(k)]
            if ex["services"]:
                out += ["", "This EXAMPLE event includes services like:", ""]
                out += [f"- {s}" for s in ex["services"]]
            who = ex.get("person", "")
            out += ["", f"- **Total Example Cost:** {ex.get('total_cost', '')}", "",
                    f"In this example, {who} would pay:", "",
                    "Cost Sharing:", "",
                    f"- **Deductibles:** {ex.get('deductibles', '')}",
                    f"- **Copayments:** {ex.get('copayments', '')}",
                    f"- **Coinsurance:** {ex.get('coinsurance', '')}", "",
                    "What isn't covered:", "",
                    f"- **Limits or exclusions:** {ex.get('limits', '')}", "",
                    f"**The total {who} would pay is:** {ex.get('total_pay', '')}", ""]
        for note in examples["notes"]:
            out += [note, ""]

    md = "\n".join(out).rstrip() + "\n"
    stats = {"questions": len(questions), "events": len(events),
             "services": sum(len(e["services"]) for e in events), "excluded": len(lists["excluded"]),
             "other_covered": len(lists["other_covered"]), "narrative": len(narrative),
             "examples": len(examples["examples"]) if examples else 0}
    return md, stats


# ---------------------------------------------------------------- driver

def out_path(pdf_path):
    rel = Path(pdf_path).resolve().relative_to(RAW)
    return (OUT / rel).with_suffix(".md")


def convert_one(pdf_path, meta, force):
    dest = out_path(pdf_path)
    if dest.exists() and not force:
        return pdf_path, "skipped", None
    md, stats = render(pdf_path, meta)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(md, encoding="utf-8")
    return pdf_path, "ok", stats


def load_catalog(year):
    path = DATA / "catalog" / f"sbc_catalog_{year}.csv"
    if not path.exists():
        return {}
    with path.open(newline="", encoding="utf-8") as f:
        return {str((ROOT / r["local_path"]).resolve()): r for r in csv.DictReader(f) if r.get("local_path")}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pdfs", nargs="*", help="specific PDFs; default is every downloaded PDF in the catalog")
    ap.add_argument("--year", default="2027")
    ap.add_argument("--force", action="store_true", help="overwrite existing Markdown")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()

    catalog = load_catalog(args.year)
    if args.pdfs:
        pdfs = [str(Path(p).resolve()) for p in args.pdfs]
    else:
        pdfs = sorted(p for p in catalog if Path(p).exists())
    if args.limit:
        pdfs = pdfs[: args.limit]

    report = OUT / f"conversion_report_{args.year}.csv"
    results, failures = [], 0
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(convert_one, p, catalog.get(p, {}), args.force): p for p in pdfs}
        for n, fut in enumerate(as_completed(futs), 1):
            p = futs[fut]
            try:
                _, status, stats = fut.result()
            except Exception as e:  # keep going; the report lists failures
                status, stats = f"error: {type(e).__name__}: {e}", None
                failures += 1
            results.append((p, status, stats))
            if n % 50 == 0 or n == len(pdfs):
                print(f"{n}/{len(pdfs)} done, {failures} failed", file=sys.stderr)

    if not args.pdfs:
        OUT.mkdir(parents=True, exist_ok=True)
        keys = ["questions", "events", "services", "excluded", "other_covered", "narrative", "examples"]
        with report.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["pdf", "markdown", "status"] + keys)
            for p, status, stats in sorted(results):
                w.writerow([Path(p).relative_to(ROOT), out_path(p).relative_to(ROOT), status]
                           + [(stats or {}).get(k, "") for k in keys])
        print(f"report: {report.relative_to(ROOT)}", file=sys.stderr)


if __name__ == "__main__":
    main()
