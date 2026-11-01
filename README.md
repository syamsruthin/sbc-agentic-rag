# plan-info-chatbot

RAG chatbot over Elevance Health plan summary documents (Anthem, Anthem Blue Cross, Anthem BCBS,
Anthem HealthKeepers, Empire, Wellpoint).

## Corpus: 2027 Summary of Benefits and Coverage (SBC)

| Source | Coverage |
|---|---|
| `sbc.anthem.com/dps` | Anthem in CA, CO, CT, GA, IN, KY, ME, MO, NH, NV, NY, OH, VA, WI: Individual, Small Group, Large Group, MEWA, ABF |
| `sbc.wellpoint.com/dps` | Wellpoint Individual in FL, MD, TX, WA |
| CMS Exchange Plan Attributes PUF | Extra HealthCare.gov SBC links the portal search misses, and HIOS plan IDs |

English only, plan documents effective 01/01/2027.

```
data/
  catalog/sbc_catalog_2027.csv    one row per SBC: brand, state, market, plan, network, contract code, URL, local path
  catalog/sbc_manifest_2027.csv   catalog + downloaded flag, bytes, sha256
  raw/sbc/2027/<portal>/<STATE>/<Market>/<Plan-Name>__<contract>__<sbcId>.pdf
  reference/cms_puf_2027/         CMS plan attributes file (deductibles, MOOP, HIOS IDs)
  markdown/sbc/2027/<portal>/<STATE>/<Market>/<same name>.md    chunk-ready Markdown, one per SBC
  markdown/conversion_report_2027.csv                           per-file status + section counts
```

## Markdown for chunking

`scripts/sbc_to_markdown.py` reads the SBC tables straight from the PDFs (pdfplumber, no OCR) and rebuilds
each document from the CMS SBC template. Body text is verbatim from the PDF; the converter only adds
headings, bold labels and `*Source: SBC page N*` citations.

- Front matter: `plan_name`, `issuer`, `plan_type`, `coverage_period`, `coverage_for`, `hios_plan_ids` are read
  from the PDF. `catalog_*` fields come from the insurer's portal index and are **not** in the PDF (they can
  differ from it, e.g. an older plan name); do not show them to users as SBC facts.
- `## Important Questions`: one `###` per question (deductible, out-of-pocket limit, referrals, ...)
- `## Common Medical Events`: one `###` per event, one `####` per service, one bullet per provider tier
  plus limitations. A note in a box spanning several rows is repeated on each row. When a row's box is blank
  at the top of a page and the note comes from the box ending the previous page, the row says so explicitly.
  `(blank in the SBC)` means the PDF cell is empty; `none` means the PDF prints "--------none--------".
- `## Excluded Services`, `## Other Covered Services`, appeal and continuation rights, MEC / Minimum Value
- `## Coverage Examples`: Peg (baby), Joe (diabetes), Mia (fracture)

Not converted: the multilingual tagline pages and the nondiscrimination notice after the coverage examples.
Split on `##`/`###` headings and prepend the plan name to each chunk.

### Verification

`scripts/verify_markdown.py` re-reads every PDF with a second text engine (poppler `pdftotext`) and checks:

| check | what it catches |
|---|---|
| numbers | any number on the PDF's content pages missing from the Markdown, or any number not in the PDF |
| benefits | each provider-tier value and limitation against the PDF cell in the same row and column; each service under the right event |
| questions | answer and "why this matters" for every Important Question |
| examples | every number of each coverage example, in order |
| sections | exact characters of the Excluded/Other Covered lists and each rights/MEC section vs the PDF region (nothing extra, nothing missing, nothing misplaced) |
| lines / words | every PDF line is in the Markdown; every Markdown word is in the PDF |

Results go to `data/markdown/verification_report_<year>.csv` (`PASS` / `REVIEW` / `FAIL`). Only load files
that pass into the index; a `FAIL` means the Markdown must not be used until fixed.
## Refresh

```bash
uv venv .venv && uv pip install --python .venv/bin/python -r requirements.txt
.venv/bin/python scripts/sbc_portal.py catalog     # ~5 min
.venv/bin/python scripts/sbc_portal.py download    # resumable; skips files already on disk
.venv/bin/python scripts/sbc_portal.py manifest
.venv/bin/python scripts/sbc_to_markdown.py        # skips PDFs already converted; --force to redo
.venv/bin/python scripts/verify_markdown.py        # independent check of every Markdown file
```

Use `--year 2028` once next year's documents are posted, `--language Spanish` for Spanish SBCs, and
`--markets Individual "Small Group"` to limit a download.
