# data/

Nothing under `data/` is committed except this file. The SBC PDFs, the CMS reference file, the catalog and
the converted Markdown are fetched or generated locally (about 6.4 GB for plan year 2027).

All sources are public:

- `sbc.anthem.com/dps` and `sbc.wellpoint.com/dps`: the insurers' public SBC search portals
- CMS Exchange Plan Attributes PUF: `download.cms.gov/marketplace-puf/<year>/plan-attributes-puf.zip`

## Rebuild

From the repository root:

```bash
uv sync                        # installs the sbc_ingest package and its CLI commands
uv run sbc-portal catalog      # ~5 min: query the portals, write the catalog
uv run sbc-portal download     # resumable; skips files already on disk
uv run sbc-portal manifest     # sizes and sha256 for every PDF
uv run sbc-snapshot            # counts per portal/state/market -> docs/reviews/corpus-snapshot-<year>.md
uv run sbc-to-markdown         # PDF -> chunk-ready Markdown
uv run sbc-verify              # independent check of every Markdown file (needs poppler)
```

`sbc-snapshot` rewrites a committed file, so `git diff docs/reviews/corpus-snapshot-2027.md` shows which counts
moved since the last run. The 2026-11-01 run listed 9,692 SBCs (6.55 GB), 2 more Anthem Small Group SBCs than
the 9,690 documented in PLAN.md section 1.2.

On a 4-CPU machine the download takes about 25 minutes, conversion about 55 and verification about 55.

Then build the facts bundle, which also holds the search index:

```bash
uv run sbc-build-bundle --version 2027.2                 # -> bundles/facts-2027.2.sqlite (facts + BM25 index)
uv run sbc-grammar-review bundles/facts-2027.2.sqlite    # -> docs/reviews/cost-grammar-review.md
uv run sbc-puf-check bundles/facts-2027.2.sqlite         # -> data/markdown/puf_check_2027.csv
uv run sbc-embed-bundle bundles/facts-2027.2.sqlite      # adds the chunk embeddings to the bundle
```

`sbc-build-bundle` also reads the portal manifest (`data/catalog/sbc_manifest_<year>.csv`, or `--manifest`) into
the bundle's `plan_source` table: each plan's fetch method, the portal's preview-form fields and the PDF's sha256.
The API's PDF proxy needs these for the SBCs the portal serves only through its preview form. A `--release` build
refuses to run without the manifest, or with any plan missing from it. Bundles from `2027.2` on have the table;
`2027.1` predates it, and the API then links those plans to the portal search page as before.

`sbc-embed-bundle` needs `OPENROUTER_API_KEY_BROKER` (see `.env.example`) and uses the embedding model in
`config/base.yaml`. Embeddings are cached in `bundles/embeddings.sqlite` by chunk hash, so a rebuild only
embeds new chunk text. Without this step the bundle still supports BM25-only search.

The retrieval eval and the PDF check of the golden dataset read the bundle (see `evals/README.md`):

```bash
uv run python -m evals.harness.retrieval --bundle bundles/facts-2027.2.sqlite --golden evals/golden/0.2.0 --setup bm25_only
uv run python -m evals.harness.pdf_check --bundle bundles/facts-2027.2.sqlite --golden evals/golden/0.2.0
```

The downloader uses 6 parallel workers by default (`--workers`). Keep it modest and don't re-run it more
often than needed; the portals are public services.

## Layout

```
data/
  catalog/sbc_catalog_<year>.csv     one row per SBC (portal index metadata, URL, local path)
  catalog/sbc_manifest_<year>.csv    catalog + downloaded flag, bytes, sha256
  raw/sbc/<year>/<portal>/<STATE>/<Market>/<Plan-Name>__<contract>__<sbcId>.pdf
  reference/cms_puf_<year>/          CMS plan attributes file
  markdown/sbc/<year>/<portal>/<STATE>/<Market>/<same name>.md
  markdown/conversion_report_<year>.csv
  markdown/verification_report_<year>.csv
```
