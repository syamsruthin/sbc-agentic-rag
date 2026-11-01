"""Independently verify every SBC Markdown file against its source PDF.

The converter (sbc_ingest.to_markdown) reads text with pdfplumber. This verifier re-reads each PDF with a
different text engine (poppler's `pdftotext -bbox-layout`) and checks the Markdown against it:

  numbers      every number on the PDF's content pages appears in the Markdown at least as many times, and
               the Markdown contains no number the PDF does not            -> catches dropped/invented values
  words        same check for words                                        -> catches dropped/garbled text
  questions    each Important Question's answer and "why this matters" match the PDF row, in order
  benefits     each service's value in each provider-tier column, and its limitations, match the PDF cell in
               the same row and column (rows matched by order, cells located by the table's ruling lines)
  examples     each coverage example's numbers match the PDF column, in order

Pages after the coverage examples (multilingual taglines, nondiscrimination notice) are intentionally not
converted and not checked.

Usage:
  sbc-verify                 # every converted file -> data/markdown/verification_report_<year>.csv
  sbc-verify file.md ...     # print problems for specific files
"""

import argparse
import collections
import csv
import html
import re
import subprocess
import sys
import unicodedata
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import pdfplumber

from sbc_core.sublabels import KNOWN_SUBLABELS
from sbc_ingest.paths import ROOT

MD_ROOT = ROOT / "data" / "markdown"

NUM_RE = re.compile(r"\d[\d,.]*")
WORD_RE = re.compile(r"[A-Za-z][A-Za-z']*")
NONE_MARK_RE = re.compile(r"-{2,}\s*none\s*-{2,}", re.I)
FOOTER_RES = [
    re.compile(r"^Page \d+ of \d+$"),
    re.compile(r"^\*\s*For more information about limitations and exceptions"),
    re.compile(r"^[A-Z]{2}/[A-Z]+/.+/(?:\d{2}-\d{2})?$"),
    re.compile(r"^[A-Z]{2}_SBC_\S"),
]
# Words the converter adds as structure (headings, labels). Anything else in the Markdown must be in the PDF.
SCAFFOLD_WORDS = {"answer", "source", "sbc", "page", "pages", "blank", "issuer", "columns", "about", "summary",
                  "other", "services", "excluded", "ids", "printed", "on", "covered", "examples", "coverage", "the", "in", "what", "you",
                  "will", "pay", "common", "medical", "events", "important", "questions", "why", "this", "matters"}
# PDF text the converter intentionally leaves out (template chrome, multilingual taglines).
DROPPED_LINE_RES = [
    re.compile(r"^Excluded Services & Other Covered Services:?$"),
    re.compile(r"^To see examples of how this plan might cover costs"),
    re.compile(r"^Common Medical Event$|^Services You May Need$|^What You Will Pay$"),
    re.compile(r"^Limitations, Exceptions, &|^Other Important Information$"),
    re.compile(r"^Important Questions\b|^Answers$|^Why This Matters:?$"),
]


def alnum(text):
    text = NONE_MARK_RE.sub(" ", text or "")
    text = re.sub(r"\bnone\b", " ", text, flags=re.I)
    text = text.replace("(blank in the SBC)", "")
    text = text.replace("(In the SBC this row's box is blank at the top of a page; this note is from the box that "
                        "continues from the previous page.)", "")
    return re.sub(r"[^0-9A-Za-z$%]", "", text).lower()


CARRIED_NOTE = ("(In the SBC this row's box is blank at the top of a page; this note is from the box that "
                "continues from the previous page.)")
SUBLABEL_MARKUP_RE = re.compile(
    r"(?:^|; )(" + "|".join(re.escape(l) for l in sorted(KNOWN_SUBLABELS, key=len, reverse=True)) + r"): ")


def punct(text):
    """Cell text with whitespace and the converter's own markup removed, punctuation kept.

    The only markup the converter may add to a cell is "Label: " / "; Label: " around known sub-labels,
    "none" for the --------none-------- marker, "(blank in the SBC)" and the carried-note sentence. Anything
    else that differs (an inserted ";" or ":") changes what the reader sees, so it is a problem."""
    text = unicodedata.normalize("NFKC", text or "")
    text = text.replace("(blank in the SBC)", "").replace(CARRIED_NOTE, "")
    text = NONE_MARK_RE.sub(" ", text)
    text = SUBLABEL_MARKUP_RE.sub(lambda m: " " + m[1] + " ", text)
    text = re.sub(r"\bnone\b", " ", text, flags=re.I)
    text = re.sub(r"[\u00a0\uf000-\uf8ff]", "", text)
    return re.sub(r"\s+", "", text)


# A multi-word sub-label split by the converter, e.g. "Designated; Specialist: $50/visit". Each half can look
# like valid markup on its own, so this is checked separately.
SPLIT_LABELS = sorted({" ".join(w[:i]) + "; " + " ".join(w[i:]) + ": " for l in KNOWN_SUBLABELS
                       for w in [l.split(" ")] for i in range(1, len(w))})


def split_label(text):
    return next((s for s in SPLIT_LABELS if s in (text or "")), None)


def nums(text):
    return [n.rstrip(".,") for n in NUM_RE.findall(text)]


# ---------------------------------------------------------------- PDF side (poppler words)

def poppler_pages(pdf_path):
    """[[(x0, top, x1, bottom, text, line_id)]] per page, from pdftotext -bbox-layout."""
    out = subprocess.run(["pdftotext", "-bbox-layout", str(pdf_path), "-"], capture_output=True, text=True,
                         check=True).stdout
    pages = []
    for page in re.findall(r"<page [^>]*>(.*?)</page>", out, re.S):
        words = []
        for li, line in enumerate(re.findall(r"<line [^>]*>(.*?)</line>", page, re.S)):
            for m in re.finditer(r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">(.*?)</word>', line):
                words.append((float(m[1]), float(m[2]), float(m[3]), float(m[4]), html.unescape(m[5]), li))
        pages.append(words)
    return pages


def words_in(words, bbox):
    x0, top, x1, bottom = bbox
    return [w for w in words if x0 <= (w[0] + w[2]) / 2 <= x1 and top <= (w[1] + w[3]) / 2 <= bottom]


def text_of(words):
    lines = collections.OrderedDict()
    for w in sorted(words, key=lambda w: (round(w[1]), w[0])):
        lines.setdefault(round(w[1]), []).append(w[4])
    return "\n".join(" ".join(v) for v in lines.values())


def page_lines(words):
    """Poppler lines, split where a wide gap shows the words belong to different table cells."""
    lines = collections.OrderedDict()
    for w in words:
        lines.setdefault(w[5], []).append(w)
    out = []
    for v in lines.values():
        v = sorted(v, key=lambda w: w[0])
        cur = [v[0]]
        for prev, w in zip(v, v[1:]):
            if w[0] - prev[2] > 7:
                out.append(" ".join(x[4] for x in cur))
                cur = []
            cur.append(w)
        out.append(" ".join(x[4] for x in cur))
    return out


# ---------------------------------------------------------------- Markdown side

def md_sections(md):
    body = md.split("\n---\n", 1)[1] if md.startswith("---\n") else md
    body = "\n".join(l for l in body.split("\n") if not re.match(r"^\*Source: SBC pages? [\d-]+\*$", l))
    secs, cur = collections.OrderedDict(), None
    for line in body.split("\n"):
        if line.startswith("## "):
            cur = line[3:].strip()
            secs[cur] = []
        elif cur is not None:
            secs[cur].append(line)
    # The provenance marker is converter wording; leave it out of the whole-document text checks.
    body = body.replace(" (In the SBC this row's box is blank at the top of a page; this note is from the box that "
                        "continues from the previous page.)", "")
    return body, secs


def md_questions(lines):
    qs, cur = [], None
    for l in lines:
        if l.startswith("### "):
            cur = {"q": l[4:], "a": "", "why": ""}
            qs.append(cur)
        elif cur and l.startswith("**Answer:** "):
            cur["a"] = l[len("**Answer:** "):]
        elif cur and l.startswith("**Why This Matters:** "):
            cur["why"] = l[len("**Why This Matters:** "):]
    return qs


def md_services(lines):
    svcs, cur, event, event_extra = [], None, "", ""
    for l in lines:
        if l.startswith("### "):
            event, event_extra = l[4:], ""
        elif l.startswith("More information") and cur is None or (l.startswith("More information") and event_extra == ""
                                                                    and svcs and svcs[-1]["event"] != event):
            event_extra = l
        elif l.startswith("#### "):
            cur = {"service": l[5:], "values": [], "event": event + " " + event_extra}
            svcs.append(cur)
        elif cur and l.startswith("- **"):
            m = re.match(r"- \*\*(.+?):\*\* ?(.*)$", l)
            if m:
                cur["values"].append((m[1], m[2]))
    return svcs


def md_examples(lines):
    exs, cur = [], None
    for l in lines:
        if l.startswith("### "):
            cur = [l[4:]]
            exs.append(cur)
        elif cur is not None:
            cur.append(l)
    return ["\n".join(e) for e in exs]


# ---------------------------------------------------------------- checks

def find_tables(pdf):
    out = []
    for i, page in enumerate(pdf.pages):
        found = page.find_tables()
        for t in found:
            if any(o is not t and o.bbox[0] <= t.bbox[0] and o.bbox[1] <= t.bbox[1] and o.bbox[2] >= t.bbox[2]
                   and o.bbox[3] >= t.bbox[3] for o in found):
                continue
            out.append((i, t))
    return out


def cme_rows(tables, ppages):
    """PDF benefit rows in order: {"service", "costs": [..], "limits", "limits_merged", "page_top"}."""
    rows, last_cols = [], None
    for page_i, t in tables:
        words = ppages[page_i]
        header = text_of(words_in(words, t.rows[0].bbox)) if t.rows else ""
        if "Common" not in header or "Medical" not in header or len(t.rows) < 2:
            continue
        # Column x-ranges from header cells (geometry only).
        cols = []
        for cell in t.rows[0].cells:
            if cell is None:
                continue
            txt = " ".join(text_of(words_in(words, cell)).split())
            if txt.startswith("Common Medical"):
                cols.append(("event", cell[0], cell[2]))
            elif txt.startswith("Services You"):
                cols.append(("service", cell[0], cell[2]))
            elif txt.startswith("Limitations"):
                cols.append(("limits", cell[0], t.bbox[2] + 1))
            elif txt.startswith("What You Will Pay"):
                span = (cell[0], cell[2])
                for c1 in t.rows[1].cells:
                    if c1 and span[0] - 1 <= (c1[0] + c1[2]) / 2 <= span[1] + 1 and text_of(words_in(words, c1)).strip():
                        cols.append(("cost", c1[0], c1[2]))
        if not any(c[0] == "cost" for c in cols):
            cols = last_cols
        last_cols = cols
        cols.sort(key=lambda c: c[1])
        ncost = sum(1 for c in cols if c[0] == "cost")
        ev_col = next((c for c in cols if c[0] == "event"), None)
        ev_cells = [cell for r in t.rows[2:] for cell in r.cells if cell is not None and ev_col
                    and ev_col[1] - 1 <= (cell[0] + cell[2]) / 2 <= ev_col[2] + 1]
        first = True
        for r in t.rows[2:]:
            row = {"service": "", "costs": [""] * ncost, "limits": "", "limits_merged": True, "page_top": first}
            cy = (r.bbox[1] + r.bbox[3]) / 2
            ev_cell = next((c for c in ev_cells if c[1] - 0.5 <= cy <= c[3] + 0.5), None)
            row["event"] = text_of(words_in(words, ev_cell)) if ev_cell else ""
            for cell in r.cells:
                if cell is None:
                    continue
                cx = (cell[0] + cell[2]) / 2
                col = next((c for c in cols if c[1] - 1 <= cx <= c[2] + 1), None)
                if col is None:
                    continue
                txt = text_of(words_in(words, cell))
                if col[0] == "service":
                    row["service"] += "\n" + txt
                elif col[0] == "limits":
                    row["limits"] += "\n" + txt
                    row["limits_merged"] = False
                elif col[0] == "cost":
                    k = [c for c in cols if c[0] == "cost"].index(col)
                    row["costs"][k] += "\n" + txt
            first = False
            if alnum(row["service"]):
                blank_top = row["page_top"] and not row["limits_merged"] and not alnum(row["limits"])
                if (row["limits_merged"] or blank_top) and rows:
                    row["owner"] = rows[-1]["owner"]  # same (possibly page-spanning) limitations cell
                else:
                    row["owner"] = len(rows)
                rows.append(row)
            elif rows:  # continuation of the previous service across a page break
                prev = rows[-1]
                rows[prev["owner"]]["limits"] += "\n" + row["limits"]
                prev["costs"] = [a + "\n" + b for a, b in zip(prev["costs"], row["costs"])]
    return rows


def check_benefits(rows, svcs, problems, review):
    if len(rows) != len(svcs):
        problems.append(f"benefits: PDF has {len(rows)} service rows, Markdown has {len(svcs)}")
        return
    for k, (r, s) in enumerate(zip(rows, svcs)):
        owner = rows[r["owner"]]["limits"]
        prev_owner = owner
        name = s["service"]
        if alnum(r["service"]) != alnum(name):
            problems.append(f"benefits[{k}]: service name PDF={r['service'].strip()!r} MD={name!r}")
            continue
        if alnum(r["event"]) and alnum(r["event"]) not in alnum(s["event"]):
            problems.append(f"benefits[{name}]: event PDF={' '.join(r['event'].split())!r} MD={s['event'].strip()!r}")
        elif not alnum(r["event"]) and not (k and s["event"] == svcs[k - 1]["event"]):
            review.append(f"benefits[{name}]: event label not printed beside this row; MD puts it under "
                          f"{s['event'].strip()!r}")
        md_costs, md_lim = s["values"][:-1], s["values"][-1][1] if s["values"] else ""
        if len(md_costs) != len(r["costs"]):
            problems.append(f"benefits[{name}]: {len(r['costs'])} cost columns in PDF, {len(md_costs)} in MD")
            continue
        for (label, val), pdf_val in zip(md_costs, r["costs"]):
            if alnum(val) != alnum(pdf_val):
                problems.append(f"benefits[{name}][{label}]: PDF={' '.join(pdf_val.split())!r} MD={val!r}")
            elif punct(val) != punct(pdf_val):
                problems.append(f"benefits[{name}][{label}]: punctuation differs from the PDF: "
                                f"PDF={' '.join(pdf_val.split())!r} MD={val!r}")
            elif split_label(val):
                problems.append(f"benefits[{name}][{label}]: sub-label split as {split_label(val)!r}: MD={val!r}")
        pdf_lim = alnum(r["limits"])
        lim_punct = f"benefits[{name}][limitations]: punctuation differs from the PDF: MD={md_lim[:160]!r}"
        if alnum(md_lim) == pdf_lim and not r["limits_merged"]:
            if punct(md_lim) != punct(r["limits"]):
                problems.append(lim_punct)
            continue
        if r["limits_merged"] and alnum(md_lim) == alnum(owner):
            if punct(md_lim) != punct(owner):
                problems.append(lim_punct)
            continue  # cell spans this row and the one(s) above in the PDF
        if r["page_top"] and not pdf_lim and alnum(md_lim) == alnum(prev_owner) and "this note is from the box" in md_lim:
            if punct(md_lim) != punct(prev_owner):
                problems.append(lim_punct)
            review.append(f"benefits[{name}]: limitations carried over from the previous page's cell")
            continue
        problems.append(f"benefits[{name}][limitations]: PDF={' '.join(r['limits'].split())[:160]!r} "
                        f"MD={md_lim[:160]!r}")


def iq_rows(tables, ppages):
    rows = []
    for page_i, t in tables:
        words = ppages[page_i]
        header = text_of(words_in(words, t.rows[0].bbox)) if t.rows else ""
        if "Common" in header and "Medical" in header:
            break
        for r in t.rows:
            cells = [c for c in r.cells if c is not None]
            texts = [text_of(words_in(words, c)) for c in cells]
            joined = " ".join(" ".join(texts).split())
            if len(cells) < 3 or joined.startswith("Important Questions") or joined.startswith("All copayment"):
                continue
            if "Summary of Benefits and Coverage (SBC)" in joined or "Total Example Cost" in joined:
                continue
            q, a, why = texts[0], texts[1], " ".join(texts[2:])
            if not alnum(q) and not alnum(a) and alnum(why).startswith("allcopayment"):
                continue
            if (not alnum(q) or q.strip()[:1].islower()) and rows:
                rows[-1] = [rows[-1][0] + " " + q, rows[-1][1] + " " + a, rows[-1][2] + " " + why]
            elif alnum(q):
                rows.append([q, a, why])
    return rows


def check_questions(rows, qs, problems):
    if len(rows) != len(qs):
        problems.append(f"questions: PDF has {len(rows)}, Markdown has {len(qs)}")
        return
    for (q, a, why), m in zip(rows, qs):
        for field, pdf_v, md_v in (("question", q, m["q"]), ("answer", a, m["a"]), ("why", why, m["why"])):
            if alnum(pdf_v) != alnum(md_v):
                problems.append(f"questions[{m['q'][:50]}][{field}]: PDF={' '.join(pdf_v.split())[:160]!r} "
                                f"MD={md_v[:160]!r}")


def check_examples(pdf, ppages, md_ex, problems):
    page_i = next((i for i, w in enumerate(ppages) if "About these Coverage Examples" in " ".join(page_lines(w))), None)
    if page_i is None:
        if md_ex:
            problems.append("examples: Markdown has examples but PDF has none")
        return None
    words = ppages[page_i]
    titles = [w for w in words if w[4] in ("Peg", "Managing", "Mia’s", "Mia's")]
    title_top = min((w[1] for w in titles), default=0)
    page = pdf.pages[page_i]
    tot = sorted((t.bbox for t in page.find_tables() if (t.extract()[0][0] or "").startswith("Total Example Cost")),
                 key=lambda b: b[0])
    if len(tot) == 3:
        bounds = [(tot[k][0] - 4, tot[k + 1][0] - 2 if k < 2 else page.width) for k in range(3)]
    else:
        bounds = [(k * page.width / 3, (k + 1) * page.width / 3) for k in range(3)]
    if len(md_ex) != 3:
        problems.append(f"examples: Markdown has {len(md_ex)} examples")
        return page_i
    for (x0, x1), md_text in zip(bounds, md_ex):
        col = [w for w in words_in(words, (x0, title_top - 2, x1, page.height - 40))]
        pdf_nums = nums(text_of(col))
        if pdf_nums != nums(md_text):
            problems.append(f"examples[{md_text.split(chr(10))[0]}]: PDF numbers {pdf_nums} MD numbers {nums(md_text)}")
    return page_i


def content_text(ppages, last_page):
    keep = []
    for words in ppages[: last_page + 1]:
        for line in page_lines(words):
            if any(r.search(line) for r in FOOTER_RES):
                continue
            keep.append(line)
    # Multilingual "Language Access Services" taglines (when printed inside the SBC body) are not converted.
    text = "\n".join(keep)
    text = re.sub(r"Language Access Services:.*?(?=\n\S[^\n]*Coverage Examples|\Z)", "", text, flags=re.S)
    return text


NARR_HEADINGS = ["Your Rights to Continue Coverage", "Your Grievance and Appeals Rights",
                 "Does this plan provide Minimum Essential Coverage", "Does this plan meet the Minimum Value Standards"]
SECTION_END = NARR_HEADINGS + ["Language Access Services", "To see examples of how this plan", "About these Coverage Examples"]


def chars(text):
    return collections.Counter(alnum(text))


def check_sections(ppages, secs, problems):
    """Exact character multiset of each list/narrative section vs the PDF text between its boundaries."""
    flat = []  # poppler lines in reading order, footers removed
    for words in ppages:
        flat += [l for l in page_lines(words) if not any(r.search(l) for r in FOOTER_RES)]
    norm = [alnum(l) for l in flat]

    def find(prefix, start=0):
        key = alnum(prefix)
        return next((i for i in range(start, len(flat)) if norm[i].startswith(key)), None)

    # Excluded + Other Covered: everything between the excluded box header and the first narrative heading.
    a = find("Services Your Plan Generally Does NOT Cover")
    if a is not None:
        b = min([i for i in (find(h, a) for h in SECTION_END) if i is not None] or [len(flat)])
        region = flat[a:b]
        pdf_c = chars("\n".join(region))
        md_lines = secs.get("Excluded Services", []) + secs.get("Other Covered Services", [])
        md_c = chars("\n".join(l for l in md_lines if l.strip()))
        if pdf_c != md_c:
            problems.append(f"lists: characters differ from the PDF region (extra in MD: {dict(md_c - pdf_c)}, "
                            f"missing from MD: {dict(pdf_c - md_c)})")
    # Narrative sections: heading -> next heading.
    for h in NARR_HEADINGS:
        a = find(h)
        md_key = next((k for k in secs if alnum(k).startswith(alnum(h))), None)
        if a is None and md_key is None:
            continue
        if a is None or md_key is None:
            problems.append(f"section {h!r}: present in {'MD' if a is None else 'PDF'} only")
            continue
        b = min([i for i in (find(x, a + 1) for x in SECTION_END) if i is not None] or [len(flat)])
        pdf_c = chars("\n".join(flat[a:b]))
        pdf_c.subtract(chars(h))
        pdf_c = +pdf_c
        md_c = chars("\n".join(secs[md_key]))
        if pdf_c != md_c:
            problems.append(f"section {h!r}: characters differ (extra in MD: {dict(md_c - pdf_c)}, "
                            f"missing from MD: {dict(pdf_c - md_c)})")


def check_header(ppages, md, problems):
    """Plan name / plan type / coverage fields, exact (punctuation-sensitive), from poppler's column lines."""
    words = ppages[0]
    lines = collections.OrderedDict()
    for w in words:
        lines.setdefault(w[5], []).append(w)
    left, right = [], []
    for v in lines.values():
        v.sort(key=lambda w: w[0])
        text = " ".join(w[4] for w in v)
        if text.startswith("The Summary of Benefits and Coverage (SBC)") or text.startswith("Important Questions"):
            break
        (right if v[0][0] > 396 else left).append(text)
    rtext = " ".join(right)
    fm = dict(re.findall(r"^(\w+): (.*)$", md.split("\n---\n", 1)[0], re.M))
    fm = {k: v.strip().strip('"') for k, v in fm.items()}
    want = {}
    m = re.search(r"Plan Type:\s*(.+?)\s*$", rtext)
    if m:
        want["plan_type"] = m.group(1)
    m = re.search(r"Coverage for:\s*(.*?)\s*(?:\||Plan Type:|$)", rtext)
    if m:
        want["coverage_for"] = m.group(1).strip() or "(blank in the SBC)"
    m = re.search(r"Coverage Period:\s*([\d/]+\s*-\s*[\d/]+)", rtext)
    if m:
        want["coverage_period"] = m.group(1)
    left = [l for l in left if not l.startswith("Summary of Benefits and Coverage")]
    if len(left) >= 2:
        want["plan_name"] = " ".join(left[1:])
    for k, v in want.items():
        got = fm.get(k, "").replace('\\"', '"')
        if " ".join(got.split()) != " ".join(v.split()):
            problems.append(f"header[{k}]: PDF={v!r} MD={got!r}")


def verify(md_path):
    md_path = Path(md_path).resolve()
    md = md_path.read_text(encoding="utf-8")
    src = re.search(r"^source_pdf: (.+)$", md, re.M).group(1).strip().strip('"')
    problems, review = [], []
    ppages = poppler_pages(ROOT / src)
    body, secs = md_sections(md)
    with pdfplumber.open(ROOT / src) as pdf:
        uris = " ".join(h.get("uri") or "" for pg in pdf.pages for h in pg.hyperlinks)
        tables = find_tables(pdf)
        check_questions(iq_rows(tables, ppages), md_questions(secs.get("Important Questions", [])), problems)
        cme_key = next((k for k in secs if k.startswith("Common Medical Events")), None)
        check_benefits(cme_rows(tables, ppages), md_services(secs.get(cme_key, [])), problems, review)
        ex_page = check_examples(pdf, ppages, md_examples(secs.get("Coverage Examples", [])), problems)
    check_sections(ppages, secs, problems)
    check_header(ppages, md, problems)

    last = ex_page if ex_page is not None else len(ppages) - 1
    pdf_text = content_text(ppages, last)
    pdf_text_kept = "\n".join(l for l in pdf_text.split("\n") if not any(r.search(l) for r in DROPPED_LINE_RES))

    # Numbers: nothing dropped, nothing invented.
    pdf_n, md_n = collections.Counter(nums(pdf_text_kept)), collections.Counter(nums(body))
    dropped = pdf_n - md_n
    all_text = "\n".join("\n".join(page_lines(w)) for w in ppages)  # footers too (NH plan IDs live there)
    invented = set(md_n) - set(nums(pdf_text)) - set(nums(all_text))
    for n in sorted(set(md_n)):  # a number wrapped across a line break, e.g. siteCode=604 / 9566964
        while True:
            split = next(((n[:k], n[k:]) for k in range(1, len(n)) if dropped[n[:k]] and dropped[n[k:]]), None)
            if not split:
                break
            dropped -= collections.Counter(split)
            invented.discard(n)
    if dropped:
        problems.append(f"numbers dropped: {dict(dropped)}")
    if invented:
        problems.append(f"numbers not in PDF: {sorted(invented)}")

    # Text: every PDF line appears (in order, ignoring spacing/punctuation) somewhere in the Markdown.
    md_flat = alnum(body)
    missing = [l for l in pdf_text_kept.split("\n") if alnum(l) and alnum(l) not in md_flat
               and not any(r.search(l) for r in DROPPED_LINE_RES)]
    if missing:
        problems.append(f"PDF lines missing from Markdown: {missing[:8]}")
    # Words: every Markdown word exists in the PDF as a word or across a line wrap ("phar"+"macy").
    pdf_flat = alnum(pdf_text.replace("\n", ""))
    # URLs are rebuilt from the PDF's own link targets, so words in those targets count as PDF words.
    pdf_words = {w.lower() for w in WORD_RE.findall((pdf_text + " " + uris).replace("’", "'"))}
    invented_w = sorted({w for w in (x.lower().strip("'") for x in WORD_RE.findall(body.replace("’", "'")))
                         if w not in pdf_words and w not in SCAFFOLD_WORDS and alnum(w) not in pdf_flat})
    if invented_w:
        problems.append(f"words not in PDF: {invented_w}")
    pdf_tokens = set(pdf_text.split())
    joined = sorted({t.strip(".,;:()*") for t in body.split() if re.search(r"\w[-/]\w", t)
                     and t not in pdf_tokens and t.strip(".,;:()*") not in pdf_tokens and not t.startswith("http")})
    if joined:
        review.append("joined across a line break: " + " ".join(joined))
    return str(md_path.relative_to(ROOT)), problems, review


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="*")
    ap.add_argument("--year", default="2027")
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()
    files = args.files or sorted(str(p) for p in (MD_ROOT / "sbc" / args.year).rglob("*.md"))

    results = []
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(verify, f): f for f in files}
        for n, fut in enumerate(as_completed(futs), 1):
            try:
                results.append(fut.result())
            except Exception as e:
                results.append((futs[fut], [f"verifier error: {type(e).__name__}: {e}"], []))
            if n % 100 == 0:
                print(f"{n}/{len(files)}", file=sys.stderr)

    results.sort()
    if args.files:
        for f, problems, review in results:
            print(f"{f}: {'OK' if not problems else f'{len(problems)} problem(s)'}")
            for p in problems:
                print("   ", p)
            for r in review:
                print("    review:", r)
        return
    report = MD_ROOT / f"verification_report_{args.year}.csv"
    with report.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["markdown", "status", "problems", "needs_review"])
        for md, problems, review in results:
            w.writerow([md, "FAIL" if problems else ("REVIEW" if review else "PASS"), " | ".join(problems),
                        " | ".join(review)])
    c = collections.Counter("FAIL" if p else ("REVIEW" if r else "PASS") for _, p, r in results)
    print(f"{len(results)} files: {dict(c)}  report: {report.relative_to(ROOT)}", file=sys.stderr)


if __name__ == "__main__":
    main()
