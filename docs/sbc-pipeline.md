# SBC ingestion pipeline

How the 2027 Summary of Benefits and Coverage (SBC) corpus is collected, converted to Markdown and verified.
Rebuild commands and the data layout are in [data/README.md](../data/README.md). The code lives in
`packages/sbc_ingest` (`portal`, `to_markdown`, `verify`), exposed as the `sbc-portal`, `sbc-to-markdown` and
`sbc-verify` commands.

## Corpus: 2027 SBCs

Elevance Health plan summary documents (Anthem, Anthem Blue Cross, Anthem BCBS, Anthem HealthKeepers, Empire,
Wellpoint).

| Source | Coverage |
|---|---|
| `sbc.anthem.com/dps` | Anthem in CA, CO, CT, GA, IN, KY, ME, MO, NH, NV, NY, OH, VA, WI: Individual, Small Group, Large Group, MEWA, ABF |
| `sbc.wellpoint.com/dps` | Wellpoint Individual in FL, MD, TX, WA |
| CMS Exchange Plan Attributes PUF | Extra HealthCare.gov SBC links the portal search misses, and HIOS plan IDs |

English only, plan documents effective 01/01/2027.

## Markdown for chunking

`sbc-to-markdown` reads the SBC tables straight from the PDFs (pdfplumber, no OCR) and rebuilds
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
The chunking strategy built on this structure is described in PLAN.md section 3.2.4.

## Verification

`sbc-verify` re-reads every PDF with a second text engine (poppler `pdftotext`) and checks:

| check | what it catches |
|---|---|
| numbers | any number on the PDF's content pages missing from the Markdown, or any number not in the PDF |
| benefits | each provider-tier value and limitation against the PDF cell in the same row and column; each service under the right event |
| questions | answer and "why this matters" for every Important Question |
| examples | every number of each coverage example, in order |
| sections | exact characters of the Excluded/Other Covered lists and each rights/MEC section vs the PDF region (nothing extra, nothing missing, nothing misplaced) |
| lines / words | every PDF line is in the Markdown; every Markdown word is in the PDF |

Results go to `data/markdown/verification_report_<year>.csv` (`PASS` / `REVIEW` / `FAIL`). Only files that
pass, or whose REVIEW reasons are on the signed allowlist (PLAN.md section 3.2.2), are loaded into the index;
a `FAIL` means the Markdown must not be used until fixed.

## Facts bundle and search index

`sbc-build-bundle` extracts every verified Markdown file into one read-only SQLite file (the facts bundle):
plans, Important Questions, benefit rows with parsed costs, list items, coverage examples, and the unique
search chunks with their plan links. Plans that fail extraction go to its `quarantine` table instead. At
runtime the bundle is read through `sbc_core.facts.SqliteFactsReader`.

`sbc-load-qdrant` loads the bundle's unique chunks into a new collection, `{env}__sbc_chunks__{version}`:

- `dense`: OpenRouter embeddings (`models.embeddings` in `config/base.yaml`), cosine, int8-quantized in RAM
  with the full vectors on disk.
- `bm25`: sparse term weights from `sbc_core.bm25`; Qdrant applies IDF.
- Payload: `chunk_hash`, `chunk_type`, `section_path`, `text`, and the `plan_keys`, `plan_years`, `states` and
  `markets` arrays, each indexed.

The `carried_note` and `event_inferred` flags and the page numbers are not in the payload. The same chunk text
can be flagged, or sit on a different page, in one plan and not another, so they are joined in per plan from
the facts bundle (`FactsReader.chunk_context`).

Collections are never changed after loading: the loader refuses to write into an existing one unless
`--recreate` is passed, and it validates the point count and plan-filtered queries before finishing. Without an
embedder the loader builds a BM25-only collection, which the retrieval eval uses in Qdrant's in-memory mode.

At query time (`sbc_retrieval.text_search`) the BM25 query also gets the SBC's own label for each service the
question names, from the service taxonomy: "MRI" adds "Imaging (CT/PET scans, MRIs)". The dense query and the
rerank see the user's text unchanged.

## Plan resolution

`sbc_retrieval.resolver.PlanResolver` decides which plans a turn is about, in code (PLAN.md section 3.4.1):
contract codes, HIOS plan IDs and SBC IDs typed in the question (looked up exactly in the bundle's
`plan_identifier` table), then plans selected in the UI, then name search on the plan names the classifier
found. A name or ID that matches more than one plan comes back `ambiguous` with the candidates for a
clarifying question; it is never guessed. Plans outside the bot's scope can't be resolved, even by exact ID.

## Other years and languages

Use `--year 2028` once next year's documents are posted, `--language Spanish` for Spanish SBCs, and
`--markets Individual "Small Group"` to limit a download.
